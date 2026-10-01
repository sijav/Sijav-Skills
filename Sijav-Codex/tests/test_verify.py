"""verify.py: the citation check of a research run, with fixture pages, the fake TypeSafe SDK and no
external network. The fetch itself runs against a loopback server whose test host names resolve to
127.0.0.1 through a patched resolver, or against a patched socket layer that records the address
connected to; nothing leaves the machine."""

import contextlib
import hashlib
import http.server
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

STAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STAGE / "skills" / "research"))
import verify  # noqa: E402

jev = verify.jevlib
_spec = importlib.util.spec_from_file_location("typesafe_sdk", STAGE / "tests" / "fixtures" / "fake_sdk" / "typesafe_sdk.py")
FAKE_SDK = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(FAKE_SDK)
KEY = "ts-fake-key-verify"

PAGE_A = """<html><head><title>Docs</title><script>var hidden = "Retries happen three times";</script></head>
<body><h1>Client</h1><p>The client retries a failed request &ldquo;up to three times&rdquo; before it
gives up.</p><p>Timeouts default to 60&nbsp;seconds.</p><p>Release notes &mdash; version 2 brings many changes
and adds HTTP/2 support for all clients.</p><p>The client API cannot be used from the browser.</p></body></html>"""
PAGE_B = "Plain text page.\nRate limits reset every minute for each key.\nconst limit = 5\n"


def claims():
    return {"claims": [
        {"n": 1, "claim": "The client retries up to three times.", "source": "https://docs.example.com/client",
         "quote": "The client retries a failed request “up to three times” before it gives up."},
        {"n": 2, "claim": "Timeouts default to one minute.", "source": "https://docs.example.com/client",
         "quote": "Timeouts default to 60 seconds."},
        {"n": 3, "claim": "Version 2 adds HTTP/2.", "source": "https://docs.example.com/client#notes",
         "quote": "Release notes — version 2 brings ... adds HTTP/2 support for all"},
        {"n": 4, "claim": "Retries are unlimited.", "source": "https://docs.example.com/client",
         "quote": "The client retries forever."},
        {"n": 5, "claim": "Limits reset each minute.", "source": "https://rates.example.org/limits",
         "quote": "Rate limits reset every minute for each key."},
        {"n": 6, "claim": "The limit is five.", "source": "https://rates.example.org/limits",
         "quote": "const limit = 5"},
        {"n": 7, "claim": "Keys rotate daily.", "source": "https://gone.example.net/keys",
         "quote": "Keys rotate every day at midnight."},
        {"n": 8, "claim": "A claim with no link.", "quote": "nothing"},
        "not an object",
        {"n": 9, "claim": "The API is not secure.", "source": "https://docs.example.com/client",
         "quote": "The ... client ... is ... not ... up"},
        {"n": 10, "claim": "The client API can be used from the browser.", "source": "https://docs.example.com/client",
         "quote": "The client API can ... be used from the browser"},
    ]}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sijav verify "))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "the project"
        (self.project / ".git").mkdir(parents=True)
        key = self.tmp / "ts.key"
        key.write_text(KEY, encoding="utf-8")
        self.log = self.tmp / "ts.jsonl"
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SIJAV_", "FAKE_TS", "TYPESAFE_"))}
        env.update(SIJAV_JEV_KEY_FILE=str(key), FAKE_TS_LOG=str(self.log), FAKE_TS_COUNT=str(self.tmp / "count"),
                   FAKE_TS_NOUL=json.dumps({"k0": 0.9, "k1": 0.5, "k2": 0.2, "k4": 0.8, "k6": 0.95}))
        for patcher in (mock.patch.dict(os.environ, env, clear=True),
                        mock.patch.dict(sys.modules, {"typesafe_sdk": FAKE_SDK})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.fetched = []

        def no_network(url):
            self.fetched.append(url)
            raise verify.PageError("HTTP 404 Not Found")

        p = mock.patch.object(verify, "fetch_text", no_network)
        p.start()
        self.addCleanup(p.stop)

    def snapshots_for(self, run, **override):
        snaps = run / "snapshots"
        snaps.mkdir(exist_ok=True)
        (snaps / "client.html").write_text(PAGE_A, encoding="utf-8")
        (snaps / "limits.txt").write_text(PAGE_B, encoding="utf-8")
        index = {
            "https://docs.example.com/client": {"file": "snapshots/client.html", "sha256": sha(snaps / "client.html"),
                                                "fetched_at": "2026-10-01T10:00Z", "by": "orchestrator web tool"},
            "https://docs.example.com/client#notes": {"file": "snapshots/client.html",
                                                      "sha256": sha(snaps / "client.html"),
                                                      "fetched_at": "2026-10-01T10:00Z", "by": "orchestrator web tool"},
            "https://rates.example.org/limits": {"file": "snapshots/limits.txt", "sha256": sha(snaps / "limits.txt"),
                                                 "fetched_at": "2026-10-01T10:01Z", "by": "orchestrator web tool"}}
        index.update(override)
        f = self.tmp / "snapshots.json"
        f.write_text(json.dumps(index), encoding="utf-8")
        return f

    def main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = verify.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def init(self, depth="standard"):
        code, out, err = self.main("init", "--question", "How does the client retry?", "--depth", depth,
                                   "--project", str(self.project))
        self.assertEqual(code, 0, err)
        return Path(out.strip())

    def claims_file(self, data=None, name="claims.json"):
        p = self.tmp / name
        p.write_text(json.dumps(data or claims()), encoding="utf-8")
        return p

    def check(self, run, *extra, data=None, snapshots=True):
        args = ["check", "--run", str(run), "--claims", str(self.claims_file(data))]
        if snapshots:
            args += ["--snapshots", str(self.snapshots_for(run))]
        return self.main(*args, *extra)

    def calls(self):
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def verdicts(self, run, name="check"):
        return json.loads((run / name / "verdicts.json").read_text(encoding="utf-8"))["verdicts"]


class Init(Base):
    def test_init_records_depth_for_the_native_agents(self):
        for depth, n, rounds in (("quick", 3, 1), ("standard", 5, 2), ("deep", 7, 3)):
            run = self.init(depth)
            self.assertEqual(run.parent, self.project.resolve() / ".codex" / "research")
            data = json.loads((run / "run.json").read_text(encoding="utf-8"))
            self.assertEqual((data["sub_questions"], data["search_rounds"], data["model"], data["effort"]),
                             (n, rounds, "gpt-6-astra", "high"))


class Check(Base):
    def write_findings(self):
        f = self.tmp / "round-1-q1.md"
        f.write_text("## Sources\n- Keys | https://gone.example.net/keys | no date | "
                     "\"Keys rotate every day at midnight.\" (supports: rotation)\n", encoding="utf-8")
        return f

    def test_every_verdict_keeps_its_meaning(self):
        run = self.init()
        code, out, err = self.check(run, "--findings", str(self.write_findings()))
        self.assertEqual(code, 0, err)
        v = {x["i"]: x for x in self.verdicts(run)}
        self.assertEqual(v[0]["verdict"], "supported")            # typography differs; 0.90
        self.assertEqual(v[1]["verdict"], "unclear")              # 0.50
        self.assertEqual(v[2]["verdict"], "not supported")        # a substantial elided quote; 0.20
        self.assertEqual(v[3]["verdict"], "quote not on its page")
        self.assertIsNone(v[3]["jev"], "a quote not on its page is never sent to Jev")
        self.assertEqual(v[4]["verdict"], "supported")            # text snapshot; 0.80
        self.assertEqual(v[5]["verdict"], "on its page; jev not asked")
        self.assertIn("holds code", v[5]["note"])
        self.assertTrue(v[6]["verdict"].startswith("unconfirmed: page not read"))
        self.assertEqual(v[6]["jev"], 0.95)
        self.assertFalse(v[6]["supported"], "an unread page is never supported, whatever Jev says")
        self.assertEqual(v[7]["verdict"], "unconfirmed: the claim cannot be checked")
        self.assertEqual(v[8]["verdict"], "unconfirmed: the claim cannot be checked")
        self.assertEqual(v[9]["verdict"], "unconfirmed: elided quote too fragmentary to check")
        self.assertEqual(v[10]["verdict"], "quote not on its page", "'can' never matches inside 'cannot'")
        self.assertEqual(sum(x["supported"] for x in v.values()), 2)
        self.assertEqual(self.fetched, ["https://gone.example.net/keys"])
        self.assertIn("yes (snapshot by orchestrator web tool, fetched 2026-10-01T10:00Z)", v[0]["quote_on_page"])

        [call] = self.calls()
        self.assertEqual(sorted(call["questions"]), ["k0", "k1", "k2", "k4", "k6"])
        self.assertEqual(call["questions"]["k0"]["instructions"]["passage"],
                         'The client retries a failed request "up to three times" before it gives up.')
        self.assertEqual(call["questions"]["k2"]["instructions"]["passage"],
                         "Release notes - version 2 brings many changes and adds HTTP/2 support for all",
                         "Jev judges the page's own wording, gap included, not the elided quote")
        self.assertEqual(call["questions"]["k0"]["criteria"], verify.JEV_CRITERIA)
        self.assertEqual(jev.code_in(call), [])

    def test_quote_matching_is_strict(self):
        page = ("The API cannot be used from browsers. " + "Filler words here. " * 30 +
                "It is documented on the reference page for every client library.")
        cases = [("The API cannot be used from browsers.", "found"),
                 ("the api CANNOT be used from browsers", "found"),
                 ("The API can be used from browsers.", "absent"),
                 ("The API can ... be used from browsers", "fragmentary"),
                 ("The API cannot be ... documented on the reference page", "absent"),  # pieces 600+ chars apart
                 ("The ... API ... is ... not", "fragmentary"),
                 ("API cannot be use", "absent"),                                      # word boundary
                 ("short", "fragmentary"), ("", "fragmentary")]
        for quote, status in cases:
            self.assertEqual(verify.find_quote(quote, page)["status"], status, quote)
        near = "Release notes - version 2 brings many changes and adds HTTP/2 support for all clients."
        self.assertEqual(verify.find_quote("Release notes - version 2 brings ... adds HTTP/2 support for all", near),
                         {"status": "found", "span": "Release notes - version 2 brings many changes and adds HTTP/2 support for all"})

    def test_a_quote_missing_from_a_truncated_page_stays_unconfirmed(self):
        def big_page(url):
            return ("Rate limits reset every minute for each key. " + "filler " * 50,
                    {"final_url": url, "address": "93.184.216.34", "charset": "utf-8", "truncated": True})

        data = {"claims": [
            {"n": 1, "claim": "Limits reset each minute.", "source": "https://big.example.org/docs",
             "quote": "Rate limits reset every minute for each key."},
            {"n": 2, "claim": "Limits are per account.", "source": "https://big.example.org/docs",
             "quote": "Limits are counted for each account, not each key."}]}
        run = self.init()
        with mock.patch.object(verify, "fetch_text", big_page):
            self.assertEqual(self.check(run, data=data, snapshots=False)[0], 0)
        v = {x["i"]: x for x in self.verdicts(run)}
        self.assertEqual(v[0]["verdict"], "supported", "found within the part that was read (Jev 0.90)")
        self.assertEqual(v[1]["verdict"], "unconfirmed: page truncated before the quote could be found")
        self.assertFalse(v[1]["supported"])

    def test_unread_page_without_findings_is_not_sent(self):
        run = self.init()
        self.assertEqual(self.check(run)[0], 0)
        v = {x["i"]: x for x in self.verdicts(run)}
        self.assertEqual(v[6]["verdict"], "unconfirmed: page not read")
        self.assertNotIn("k6", self.calls()[0]["questions"])

    def test_jev_off_still_checks_pages_and_says_so(self):
        os.environ["SIJAV_JEV"] = "off"
        run = self.init()
        code, out, _ = self.check(run)
        self.assertEqual(code, 0)
        v = {x["i"]: x for x in self.verdicts(run)}
        self.assertEqual(v[0]["verdict"], "on its page; jev not asked")
        self.assertIn("switched off (SIJAV_JEV=off)", v[0]["note"])
        self.assertFalse(any(x["supported"] for x in v.values()))
        self.assertIn("Jev did not judge every quote (unjudged): switched off", out)
        self.assertEqual(self.calls(), [])

    def test_missing_sdk_or_key_is_unjudged(self):
        run = self.init()
        with mock.patch.object(jev, "load_sdk", side_effect=jev.JevUnavailable("the TypeSafe SDK is not installed")):
            self.assertEqual(self.check(run)[0], 0)
        self.assertIn("SDK is not installed", json.loads((run / "check" / "jev.json").read_text())["cause"])
        del os.environ["SIJAV_JEV_KEY_FILE"]
        run = self.init()
        self.assertEqual(self.check(run)[0], 0)
        self.assertIn("no Jev key", json.loads((run / "check" / "jev.json").read_text())["cause"])
        self.assertEqual(self.calls(), [])

    def test_bad_answers_leave_those_claims_unconfirmed(self):
        os.environ["FAKE_TS_MODE"] = "missing"
        run = self.init()
        self.assertEqual(self.check(run)[0], 0)
        v = {x["i"]: x for x in self.verdicts(run)}
        self.assertEqual(v[0]["verdict"], "on its page; jev not asked")
        self.assertIn("no noul answer", v[0]["note"])
        self.assertEqual(v[1]["verdict"], "unclear")

    def test_rerun_resumes_and_refuses_changed_inputs(self):
        run = self.init()
        self.assertEqual(self.check(run)[0], 0)
        first = (run / "check" / "check.md").read_text(encoding="utf-8")
        self.assertEqual(self.check(run)[0], 0)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.fetched, ["https://gone.example.net/keys"], "a failed page is not refetched")
        self.assertEqual((run / "check" / "check.md").read_text(encoding="utf-8"), first)
        for extra, changed in ((["--findings", str(self.write_findings())], "findings_sha256"),
                               (["--no-fetch"], "fetch")):
            code, _o, err = self.check(run, *extra)
            self.assertEqual(code, 2, extra)
            self.assertIn(f"{changed} changed", err)
        code, _o, err = self.check(run, data={"claims": claims()["claims"][:2]})
        self.assertEqual(code, 2)
        self.assertIn("claims_sha256 changed", err)
        os.environ["SIJAV_JEV"] = "off"
        code, _o, err = self.check(run)
        self.assertEqual(code, 2, "Jev's availability is an input too")
        self.assertIn("jev changed", err)
        del os.environ["SIJAV_JEV"]
        self.assertEqual(self.check(run, "--name", "check-2", data={"claims": claims()["claims"][:2]})[0], 0)
        self.assertEqual(len(self.calls()), 2)

    def test_snapshots_need_contained_files_and_provenance(self):
        run = self.init()
        outside = self.tmp / "outside.html"
        outside.write_text(PAGE_A, encoding="utf-8")
        bad = {"outside the run folder": {"https://docs.example.com/client": {
                   "file": str(outside), "sha256": sha(outside), "fetched_at": "t", "by": "x"}},
               "escaping with ..": {"https://docs.example.com/client": {
                   "file": "../outside.html", "sha256": sha(outside), "fetched_at": "t", "by": "x"}},
               "hash mismatch": {"https://docs.example.com/client": {
                   "file": "snapshots/client.html", "sha256": "0" * 64, "fetched_at": "t", "by": "x"}},
               "no provenance": {"https://docs.example.com/client": {"file": "snapshots/client.html"}}}
        for name, override in bad.items():
            with self.subTest(name):
                snaps = self.snapshots_for(run, **override)
                code, _o, err = self.main("check", "--run", str(run), "--claims", str(self.claims_file()),
                                          "--snapshots", str(snaps), "--name", "bad")
                self.assertEqual(code, 2, err)
                self.assertIn("the snapshot index is refused", err)
        self.assertFalse((run / "bad").exists())

    def test_an_interrupted_or_unexpected_jev_call_is_never_asked_again(self):
        run = self.init()
        with mock.patch.object(jev, "call", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.check(run)
        error = json.loads((run / "check" / "jev-1-error.json").read_text(encoding="utf-8"))
        self.assertEqual(error["outcome"], "unknown")
        # as if the process had died between handing over the request and recording anything
        (run / "check" / "jev-1-error.json").unlink()
        code, _o, err = self.check(run)
        self.assertEqual(code, 1)
        self.assertIn("Jev is never asked twice", err)
        self.assertEqual(self.calls(), [])
        code, out, err = self.check(run, "--recover-interrupted")
        self.assertEqual(code, 0, err)
        self.assertIn("recorded by --recover-interrupted", json.loads(
            (run / "check" / "jev-1-error.json").read_text(encoding="utf-8"))["cause"])
        self.assertEqual(self.calls(), [], "recovery records the outcome as unknown; it does not ask")
        self.assertFalse(any(x["supported"] for x in self.verdicts(run)))

        run = self.init()
        with mock.patch.object(jev, "call", side_effect=RuntimeError("socket library error")):
            self.assertEqual(self.check(run)[0], 0)
        error = json.loads((run / "check" / "jev-1-error.json").read_text(encoding="utf-8"))
        self.assertEqual((error["outcome"], error["cause"]), ("unknown", "unexpected RuntimeError: socket library error"))

    def test_a_reply_saved_before_a_crash_is_reused(self):
        run = self.init()
        with mock.patch.object(verify, "step_verdicts", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.check(run)
        (run / "check" / "jev.json").unlink()
        self.assertTrue((run / "check" / "jev-1-response.json").exists())
        self.assertEqual(self.check(run)[0], 0)
        self.assertEqual(len(self.calls()), 1, "the saved reply was used; Jev was not asked twice")

    def test_batches_and_a_failed_later_batch(self):
        os.environ["FAKE_TS_MODE"] = "fail_batch_2"
        os.environ["FAKE_TS_NOUL"] = "{}"
        many = {"claims": [{"n": i, "claim": f"Claim {i}.", "source": "https://rates.example.org/limits",
                            "quote": "Rate limits reset every minute for each key."} for i in range(45)]}
        run = self.init()
        self.assertEqual(self.check(run, data=many)[0], 0)
        self.assertEqual([len(c["questions"]) for c in self.calls()], [40, 5])
        j = json.loads((run / "check" / "jev.json").read_text(encoding="utf-8"))
        self.assertEqual(j["status"], "partial")
        v = self.verdicts(run)
        self.assertEqual(sum(x["verdict"] == "not supported" for x in v), 40)
        self.assertEqual(sum(x["verdict"] == "on its page; jev not asked" for x in v), 5)
        self.assertEqual(json.loads((run / "check" / "jev-2-error.json").read_text())["outcome"], "answered")
        self.assertEqual(self.check(run, data=many)[0], 0)
        self.assertEqual(len(self.calls()), 2, "the failed batch is not retried on resume")

    def test_report_without_a_claim_list_is_unchecked(self):
        run = self.init()
        report = self.tmp / "report.md"
        report.write_text("# Report\n\nThe client retries [1].\n", encoding="utf-8")
        code, out, err = self.main("check", "--run", str(run), "--report", str(report))
        self.assertEqual(code, 2)
        self.assertIn("Not checked", out)

    def test_report_with_its_claim_list_and_status(self):
        run = self.init()
        report = self.tmp / "report.md"
        report.write_text("# Report\n\nThe client retries [1].\n\n```json\n" + json.dumps(
            {"claims": claims()["claims"][:1]}) + "\n```\n", encoding="utf-8")
        code, _out, err = self.main("check", "--run", str(run), "--report", str(report),
                                    "--snapshots", str(self.snapshots_for(run)))
        self.assertEqual(code, 0, err)
        checked = (run / "check" / "report-checked.md").read_text(encoding="utf-8")
        self.assertTrue(checked.startswith("# Report\n\nThe client retries [1]."))
        self.assertNotIn("```json", checked)
        (run / "check" / "pages" / "broken.json").write_text("{not json", encoding="utf-8")
        code, out, err = self.main("status", "--run", str(run))
        self.assertEqual(code, 0, err)
        self.assertIn("jev: judged", out)
        self.assertIn("pages: unreadable", out)


class Units(unittest.TestCase):
    def test_board_then_git_then_claude_folder_decide_the_root(self):
        with tempfile.TemporaryDirectory(prefix="sijav root ") as tmp:
            project = Path(tmp) / "project"
            sub = project / "src" / "deep"
            (sub / ".claude").mkdir(parents=True)
            (project / ".git").mkdir()
            self.assertEqual(verify.project_root(None, sub), project.resolve())
            (project / "src" / ".claude").mkdir()
            (project / "src" / ".claude" / "todo.db").write_bytes(b"")
            self.assertEqual(verify.project_root(None, sub), (project / "src").resolve())

    def test_public_addresses_include_what_ipv6_embeds(self):
        for addr, public in (("93.184.216.34", True), ("127.0.0.1", False), ("10.0.0.8", False),
                             ("169.254.169.254", False), ("::1", False), ("64:ff9b::a00:1", False),
                             ("64:ff9b::808:808", True), ("2002:a00:1::1", False), ("::ffff:10.0.0.1", False),
                             ("fd00::1", False)):
            self.assertEqual(verify.public_ip(addr), public, addr)
        with self.assertRaisesRegex(verify.PageError, "local or private"):
            verify.resolve_public("localhost", 80)


class Fetch(unittest.TestCase):
    """The real fetch code: a loopback server stands in for public hosts named *.test."""

    PAGES = {"/page": (200, "text/html; charset=utf-8", PAGE_A.encode("utf-8")),
             "/nohead": (200, "text/html", b"<html><head><title>T</title><body><p>The quote sits in the body text.</p>"),
             "/bogus": (200, "text/html; charset=x-bogus", b"<p>hello</p>"),
             "/latin": (200, "text/html", "<meta charset=\"iso-8859-1\"><p>Café au lait est servi.</p>".encode("latin-1")),
             "/badutf": (200, "text/plain", b"caf\xe9 in latin-1 bytes"),
             "/big": (200, "text/plain", b"start " + b"x" * 200 + b" end"),
             "/pdf": (200, "application/pdf", b"%PDF-1.7"),
             "/to-private": (302, "text/plain", b""),
             "/loop": (302, "text/plain", b"")}

    def setUp(self):
        pages = self.PAGES

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status, kind, body = pages[self.path]
                self.send_response(status)
                if self.path == "/to-private":
                    self.send_header("Location", "http://private.test/secret")
                if self.path == "/loop":
                    self.send_header("Location", "/loop")
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

        def resolve(host, port):
            if host == "docs.test":
                return "127.0.0.1"
            raise verify.PageError("a local or private address")

        p = mock.patch.object(verify, "resolve_public", resolve)
        p.start()
        self.addCleanup(p.stop)

    def url(self, path):
        return f"http://docs.test:{self.port}{path}"

    def test_reading_and_decoding(self):
        text, info = verify.fetch_text(self.url("/page#frag"))
        self.assertIn("up to three times", text)
        self.assertNotIn("Retries happen three times", text, "scripts are not page text")
        self.assertEqual((info["address"], info["charset"], info["truncated"]), ("127.0.0.1", "utf-8", False))
        self.assertIn("The quote sits in the body text.", verify.fetch_text(self.url("/nohead"))[0],
                      "a missing </head> does not hide the body")
        self.assertIn("Café au lait", verify.fetch_text(self.url("/latin"))[0])
        for path, words in (("/bogus", "unknown charset 'x-bogus'"), ("/badutf", "not valid utf-8"),
                            ("/pdf", "application/pdf page, not text"),
                            ("/to-private", "local or private"), ("/loop", "more than 5 redirects")):
            with self.assertRaisesRegex(verify.PageError, words, msg=path):
                verify.fetch_text(self.url(path))

    def test_truncation_is_recorded(self):
        with mock.patch.object(verify, "PAGE_BYTES", 50):
            text, info = verify.fetch_text(self.url("/big"))
        self.assertTrue(info["truncated"])
        self.assertNotIn("end", text)


class Rebinding(unittest.TestCase):
    def test_the_connection_goes_to_the_checked_address_only(self):
        answers = iter([[(2, 1, 6, "", ("93.184.216.34", 80))], [(2, 1, 6, "", ("127.0.0.1", 80))]])
        connected = []

        def connect(address, timeout=None, *a, **k):
            connected.append(address)
            raise ConnectionRefusedError("test: no network")

        with mock.patch.object(verify.socket, "getaddrinfo", side_effect=lambda *a, **k: next(answers)) as dns, \
                mock.patch.object(verify.socket, "create_connection", side_effect=connect):
            with self.assertRaisesRegex(verify.PageError, "ConnectionRefusedError"):
                verify.fetch_text("http://rebind.test/page")
        self.assertEqual(connected, [("93.184.216.34", 80)], "no second lookup can steer the connection")
        self.assertEqual(dns.call_count, 1)

    def test_a_mixed_dns_answer_is_refused(self):
        with mock.patch.object(verify.socket, "getaddrinfo", return_value=[
                (2, 1, 6, "", ("93.184.216.34", 80)), (2, 1, 6, "", ("10.0.0.5", 80))]):
            with self.assertRaisesRegex(verify.PageError, "local or private"):
                verify.resolve_public("mixed.test", 80)


if __name__ == "__main__":
    unittest.main()

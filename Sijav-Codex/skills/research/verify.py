#!/usr/bin/env python3
"""Sijav-Codex research: check every cited quote on its own page, then let Jev judge support.

    init    --question Q [--why W] [--scope S] [--answers A] [--depth quick|standard|deep]
            [--effort high|xhigh] [--project DIR]
    check   --run DIR (--claims FILE | --report FILE) [--snapshots FILE] [--findings FILE ...]
            [--no-fetch] [--name check] [--recover-interrupted]
    status  --run DIR [--name check]

This helper automates only the citation check of a research run. The plan, the owner's questions,
the sub-question searches, the gap rounds, the report and its claim list are written by native
gpt-6-astra agents under the orchestrator's direction ($sijav-codex-research); `init` only creates
the run folder <project>/.codex/research/<UTC time>-<slug>-<id>/ and records the brief and the depth
(quick 3 sub-questions and 1 search round, standard 5 and 2, deep 7 and 3) for them to follow.

`check` takes the report's claim list, exactly as written: a JSON file {"claims": [{"n", "claim",
"source", "quote"}]} or the report itself, whose last fenced json block holds that list. Then, step
by step, each saved under <run>/<name>/ so a rerun resumes where it stopped without repeating a
finished step or hiding a failure:

  inputs.json  fingerprints of the claim list, snapshots, findings, fetch mode and Jev availability;
               a rerun with different inputs is refused (give the new inputs a new --name)
  1. claims.json   the claim list as given (bytes kept), and each entry as parsed
  2. pages/        each cited page once, either fetched afresh or taken from a recorded snapshot.
                   A fetch goes only to a public http(s) address, connects to the very address that
                   was checked (no second DNS lookup), re-checks every redirect, sends SNI and verifies
                   the certificate for the host, and stops at 20 s per operation and 5 MB. A snapshot
                   is a file inside the run folder whose sha256, fetch time and fetcher are given.
                   A page that could not be read is recorded with its cause and not refetched.
  3. quotes.json   whether each quote is on its page: word for word, on word boundaries (typography,
                   spacing and case aside). A quote cut with "..." is accepted only when every piece
                   has at least 4 words and each follows the last within 300 characters; otherwise it
                   is too fragmentary to check. The page's matched wording is kept.
  4. jev.json      Jev (jev-latest, through ../roast/jev.py) is asked, in batches of 40 nouls, whether
                   the page's matched wording supports each claim (only for quotes found on their
                   page, or found in the search findings for an unreadable page). Code-like claims or
                   quotes are not sent. Each batch's request is written before the call; a request with
                   no recorded reply or error means a run stopped mid-call, and the rerun refuses until
                   --recover-interrupted records it as interrupted. Jev is never asked twice.
  5. verdicts.json, check.md (and report-checked.md when a report was given)

Verdicts (source research.py): supported when the quote is on its page and Jev's probability is at
least 0.65; not supported at 0.35 or less; unclear between; quote not on its page; unconfirmed: page
not read, page truncated or not fully read, elided quote too fragmentary, the claim cannot be checked;
on its page; jev not asked. Only `supported` is supported: every other verdict is unconfirmed when
the report is used. SIJAV_JEV=off, a missing key or SDK, or a failed Jev call: pages and quotes are
still checked and the check says Jev did not judge them.

Exit codes: 0 checked; 1 failed (what is done is kept; rerun to resume); 2 usage, changed inputs, or
the report gave no claim list (nothing checked: treat every claim as unconfirmed).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import html.parser
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import tempfile
import urllib.parse
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "roast"))
import jev as jevlib  # noqa: E402  -- the key, the switch, the validated typed Jev call

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SCHEMA = "sijav-codex-research-check/2"
DEPTH = {"quick": (3, 1), "standard": (5, 2), "deep": (7, 3)}  # (sub-questions, search rounds)
MODEL = "gpt-6-astra"
JEV_BATCH = 40  # claims per Jev call, well inside its token limits
# Jev's undecided band is (0.35, 0.65): at or above 0.65 a quote supports its claim, at or below
# 0.35 it does not, and between the two it is unclear (source research.py).
SUPPORTED, UNSUPPORTED = 0.65, 0.35
PAGE_BYTES, PAGE_SECONDS, PAGE_WORKERS, MAX_REDIRECTS = 5_000_000, 20, 8, 5
MIN_PIECE_WORDS, MAX_GAP, MAX_SPAN = 4, 300, 2000
AGENT = "Mozilla/5.0 (compatible; sijav-research-check/1.0; +https://github.com/sijav/Sijav-Skills)"
BLOCKS = {"p", "div", "li", "br", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote",
          "section", "article", "header", "footer", "dt", "dd", "table", "ul", "ol", "figcaption", "main", "aside"}
JEV_STATE = {"task": "Judge from the quoted passage alone whether it supports the claim it is cited "
                     "for. Each question gives one claim and the passage as it stands on the cited page."}
JEV_CRITERIA = {"true": "The passage says what the claim says, or clearly implies it.",
                "false": "The passage does not say it, says less, or says something else."}
NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
SIX_TO_FOUR = ipaddress.ip_network("2002::/16")
OK, FAILED, USAGE = 0, 1, 2


class VerifyError(Exception):
    def __init__(self, message: str, code: int = FAILED):
        super().__init__(message)
        self.code = code


class PageError(RuntimeError):
    """Why a cited page could not be read."""


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "research"


def write_new(path: Path, data) -> None:
    raw = data if isinstance(data, bytes) else (
        json.dumps(data, indent=1, ensure_ascii=False) + "\n" if not isinstance(data, str) else data
    ).encode("utf-8")
    with open(path, "xb") as f:
        f.write(raw)


def write_atomic(path: Path, data) -> None:
    """A step's result appears whole or not at all, so a crash never leaves half a step."""
    raw = (json.dumps(data, indent=1, ensure_ascii=False) + "\n" if not isinstance(data, str) else data)
    fd, tmp = tempfile.mkstemp(prefix=".step-", suffix=".tmp", dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(raw)
    os.replace(tmp, path)


def load(path: Path):
    """A step file, or None when it is absent; a damaged one stops the check with its path."""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VerifyError(f"{path} cannot be read as JSON ({exc}); it was left untouched") from exc


def project_root(explicit: str | None, start: Path | None = None) -> Path:
    if explicit:
        root = Path(explicit).expanduser()
        if not root.is_dir():
            raise VerifyError(f"--project {root} is not an existing folder", USAGE)
        return root.resolve()
    home = Path.home().resolve()
    here = (start or Path.cwd()).resolve()
    chain = []
    for p in (here, *here.parents):
        if p == home:
            break
        chain.append(p)
    # the nearest board, else .git, else a bare .claude (as claude_session.py)
    for test in (lambda p: (p / ".claude" / "todo.db").is_file(), lambda p: (p / ".git").exists(),
                 lambda p: (p / ".claude").is_dir()):
        for p in chain:
            if test(p):
                return p
    raise VerifyError(f"no project found at or above {here}: pass --project DIR; nothing was created", USAGE)


# ------------------------------------------------------------- the claims


def json_object(text: str):
    """The JSON object in a reply: its last fenced json block if it has one, else the outermost braces."""
    fenced = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    raw = fenced[-1] if fenced else text[text.find("{"): text.rfind("}") + 1]
    try:
        found = json.loads(raw)
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def without_claims(report: str) -> str:
    at = report.rfind("```json")
    return (report[:at] if at >= 0 else report).strip()


def parse_claims(text: str) -> list[dict] | None:
    """Every entry of the claim list, kept even when it cannot be checked; None when there is no list."""
    found = json_object(text)
    claims = found.get("claims") if found else None
    if not isinstance(claims, list) or not claims:
        return None
    return [c if isinstance(c, dict) else {"unparsed": c} for c in claims]


# ------------------------------------------------------------- quotes


def normal(text: str) -> str:
    """Text compared without typography: quote marks, dashes, ellipses and spacing. Case is kept, so
    the matched wording can be shown as the page has it; matching ignores case."""
    for a, b in (("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'"), (" ", " "),
                 ("—", "-"), ("–", "-"), ("…", "...")):
        text = text.replace(a, b)
    text = re.sub(r"\s+", " ", text)
    return re.sub(r" ([.,;:!?)\]])", r"\1", text).strip()


def _piece(p: str) -> re.Pattern:
    """A piece matched on word boundaries: 'can' never matches inside 'cannot'."""
    return re.compile(r"(?<!\w)" + re.escape(p) + r"(?!\w)", re.I)


def find_quote(quote: str, text: str) -> dict:
    """{"status": "found", "span": the page's matched wording} or {"status": "absent"} or
    {"status": "fragmentary", "why": ...}. An elided quote's pieces must each have MIN_PIECE_WORDS words
    and follow one another within MAX_GAP characters; nothing is accepted on piece presence alone."""
    q = normal(str(quote or "")).strip().strip("\"'").strip()
    pieces = [p.strip().strip("\"'").strip(" -,;:") for p in q.split("...")]
    pieces = [p for p in pieces if p]
    if not pieces or sum(len(p) for p in pieces) < 8:
        return {"status": "fragmentary", "why": "the quote is empty or shorter than 8 characters"}
    if len(pieces) > 1 and any(len(p.split()) < MIN_PIECE_WORDS for p in pieces):
        return {"status": "fragmentary", "why": f"an elided quote needs at least {MIN_PIECE_WORDS} words in every"
                                                " piece; a shorter piece could match unrelated text"}
    hay = normal(text)
    for first in _piece(pieces[0]).finditer(hay):
        end, ok = first.end(), True
        for p in pieces[1:]:
            m = _piece(p).search(hay, end, end + MAX_GAP + len(p) + 1)
            if not m or m.start() - end > MAX_GAP:
                ok = False
                break
            end = m.end()
        if ok:
            return {"status": "found", "span": hay[first.start():end][:MAX_SPAN]}
    return {"status": "absent"}


def quote_in(quote: str, text: str) -> bool:
    return find_quote(quote, text)["status"] == "found"


def link_key(link: str) -> str:
    p = urllib.parse.urlsplit(str(link).strip())
    return p.netloc.lower().removeprefix("www.") + p.path.rstrip("/") + (f"?{p.query}" if p.query else "")


def quoted_in_findings(claim: dict, findings: list[str]) -> dict:
    """The quote's match in a search-finding line that names this claim's page, or {}."""
    key = link_key(claim.get("source", ""))
    for text in findings:
        for line in text.splitlines():
            links = {link_key(u) for u in re.findall(r"https?://[^\s|)\]>\"']+", line)}
            if key in links:
                m = find_quote(str(claim.get("quote") or ""), line)
                if m["status"] == "found":
                    return m
    return {}


# ------------------------------------------------------------- pages


def public_ip(text: str) -> bool:
    """A globally routable address, including what NAT64, 6to4, Teredo and IPv4-mapped IPv6 embed."""
    try:
        ip = ipaddress.ip_address(text.split("%")[0])
    except ValueError:
        return False
    if not ip.is_global:
        return False
    if ip.version == 6:
        embedded = []
        if ip.ipv4_mapped:
            embedded.append(ip.ipv4_mapped)
        if any(ip in net for net in NAT64):
            embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if ip in SIX_TO_FOUR:
            embedded.append(ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF))
        if ip.teredo:
            embedded.extend(ip.teredo)
        if any(not e.is_global for e in embedded):
            return False
    return True


def resolve_public(host: str, port: int) -> str:
    """One public address for `host`: every address it resolves to must be public (no mixed answers)."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as e:
        raise PageError(f"the host was not found ({e})") from e
    addrs = [info[4][0] for info in infos]
    if not addrs or any(not public_ip(a) for a in addrs):
        raise PageError("a local or private address")
    return addrs[0]


class _PinnedHTTP(http.client.HTTPConnection):
    """Connects to the address that was checked, not to a second DNS answer."""

    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout)
        self.pinned_ip, self.peer = ip, None

    def connect(self):
        self.sock = socket.create_connection((self.pinned_ip, self.port), self.timeout)
        self.peer = self.sock.getpeername()[0]


class _PinnedHTTPS(http.client.HTTPSConnection):
    """As _PinnedHTTP, with SNI and certificate verification for the requested host name."""

    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self.pinned_ip, self.peer = ip, None

    def connect(self):
        sock = socket.create_connection((self.pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        self.peer = self.sock.getpeername()[0]


class _Text(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "title"}  # no "head": its end tag is optional

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag in BLOCKS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skipping = max(0, self.skipping - 1)
        elif tag in BLOCKS:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)


def visible_text(page: str) -> str:
    parser = _Text()
    parser.feed(page)
    parser.close()
    return "".join(parser.parts)


def decode(raw: bytes, charset: str | None, html_page: bool) -> tuple[str, str]:
    """(text, charset used). The header's charset, else an HTML <meta> charset, else UTF-8; strictly,
    so a wrong or unknown charset makes the page unread rather than garbled."""
    used = charset
    if not used and html_page:
        m = re.search(rb"<meta[^>]+charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", raw[:4096], re.I)
        used = m.group(1).decode("ascii") if m else None
    used = used or "utf-8"
    try:
        return raw.decode(used), used
    except LookupError as exc:
        raise PageError(f"the page declares an unknown charset {used!r}") from exc
    except UnicodeDecodeError as exc:
        raise PageError(f"the page is not valid {used} text ({exc.reason} at byte {exc.start})") from exc


def fetch_text(url: str) -> tuple[str, dict]:
    """(the readable text of a public page fetched afresh, how it was read). PageError says why not."""
    for _hop in range(MAX_REDIRECTS + 1):
        url = url.split("#")[0]
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise PageError("not an http or https link")
        port = p.port or (443 if p.scheme == "https" else 80)
        ip = resolve_public(p.hostname, port)
        conn = (_PinnedHTTPS if p.scheme == "https" else _PinnedHTTP)(p.hostname, port, ip, PAGE_SECONDS)
        try:
            conn.request("GET", (p.path or "/") + (f"?{p.query}" if p.query else ""),
                         headers={"User-Agent": AGENT, "Accept": "text/html,text/plain;q=0.9",
                                  "Accept-Encoding": "identity"})
            r = conn.getresponse()
            peer = conn.peer or ""
            if not peer or ipaddress.ip_address(peer.split("%")[0]) != ipaddress.ip_address(ip.split("%")[0]):
                raise PageError(f"connected to {peer or 'an unknown address'}, not the checked address {ip}")
            if r.status in (301, 302, 303, 307, 308):
                location = r.getheader("Location")
                if not location:
                    raise PageError(f"HTTP {r.status} without a Location")
                url = urllib.parse.urljoin(url, location)
                continue
            if r.status != 200:
                raise PageError(f"HTTP {r.status} {r.reason}")
            kind = r.headers.get_content_type()
            if kind not in ("text/html", "application/xhtml+xml", "text/plain"):
                raise PageError(f"a {kind} page, not text")
            charset = r.headers.get_content_charset()
            raw = r.read(PAGE_BYTES + 1)
        except PageError:
            raise
        except Exception as e:  # noqa: BLE001 -- every network failure means the same: the page was not read
            raise PageError(f"{type(e).__name__}: {e}"[:200]) from e
        finally:
            conn.close()
        truncated = len(raw) > PAGE_BYTES
        text, used = decode(raw[:PAGE_BYTES], charset, kind != "text/plain")
        return (text if kind == "text/plain" else visible_text(text)), {
            "final_url": url, "address": ip, "charset": used, "truncated": truncated}
    raise PageError(f"more than {MAX_REDIRECTS} redirects")


def page_id(url: str) -> str:
    return sha256(url)[:16]


def read_snapshots(path: str | None, run: Path) -> tuple[dict[str, dict], str | None]:
    """({url: entry}, sha256 of the index) for an index of snapshots the orchestrator saved:
    {"<url>": {"file": "<path inside the run folder>", "sha256": "...", "fetched_at": "...",
    "by": "<the tool that fetched it>"}}. Files outside the run folder, missing provenance and
    hash mismatches are refused before anything is checked."""
    if not path:
        return {}, None
    try:
        raw = Path(path).read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise VerifyError(f"cannot read --snapshots {path} as JSON: {exc}", USAGE) from exc
    if not isinstance(data, dict):
        raise VerifyError("--snapshots must map each cited URL to its saved page", USAGE)
    out, problems, root = {}, [], run.resolve()
    for url, v in data.items():
        if not isinstance(v, dict):
            problems.append(f"{url}: give file, sha256, fetched_at and by")
            continue
        missing = [k for k in ("file", "sha256", "fetched_at", "by") if not str(v.get(k) or "").strip()]
        if missing:
            problems.append(f"{url}: missing {missing}")
            continue
        rel = Path(v["file"])
        f = (root / rel).resolve()
        if rel.is_absolute() or not f.is_relative_to(root):
            problems.append(f"{url}: {v['file']} is not inside the run folder {root}")
            continue
        try:
            actual = sha256(f.read_bytes())
        except OSError as exc:
            problems.append(f"{url}: {v['file']} cannot be read ({exc})")
            continue
        if actual != str(v["sha256"]).lower():
            problems.append(f"{url}: {v['file']} has sha256 {actual}, not the recorded {v['sha256']}")
            continue
        out[url] = {**v, "file": str(f)}
    if problems:
        raise VerifyError("the snapshot index is refused; nothing was checked:\n- " + "\n- ".join(problems), USAGE)
    return out, sha256(raw)


def one_page(url: str, snapshot: dict | None, fetch: bool, pages: Path) -> dict:
    pid = page_id(url)
    record = {"url": url, "id": pid, "at": now()}
    text = None
    try:
        if snapshot:
            raw = Path(snapshot["file"]).read_bytes()
            is_html = Path(snapshot["file"]).suffix.lower() in (".html", ".htm") or raw.lstrip()[:1] == b"<"
            body, used = decode(raw, snapshot.get("charset"), is_html)
            text = visible_text(body) if is_html else body
            record.update(how="snapshot", snapshot_file=snapshot["file"], snapshot_sha256=snapshot["sha256"],
                          snapshot_fetched_at=snapshot["fetched_at"], snapshot_by=snapshot["by"], charset=used,
                          truncated=False)
        elif not fetch:
            record.update(how="not read", error="no snapshot was given and fetching was turned off (--no-fetch)")
        else:
            text, info = fetch_text(url)
            record.update(how="fetched", **info)
    except PageError as exc:
        record.update(how="not read", error=str(exc))
        text = None
    except Exception as exc:  # noqa: BLE001 -- one page never stops the others; its cause is kept
        record.update(how="not read", error=f"{type(exc).__name__}: {exc}"[:200])
        text = None
    if text is not None:
        write_atomic(pages / f"{pid}.txt", text)
        record.update(chars=len(text), text_sha256=sha256(text), text_file=f"{pid}.txt")
    write_atomic(pages / f"{pid}.json", record)
    return record


# ------------------------------------------------------------- the check


def checkable(c: dict) -> str:
    """'' when a claim entry can be checked, else why not."""
    if "unparsed" in c:
        return "the entry is not an object"
    if not str(c.get("claim") or "").strip():
        return "the entry has no claim"
    if not str(c.get("source") or "").strip():
        return "the claim has no source link"
    return ""


def step_inputs(folder: Path, fingerprint: dict) -> None:
    """Refuse to resume a check with inputs other than the ones it began with."""
    path = folder / "inputs.json"
    old = load(path)
    if old is None:
        write_new(path, {"schema": SCHEMA, "at": now(), **fingerprint})
        return
    changed = [k for k in fingerprint if old.get(k) != fingerprint[k]]
    if changed:
        raise VerifyError(f"{folder} began with other inputs ({', '.join(changed)} changed). A check keeps its"
                          " inputs so a resumed step is never stale; give these inputs their own --name.", USAGE)


def step_claims(folder: Path, raw: bytes, given: str, claims: list[dict]) -> list[dict]:
    path = folder / "claims.json"
    old = load(path)
    if old is not None:
        return old["claims"]
    write_new(folder / "claims-as-given.txt", raw)
    write_atomic(path, {"schema": SCHEMA, "given": given, "raw_sha256": sha256(raw), "at": now(), "claims": claims})
    return claims


def step_pages(folder: Path, claims: list[dict], snapshots: dict, fetch: bool) -> dict[str, dict]:
    pages = folder / "pages"
    pages.mkdir(exist_ok=True)
    urls = sorted({str(c["source"]).strip() for c in claims if not checkable(c)})
    done = {u: load(pages / f"{page_id(u)}.json") for u in urls}
    todo = [u for u in urls if done[u] is None]
    with concurrent.futures.ThreadPoolExecutor(max_workers=PAGE_WORKERS) as pool:
        for rec in pool.map(lambda u: one_page(u, snapshots.get(u), fetch, pages), todo):
            done[rec["url"]] = rec
    return done


def page_text(folder: Path, rec: dict) -> str | None:
    if rec.get("text_file"):
        return (folder / "pages" / rec["text_file"]).read_text(encoding="utf-8")
    return None


def step_quotes(folder: Path, claims: list[dict], pages: dict, findings: list[str]) -> list[dict]:
    path = folder / "quotes.json"
    old = load(path)
    if old is not None:
        return old["rows"]
    rows = []
    for i, c in enumerate(claims):
        why = checkable(c)
        if why:
            rows.append({"i": i, "on_page": None, "why_not": why, "in_findings": False})
            continue
        rec = pages[str(c["source"]).strip()]
        text = page_text(folder, rec)
        found = find_quote(str(c.get("quote") or ""), text) if text is not None else {}
        on_page = None if text is None else found["status"] == "found"
        in_findings = quoted_in_findings(c, findings) if findings and text is None else {}
        rows.append({"i": i, "on_page": on_page, "match": found.get("status"), "fragmentary": found.get("why"),
                     "span": found.get("span") or in_findings.get("span"), "why_not": rec.get("error", "") if text is None else "",
                     "page": rec["id"], "page_how": rec["how"], "truncated": bool(rec.get("truncated")),
                     "snapshot_by": rec.get("snapshot_by"), "snapshot_fetched_at": rec.get("snapshot_fetched_at"),
                     "no_quote": not str(c.get("quote") or "").strip(), "in_findings": bool(in_findings)})
    write_atomic(path, {"findings_given": bool(findings), "rows": rows})
    return rows


def step_jev(folder: Path, claims: list[dict], rows: list[dict], recover: bool) -> dict:
    """Jev's probability per claim index; every batch's request, reply and error are kept, and a batch
    is never asked twice."""
    path = folder / "jev.json"
    old = load(path)
    if old is not None:
        return old
    ask = [r["i"] for r in rows if r["on_page"] or (r["on_page"] is None and r.get("in_findings"))]
    held, send = {}, []
    for i in ask:
        span = next(r for r in rows if r["i"] == i)["span"]
        hits = jevlib.code_in({"claim": str(claims[i].get("claim") or ""), "passage": str(span or "")})
        if hits:
            held[str(i)] = "holds code, so it was not sent to Jev: " + "; ".join(hits)
        else:
            send.append(i)
    result = {"asked": send, "held_back": held, "scores": {}, "answer_problems": {}, "batches": [],
              "status": "judged", "cause": ""}
    if not send:
        result.update(status="nothing to ask")
        write_atomic(path, result)
        return result
    why = jevlib.unavailable()
    if why:
        result.update(status="unjudged", cause=why)
        write_atomic(path, result)
        return result
    spans = {r["i"]: r.get("span") for r in rows}
    for b, start in enumerate(range(0, len(send), JEV_BATCH), 1):
        batch = send[start:start + JEV_BATCH]
        questions = {f"k{i}": {"type": "noul", "criteria": JEV_CRITERIA,
                               "instructions": {"claim": str(claims[i].get("claim") or ""), "passage": spans[i],
                                                "ask": "Does the passage, taken on its own, support the claim?"}}
                     for i in batch}
        hits = jevlib.code_in({"state": JEV_STATE, "questions": questions})
        if hits:  # cannot happen after the per-claim check; kept so nothing with code is ever sent
            raise VerifyError("code would reach Jev: " + "; ".join(hits))
        request, response, error = (folder / f"jev-{b}-{k}.json" for k in ("request", "response", "error"))
        sent_before = load(request)
        if sent_before is not None and sent_before.get("questions") != questions:
            raise VerifyError(f"{request} asked other questions than this check would; it was left untouched")
        if sent_before is not None and not response.exists() and not error.exists():
            if not recover:
                raise VerifyError(
                    f"{request} was handed to the SDK and no reply or error was recorded: an earlier run stopped"
                    " during that Jev call, which may have been answered. Jev is never asked twice: rerun with"
                    " --recover-interrupted to record the batch as interrupted (outcome unknown).")
            write_new(error, {"at": now(), "outcome": "unknown",
                              "cause": "an earlier run stopped during this call; recorded by --recover-interrupted"})
        if error.exists():  # a recorded failure stays a failure: no re-ask on resume
            exc_text = (load(error) or {}).get("cause", "recorded failure")
        elif response.exists():  # answered before an interruption: reuse, never ask twice
            exc_text, reply = None, load(response)["reply"]
        else:
            write_new(request, {"model": jevlib.JEV_MODEL, "state": JEV_STATE, "questions": questions,
                                "handed_to_sdk_at": now()})
            try:
                reply, exc_text = jevlib.call(JEV_STATE, questions), None
            except jevlib.JevCallFailed as exc:
                exc_text = str(exc)
                write_new(error, {"cause": exc_text, "at": now(), "outcome": exc.outcome})
            except jevlib.JevError as exc:
                exc_text = str(exc)
                write_new(error, {"cause": exc_text, "at": now(), "outcome": "not_sent"})
            except Exception as exc:  # noqa: BLE001 -- recorded; the outcome is unknown
                exc_text = f"unexpected {type(exc).__name__}: {exc}"
                write_new(error, {"cause": exc_text, "at": now(), "outcome": "unknown"})
            except BaseException as exc:
                write_new(error, {"cause": f"interrupted ({type(exc).__name__})", "at": now(), "outcome": "unknown"})
                raise
            else:
                write_new(response, {"reply": reply, "received_at": now()})
        if exc_text is not None:
            result.update(status="unjudged" if b == 1 else "partial",
                          cause=f"batch {b} failed: {exc_text}; it and later claims were not judged")
            result["batches"].append({"batch": b, "claims": batch, "status": "failed"})
            break
        answers = reply.get("answers") if isinstance(reply, dict) else None
        problems = jevlib.answer_problems(questions, reply)
        for i in batch:
            a = (answers or {}).get(f"k{i}")
            bad = jevlib.one_answer_problems(questions[f"k{i}"], a) if isinstance(a, dict) and a.get("type") == "noul" \
                else ["no noul answer"]
            if bad:
                result["answer_problems"][str(i)] = "; ".join(bad)
            else:
                result["scores"][str(i)] = float(a["noul"])
        result["batches"].append({"batch": b, "claims": batch, "status": "answered",
                                  "served_model": reply.get("model") if isinstance(reply, dict) else None,
                                  "usage": reply.get("usage") if isinstance(reply, dict) else None,
                                  "reply_problems": problems})
    write_atomic(path, result)
    return result


def cell(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).replace("|", "/").strip()[:160]


def step_verdicts(folder: Path, claims: list[dict], rows: list[dict], jev: dict) -> tuple[list[dict], str]:
    verdicts, tally = [], {}
    findings_given = load(folder / "quotes.json")["findings_given"]
    for c, r in zip(claims, rows):
        i = str(r["i"])
        p = jev["scores"].get(i)
        note = ""
        if "page" not in r:
            verdict, where = "unconfirmed: the claim cannot be checked", f"not checked: {r['why_not']}"
        elif r["on_page"] is None:
            where = f"not read: {r['why_not']}"
            verdict = "unconfirmed: page not read"
            if findings_given and not r["in_findings"]:
                verdict += "; the quote is not in the findings"
        elif r.get("match") == "fragmentary":
            where, verdict, note = "not checkable", "unconfirmed: elided quote too fragmentary to check", r["fragmentary"]
        elif not r["on_page"] and r.get("truncated"):
            where, verdict = "not in the first 5 MB", "unconfirmed: page truncated before the quote could be found"
        elif not r["on_page"]:
            where, verdict = "no", "quote not on its page"
            if r.get("no_quote"):
                note = "no quote was given"
        else:
            where = "yes" + (f" (snapshot by {r['snapshot_by']}, fetched {r['snapshot_fetched_at']})"
                             if r.get("page_how") == "snapshot" else "")
            if p is None:
                verdict = "on its page; jev not asked"
                note = (jev["held_back"].get(i) or jev["answer_problems"].get(i)
                        or (f"Jev did not judge: {jev['cause']}" if jev.get("cause") else "Jev did not judge"))
            else:
                verdict = "supported" if p >= SUPPORTED else "not supported" if p <= UNSUPPORTED else "unclear"
        if r["on_page"] is None and p is not None:
            note = f"Jev {p:.2f} on the passage in the findings, but the page was not read"
        head = verdict.split(";")[0]
        tally[head] = tally.get(head, 0) + 1
        verdicts.append({"i": r["i"], "n": c.get("n"), "source": c.get("source"), "claim": c.get("claim"),
                         "quote": c.get("quote"), "matched_on_page": r.get("span"), "quote_on_page": where,
                         "jev": p, "verdict": verdict, "supported": verdict == "supported", "note": note})
    counts = ", ".join(f"{n} {v}" for v, n in sorted(tally.items(), key=lambda kv: -kv[1]))
    lines = []
    for v in verdicts:
        shown = "" if v["jev"] is None else f"{v['jev']:.2f}"
        lines.append(f"| {v['i'] + 1} | [{cell(v['n'])}] {cell(v['source'])} | {cell(v['claim'])} | "
                     f"{cell(v['quote_on_page'])} | {shown} | {v['verdict']} | {cell(v['note'])} |")
    if jev["status"] == "judged":
        note = ""
    elif jev["status"] == "nothing to ask":
        note = "\n\nJev was not asked: no quote could be confirmed on its page."
    else:
        note = f"\n\nJev did not judge every quote ({jev['status']}): {jev['cause']}."
    text = ("## Citation check\n\n"
            f"{len(claims)} claims: {counts}.\n\n"
            "Each claim's quote was looked for, word for word, on the page it cites, fetched afresh or from a"
            " recorded snapshot; Jev then judged whether the page's matched wording supports the claim"
            f" (supported at {SUPPORTED} or more, not supported at {UNSUPPORTED} or less, unclear between). A"
            " claim counts as supported only when its quote is on its page and Jev says so; treat every other"
            f" verdict as unconfirmed.{note}\n\n"
            "| # | Source | Says | Quote on its page | jev | Verdict | Note |\n|---|---|---|---|---|---|---|\n"
            + "\n".join(lines) + "\n")
    write_atomic(folder / "verdicts.json", {"verdicts": verdicts, "tally": tally, "jev_status": jev["status"],
                                            "jev_cause": jev.get("cause", "")})
    write_atomic(folder / "check.md", text)
    return verdicts, text


# ------------------------------------------------------------- commands


def cmd_init(a) -> int:
    root = project_root(a.project)
    rid = uuid.uuid4().hex[:6]
    folder = root / ".codex" / "research" / f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{slug(a.question)}-{rid}"
    folder.mkdir(parents=True)
    n, rounds = DEPTH[a.depth]
    write_new(folder / "run.json", {"id": rid, "question": a.question, "why": a.why, "scope": a.scope,
                                    "answers": a.answers, "depth": a.depth, "sub_questions": n,
                                    "search_rounds": rounds, "follow_ups_per_gap_round": 3,
                                    "parallel_searches": 4, "model": MODEL, "effort": a.effort,
                                    "created": now()})
    print(str(folder))
    print(f"[verify] research run for native agents: {n} sub-questions, {rounds} search round(s),"
          f" {MODEL} at {a.effort}. The plan, searches, gaps and report are the native agents' steps.",
          file=sys.stderr)
    return OK


def cmd_check(a) -> int:
    run = Path(a.run).resolve()
    if not run.is_dir():
        raise VerifyError(f"--run {run} is not a folder", USAGE)
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,40}", a.name):
        raise VerifyError("--name is a lowercase name of letters, digits, '.', '_' or '-'", USAGE)
    src = a.claims or a.report
    try:
        raw = Path(src).read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise VerifyError(f"cannot read {src} as UTF-8: {exc}", USAGE) from exc
    claims = parse_claims(text)
    folder = run / a.name
    if claims is None:
        folder.mkdir(exist_ok=True)
        msg = ("## Citation check\n\n**Not checked:** the report gave no list of its claims, so no citation was"
               " checked. Treat every claim as unconfirmed.\n")
        if not (folder / "check.md").exists():
            write_atomic(folder / "check.md", msg)
        print(msg)
        print("NOT CHECKED: no claim list was found; ask the report's author once for it, or treat every claim"
              " as unconfirmed.", file=sys.stderr)
        return USAGE
    snapshots, snapshots_sha = read_snapshots(a.snapshots, run)
    findings = []
    for f in a.findings:
        try:
            findings.append(Path(f).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            raise VerifyError(f"cannot read --findings {f}: {exc}", USAGE) from exc
    folder.mkdir(exist_ok=True)
    step_inputs(folder, {"claims_sha256": sha256(raw), "snapshots_sha256": snapshots_sha,
                         "findings_sha256": [sha256(t) for t in findings], "fetch": not a.no_fetch,
                         "jev": jevlib.unavailable() or "available"})
    claims = step_claims(folder, raw, str(Path(src).resolve()), claims)
    pages = step_pages(folder, claims, snapshots, fetch=not a.no_fetch)
    rows = step_quotes(folder, claims, pages, findings)
    jev = step_jev(folder, claims, rows, recover=a.recover_interrupted)
    verdicts, text = step_verdicts(folder, claims, rows, jev)
    if a.report:
        out = folder / "report-checked.md"
        if not out.exists():
            write_new(out, f"{without_claims(text_of(a.report))}\n\n{text}\n_The research record: {run}_\n")
    print(text)
    print(f"[verify] record: {folder}", file=sys.stderr)
    return OK


def text_of(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def cmd_status(a) -> int:
    folder = Path(a.run).resolve() / a.name
    steps = [("inputs", "inputs.json"), ("claims", "claims.json"), ("pages", "pages"), ("quotes", "quotes.json"),
             ("jev", "jev.json"), ("verdicts", "verdicts.json")]
    print(f"citation check {folder}")
    for name, f in steps:
        p = folder / f
        state = "done" if p.exists() else "not yet"
        try:
            if name == "pages" and p.is_dir():
                recs = [load(x) or {} for x in p.glob("*.json")]
                state = f"{len(recs)} pages: " + ", ".join(
                    f"{sum(1 for r in recs if r.get('how') == h)} {h}" for h in ("fetched", "snapshot", "not read"))
            if name == "jev" and p.exists():
                j = load(p) or {}
                state = f"{j.get('status', 'unknown')}" + (f" ({j['cause']})" if j.get("cause") else "")
        except VerifyError as exc:
            state = f"unreadable: {exc}"
        print(f"  {name}: {state}")
    pending = [r.name for r in folder.glob("jev-*-request.json")
               if not (folder / r.name.replace("request", "response")).exists()
               and not (folder / r.name.replace("request", "error")).exists()]
    if pending:
        print(f"  interrupted Jev call(s) with no recorded outcome: {pending} (rerun check with --recover-interrupted)")
    return OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    i = sub.add_parser("init", allow_abbrev=False)
    i.add_argument("--question", required=True)
    i.add_argument("--why", default="not stated")
    i.add_argument("--scope", default="not stated")
    i.add_argument("--answers", default="none yet")
    i.add_argument("--depth", choices=list(DEPTH), default="standard")
    i.add_argument("--effort", choices=("high", "xhigh"), default="high")
    i.add_argument("--project", default=None)
    c = sub.add_parser("check", allow_abbrev=False)
    c.add_argument("--run", required=True)
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--claims", help='a JSON file {"claims": [...]}')
    g.add_argument("--report", help="the report, ending with its fenced json claim list")
    c.add_argument("--snapshots", default=None, help="JSON index of saved pages inside the run folder")
    c.add_argument("--findings", action="append", default=[], help="saved search answers (repeatable)")
    c.add_argument("--no-fetch", action="store_true", help="read pages only from snapshots")
    c.add_argument("--name", default="check")
    c.add_argument("--recover-interrupted", action="store_true",
                   help="record a Jev call an earlier run left without an outcome as interrupted")
    s = sub.add_parser("status", allow_abbrev=False)
    s.add_argument("--run", required=True)
    s.add_argument("--name", default="check")
    a = ap.parse_args(argv)
    try:
        return {"init": cmd_init, "check": cmd_check, "status": cmd_status}[a.command](a)
    except VerifyError as exc:
        print(f"VERIFY {'USAGE' if exc.code == USAGE else 'FAILED'}: {exc}", file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(f"VERIFY FAILED: {exc}. What is done is kept; rerun the same command to resume.", file=sys.stderr)
        return FAILED


if __name__ == "__main__":
    sys.exit(main())

"""copy_session: a codex session's readable history copied into a new session
under the logged-in account; a manual tool, not used by the runner (owner,
2026-09-26: "Keep the copier, revert the rest"). Every failure names its
exact cause."""

import io
import json
import types

import pytest

import codex_context as cc
import copy_session


@pytest.fixture
def home(tmp_path, monkeypatch):
    # never the real login or session files
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("CODEX_ACCOUNT_ID", "acct-A")
    return tmp_path


def rollout(home, thread, lines, account="acct-A"):
    """A session file for `thread` in the fake CODEX_HOME."""
    d = home / "codex-home" / "sessions" / "2026" / "09" / "26"
    d.mkdir(parents=True, exist_ok=True)
    meta = {"type": "session_meta", "payload": {"id": thread, "creator_account_id": account}}
    rows = [meta] + [{"type": "response_item", "payload": p} for p in lines]
    (d / f"rollout-2026-09-26T00-00-00-{thread}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def say(role, text):
    kind = "output_text" if role == "assistant" else "input_text"
    return {"type": "message", "role": role, "content": [{"type": kind, "text": text}]}


def fake_app_server(monkeypatch, ids, made, compactions=None):
    """codex app-server stand-in: new sessions get the next id; each session's
    injected items are appended to `made` (one list per new session)."""
    ids = iter(ids)

    class Server:
        def __init__(self, *a, **k):
            pass

        def send(self, method, params=None, notify=False):
            if method == "thread/start":
                made.append([])
                return {"thread": {"id": next(ids)}}
            if method == "thread/inject_items":
                made[-1].extend(params["items"])
            return {}

        def wait_turn(self, thread, timeout):
            if compactions is not None:
                compactions.append(thread)
            return {"status": "completed", "durationMs": 0}

        def close(self):
            pass

    monkeypatch.setattr(cc, "_AppServer", Server)


def test_the_export_keeps_every_readable_item_and_nothing_encrypted(home):
    rollout(home, "T-9", [
        say("user", "find a cafe"),
        {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "permissions"}]},
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "they want quiet"}], "encrypted_content": "SECRET"},
        {"type": "reasoning", "summary": [], "encrypted_content": "SECRET2"},
        {"type": "function_call", "name": "search", "arguments": '{"q": "cafe"}', "call_id": "c1"},
        {"type": "function_call_output", "call_id": "c1", "output": "2 places"},
        say("assistant", "here are two"),
    ])
    items = cc.export_context(cc.rollout_path("T-9"))
    texts = [i["content"][0]["text"] for i in items]
    assert texts == ["find a cafe", "[my reasoning summary]\nthey want quiet", '[tool call search]\n{"q": "cafe"}',
                     "[tool output]\n2 places", "here are two"]
    assert "SECRET" not in json.dumps(items)


def test_the_export_leaves_out_the_notes_codex_writes_itself(home):
    rollout(home, "T-8", [
        say("user", "<environment_context>\n  <cwd>D:\\old</cwd>\n</environment_context>"),
        say("user", "<recommended_plugins>\nHere is a list of plugins\n</recommended_plugins>"),
        say("user", "# AGENTS.md instructions for D:\\old\n\n<INSTRUCTIONS>be brief</INSTRUCTIONS>"),
        {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "<environment_context>x</environment_context>"},
            {"type": "input_text", "text": "the real ask"}]},
        say("assistant", "<environment_context> is what codex sends"),
    ])
    texts = [i["content"][0]["text"] for i in cc.export_context(cc.rollout_path("T-8"))]
    assert texts == ["the real ask", "<environment_context> is what codex sends"]


def test_a_big_history_goes_in_batches_with_a_compaction_between(home, monkeypatch):
    rollout(home, "T-big", [say("user", "x" * 40) for _ in range(5)])
    made, compactions = [], []
    fake_app_server(monkeypatch, ["T-new"], made, compactions)
    got = copy_session.copy_session("T-big", None, cwd=home, log=home / "copy.log", batch_chars=90)
    assert got == "T-new"
    assert len(made[0]) == 5                      # every item arrived, word for word
    assert compactions == ["T-new", "T-new"]      # 3 batches of 2, 2 and 1: a compaction after the first two


def test_a_small_history_goes_in_one_batch(home, monkeypatch):
    rollout(home, "T-small", [say("user", "hi"), say("assistant", "hello")])
    made, compactions = [], []
    fake_app_server(monkeypatch, ["T-new"], made, compactions)
    assert copy_session.copy_session("T-small", "T-old", cwd=home, log=home / "copy.log") == "T-new"
    assert compactions == [] and len(made[0]) == 2


def test_a_missing_source_names_where_it_looked(home):
    with pytest.raises(cc.ContextError, match="no session file for thread T-none under"):
        copy_session.copy_session("T-none", None, cwd=home, log=home / "copy.log")


def app_server_reading(tmp_path, lines, pending=()):
    """A codex_context._AppServer whose app-server printed `lines`."""
    srv = object.__new__(cc._AppServer)
    srv.log, srv.err, srv.pending = tmp_path / "app.log", [], list(pending)
    srv.proc = types.SimpleNamespace(stdout=io.StringIO("".join(json.dumps(m) + "\n" for m in lines)),
                                     poll=lambda: None)
    return srv


def turn_done(status, thread="T-1", error=None):
    return {"method": "turn/completed",
            "params": {"threadId": thread, "turn": {"status": status, "error": error, "durationMs": 88842}}}


def test_a_compaction_ends_when_its_turn_completes(tmp_path):
    # what codex 0.157.0 sent live on 2026-09-26: no thread/compacted at all
    srv = app_server_reading(tmp_path, [
        {"method": "turn/started", "params": {"threadId": "T-1"}},
        {"method": "item/started", "params": {"threadId": "T-1", "item": {"type": "contextCompaction"}}},
        {"method": "item/completed", "params": {"threadId": "T-1", "item": {"type": "contextCompaction"}}},
        turn_done("completed", thread="T-other"),
        turn_done("completed"),
    ])
    assert srv.wait_turn("T-1", 5)["durationMs"] == 88842


def test_a_failed_compaction_names_codex_own_error(tmp_path):
    srv = app_server_reading(tmp_path, [turn_done("failed", error={"message": "context too long"})])
    with pytest.raises(cc.ContextError, match="ended 'failed'.*context too long"):
        srv.wait_turn("T-1", 5)


def test_a_turn_that_ended_before_the_answer_is_not_missed(tmp_path):
    srv = app_server_reading(tmp_path, [], pending=[turn_done("completed")])
    assert srv.wait_turn("T-1", 5)["status"] == "completed"


def test_an_error_codex_retries_is_not_a_failure(tmp_path):
    retried = {"method": "error", "params": {"threadId": "T-1", "willRetry": True, "error": {"message": "busy"}}}
    assert app_server_reading(tmp_path, [retried, turn_done("completed")]).wait_turn("T-1", 5)
    final = {"method": "error", "params": {"threadId": "T-1", "willRetry": False, "error": {"message": "quota"}}}
    with pytest.raises(cc.ContextError, match="quota"):
        app_server_reading(tmp_path, [final]).wait_turn("T-1", 5)

"""A stand-in for typesafe-sdk 0.7.1 in tests. It makes no network call.

Its public surface mirrors the installed 0.7.1 source (typesafe_sdk/__init__.py, _core/retry.py,
_core/errors.py, _core/response_types.py): TypeSafeClient(*, api_key, model, retry, timeout, headers,
transport, http_client, base_url); system_one(state, questions, *, model, retry, timeout, ...);
RetryPolicy(max_retries=2, ...); TypeSafeAPIError(status, body, headers, message, endpoint) and its
status subclasses; TypeSafeAPIConnectionError and TypeSafeAPITimeoutError; SystemOneResponse with
model, usage and answers, whose model_dump(mode="json") gives string keys for score levels, and a
request_id. tests/test_jev_sdk.py runs the real installed SDK through a local transport.

$FAKE_TS_LOG gets one JSON line per system_one call (the api_key, model, state, questions, and the
retry and timeout given to the client and to the call). $FAKE_TS_VERSION overrides __version__.
$FAKE_TS_MODE says how to answer:
  ok             every question answered in its type (nouls 0.1 unless $FAKE_TS_NOUL, a JSON map
                 from question id to probability, says otherwise)
  missing        the first question gets no answer
  extra          an answer to a question that was not asked
  wrong_type     the first answer has another type
  out_of_range   the first noul is 1.7
  too_long       HTTP 400 with Jev's max_tokens_exceeded body, every call
  too_long_once  max_tokens_exceeded on the first call only (counted in $FAKE_TS_COUNT)
  forbidden      HTTP 403, with the api key echoed in the body
  connection     a connection error without an HTTP response
  timeout        the SDK's timeout error
  fail_batch_2   ok on the first call, HTTP 503 on the second
  unexpected     a RuntimeError that is not an SDK error
  interrupt      KeyboardInterrupt during the call
  unserializable a reply object that is not JSON data
  crash          the process ends abruptly during the call (as if killed)
"""

import json
import os
from dataclasses import dataclass, field

__version__ = os.environ.get("FAKE_TS_VERSION", "0.7.1")


class TypeSafeError(Exception):
    pass


class TypeSafeAPIError(TypeSafeError):
    def __init__(self, status, body, headers=None, message=None, endpoint="POST /v1/system-one"):
        super().__init__(status, body, headers, message, endpoint)
        self.status, self.body, self.headers, self.endpoint = status, body, headers or {}, endpoint

    @property
    def request_id(self):
        return "req_fake_error"

    def __str__(self):
        return f"{self.endpoint}: {self.status} {self.body if isinstance(self.body, str) else json.dumps(self.body)}"


class TypeSafeBadRequestError(TypeSafeAPIError):
    pass


class TypeSafePermissionDeniedError(TypeSafeAPIError):
    pass


class TypeSafeInternalServerError(TypeSafeAPIError):
    pass


class TypeSafeAPIConnectionError(TypeSafeError, ConnectionError):
    pass


class TypeSafeAPITimeoutError(TypeSafeAPIConnectionError, TimeoutError):
    def __init__(self, timeout):
        super().__init__(timeout)
        self.timeout = timeout

    def __str__(self):
        return f"Request timed out (timeout={self.timeout})."


@dataclass
class RetryPolicy:
    max_retries: int = 2
    backoff_initial: float = 0.5
    backoff_max: float = 5.0
    backoff_jitter: float = 0.25
    http_statuses: set = field(default_factory=lambda: {408, 429, *range(500, 600)})
    timeout: float = 30.0


class _Question:
    kind = ""

    def __init__(self, *, instructions=None, criteria=None):
        if self.kind in ("choice", "score") and not criteria:
            raise ValueError(f"{self.kind} needs criteria")
        self.instructions, self.criteria = instructions, criteria

    def model_dump(self, mode="python"):
        out = {"type": self.kind, "instructions": self.instructions}
        if self.criteria is not None:
            out["criteria"] = self.criteria
        return out


class Noul(_Question):
    kind = "noul"


class Choice(_Question):
    kind = "choice"


class Score(_Question):
    kind = "score"


class SystemOneResponse:
    def __init__(self, model, usage, answers):
        self.model, self.usage, self.answers = model, usage, answers

    @property
    def request_id(self):
        return "req_fake_123"

    def model_dump(self, mode="python"):
        return {"model": self.model, "usage": dict(self.usage), "answers": json.loads(json.dumps(self.answers))}


class _NotJSON:
    def __init__(self):
        self.model = object()
        self.request_id = "req_fake_unserializable"
        self.raw_http_response = type("Raw", (), {"text": '{"model": "jev-1.13.0", "answers": "raw wire body"}'})()


def _answer(qid, q, nouls):
    if q.kind == "noul":
        return {"type": "noul", "noul": float(nouls.get(qid, 0.1))}
    if q.kind == "choice":
        names = list(q.criteria)
        probs = {n: (0.7 if i == 0 else 0.3 / (len(names) - 1)) for i, n in enumerate(names)}
        return {"type": "choice", "choice": names[0], "confidence": 0.55, "probabilities": probs}
    levels = len(q.criteria)
    probs = {i: 1.0 / levels for i in range(levels)}
    return {"type": "score", "score": (levels - 1) / 2, "confidence": 0.2,
            "legend": {i: c for i, c in enumerate(q.criteria)}, "probabilities": probs}


def _count():
    path = os.environ.get("FAKE_TS_COUNT")
    if not path:
        return 1
    n = int(open(path).read()) + 1 if os.path.exists(path) else 1
    with open(path, "w") as f:
        f.write(str(n))
    return n


def _policy(p):
    return None if p is None else {"max_retries": p.max_retries}


class TypeSafeClient:
    def __init__(self, *, api_key=None, model=None, retry=None, timeout=None, headers=None, transport=None,
                 http_client=None, base_url=None):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        if not self.api_key:
            raise TypeSafeError("no api key")
        self.retry, self.timeout = retry, timeout

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        pass

    def system_one(self, state, questions, *, model=None, retry=None, timeout=None, extra_headers=None,
                   extra_body=None, response_model=None):
        log = os.environ.get("FAKE_TS_LOG")
        if log:
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"api_key": self.api_key, "model": model, "state": state,
                                    "questions": {k: v.model_dump() for k, v in questions.items()},
                                    "client_retry": _policy(self.retry), "client_timeout": self.timeout,
                                    "call_retry": _policy(retry), "call_timeout": timeout}) + "\n")
        mode = os.environ.get("FAKE_TS_MODE", "ok")
        gate = os.environ.get("FAKE_TS_GATE")  # hold the call until the test creates this file
        if gate:
            import time
            while not os.path.exists(gate):
                time.sleep(0.05)
        n = _count()
        if mode == "too_long" or (mode == "too_long_once" and n == 1):
            raise TypeSafeBadRequestError(400, {"detail": {"error_type": "max_tokens_exceeded",
                                                           "message": "state plus questions exceed 64k tokens"}})
        if mode == "forbidden":
            raise TypeSafePermissionDeniedError(403, f"forbidden for key {self.api_key}")
        if mode == "connection":
            raise TypeSafeAPIConnectionError("Connection error: connection reset by peer")
        if mode == "timeout":
            raise TypeSafeAPITimeoutError(timeout)
        if mode == "fail_batch_2" and n == 2:
            raise TypeSafeInternalServerError(503, "service unavailable")
        if mode == "unexpected":
            raise RuntimeError("an HTTP library error the SDK did not wrap")
        if mode == "interrupt":
            raise KeyboardInterrupt
        if mode == "crash":
            os._exit(9)
        if mode == "unserializable":
            return _NotJSON()
        nouls = json.loads(os.environ.get("FAKE_TS_NOUL", "{}"))
        answers = {qid: _answer(qid, q, nouls) for qid, q in questions.items()}
        first = next(iter(answers))
        if mode == "missing":
            answers.pop(first)
        elif mode == "extra":
            answers["not_asked"] = {"type": "noul", "noul": 0.5}
        elif mode == "wrong_type":
            answers[first] = {"type": "score", "score": 1.0, "confidence": 0.5, "probabilities": {0: 0.5, 1: 0.5}}
        elif mode == "out_of_range":
            answers[first] = {"type": "noul", "noul": 1.7}
        return SystemOneResponse("jev-1.13.0", {"input_tokens": 900, "output_tokens": None}, answers)

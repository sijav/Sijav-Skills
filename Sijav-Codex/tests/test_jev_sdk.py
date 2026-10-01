"""jev.call against the REAL installed typesafe-sdk 0.7.x, through a local httpx2.MockTransport.

No network, no real key: the transport is injected through jev.CLIENT_OPTIONS and answers every
request in-process, and the key is a made-up test value. This checks the helper's use of the real
SDK contract (TypeSafeClient options, RetryPolicy(max_retries=0), the timeout, question objects with
structured instructions, response parsing with integer score levels, error classes) instead of a
fake's assumptions. Skipped where the SDK is not installed (it needs Python 3.10+).
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

STAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STAGE / "skills" / "roast"))
import jev  # noqa: E402

def _real_sdk():
    """The installed SDK, imported past any test fixture that shadows it on sys.path."""
    saved_path, saved_mod = sys.path[:], sys.modules.pop("typesafe_sdk", None)
    sys.path[:] = [p for p in sys.path if "fixtures" not in p]
    try:
        import typesafe_sdk as real
        return real
    except ImportError:
        return None
    finally:
        sys.path[:] = saved_path
        if saved_mod is not None:
            sys.modules["typesafe_sdk"] = saved_mod
        else:
            sys.modules.pop("typesafe_sdk", None)


SDK = _real_sdk()
try:
    import httpx2
except ImportError:
    httpx2 = None
REAL = SDK is not None and httpx2 is not None and SDK.__version__.startswith("0.7.")

KEY = "ts-test-key-not-real-0123"
QUESTIONS = {
    "q1": {"type": "noul", "instructions": {"claim": "Saved answers survive a restart.",
                                             "ask": "Does the state support the claim?"},
           "criteria": {"true": "it does", "false": "it does not"}},
    "c": {"type": "choice", "instructions": "Which step is weakest?", "criteria": {"a": "saving", "b": "showing"}},
    "s": {"type": "score", "instructions": "How complete is it?", "criteria": ["low", "mid", "high"]},
}
STATE = {"what_was_done": "Answers are saved when given.", "checked_facts": ["The store writes each answer."]}
GOOD = {"model": "jev-1.13.0", "usage": {"input_tokens": 5, "output_tokens": None},
        "answers": {"q1": {"type": "noul", "noul": 0.3},
                    "c": {"type": "choice", "choice": "a", "confidence": 0.4, "probabilities": {"a": 0.7, "b": 0.3}},
                    "s": {"type": "score", "score": 1.2, "confidence": 0.2,
                          "legend": {"0": "low", "1": "mid", "2": "high"},
                          "probabilities": {"0": 0.2, "1": 0.4, "2": 0.4}}}}


@unittest.skipUnless(REAL, "the real typesafe-sdk 0.7.x is not installed for this Python")
class RealSdk(unittest.TestCase):
    def setUp(self):
        self.requests = []
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SIJAV_", "TYPESAFE_"))}
        env["TYPESAFE_API_KEY"] = KEY
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        m = mock.patch.dict(sys.modules, {"typesafe_sdk": SDK})  # this test's SDK is the real one
        m.start()
        self.addCleanup(m.stop)
        self.addCleanup(jev.CLIENT_OPTIONS.clear)

    def serve(self, respond):
        def handler(request):
            self.requests.append(request)
            return respond(request)

        jev.CLIENT_OPTIONS["transport"] = httpx2.MockTransport(handler)

    def test_a_judged_reply_through_the_real_sdk(self):
        self.serve(lambda r: httpx2.Response(200, json=GOOD, headers={"x-typesafe-request-id": "req_abc"}))
        reply = jev.call(STATE, QUESTIONS)
        [request] = self.requests
        body = json.loads(request.content)
        self.assertEqual(body["model"], "jev-latest")
        self.assertEqual(body["state"], STATE)
        self.assertEqual(body["questions"]["q1"]["instructions"], QUESTIONS["q1"]["instructions"],
                         "Noul instructions may be a JSON object (JSONContent)")
        self.assertEqual(body["questions"]["s"]["criteria"], ["low", "mid", "high"])
        self.assertEqual(request.headers["authorization"], f"Bearer {KEY}")
        self.assertEqual(request.extensions["timeout"]["read"], 60.0)
        self.assertNotIn("x-typesafe-retry-count", request.headers)
        self.assertEqual(reply["request_id"], "req_abc")
        self.assertEqual(json.loads(reply["raw_body"]), GOOD)
        self.assertEqual(reply["answers"]["s"]["probabilities"], {"0": 0.2, "1": 0.4, "2": 0.4})
        self.assertEqual(jev.answer_problems(QUESTIONS, reply), [])
        self.assertEqual(jev.shown(reply["answers"]["q1"]), "probability true 0.30")
        json.dumps(reply)

    def test_a_server_error_is_not_retried(self):
        self.serve(lambda r: httpx2.Response(503, json={"detail": "busy"}))
        with self.assertRaises(jev.JevCallFailed) as failed:
            jev.call(STATE, QUESTIONS)
        self.assertEqual(len(self.requests), 1, "the SDK's default would retry a 503 twice")
        self.assertEqual((failed.exception.status, failed.exception.outcome), (503, "answered"))
        self.assertIn("TypeSafeInternalServerError", str(failed.exception))

    def test_a_timeout_is_not_retried_and_its_outcome_is_unknown(self):
        def timeout(request):
            raise httpx2.ReadTimeout("read timed out", request=request)

        self.serve(timeout)
        with self.assertRaises(jev.JevCallFailed) as failed:
            jev.call(STATE, QUESTIONS)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(failed.exception.outcome, "unknown")
        self.assertIn("TypeSafeAPITimeoutError", str(failed.exception))

    def test_too_long_is_told_apart(self):
        self.serve(lambda r: httpx2.Response(400, json={"detail": {"error_type": "max_tokens_exceeded"}}))
        with self.assertRaises(jev.JevCallFailed) as failed:
            jev.call(STATE, QUESTIONS)
        self.assertTrue(failed.exception.too_long)
        self.assertEqual(failed.exception.status, 400)

    def test_an_unknown_answer_type_is_dropped_by_the_sdk_and_caught_as_missing(self):
        reply = json.loads(json.dumps(GOOD))
        reply["answers"]["c"] = {"type": "rank", "order": ["a", "b"]}
        self.serve(lambda r: httpx2.Response(200, json=reply))
        got = jev.call(STATE, QUESTIONS)
        self.assertIn("Jev did not answer ['c']", jev.answer_problems(QUESTIONS, got))

    def test_a_malformed_reply_is_an_answered_failure(self):
        self.serve(lambda r: httpx2.Response(200, json={"answers": {}}))
        with self.assertRaises(jev.JevCallFailed) as failed:
            jev.call(STATE, QUESTIONS)
        self.assertEqual(failed.exception.outcome, "answered")
        self.assertIn("TypeSafeAPIResponseValidationError", str(failed.exception))

    def test_a_malformed_key_is_refused_before_any_request(self):
        os.environ["TYPESAFE_API_KEY"] = "bad key with spaces"
        self.serve(lambda r: httpx2.Response(200, json=GOOD))
        with self.assertRaises(jev.JevCallFailed) as failed:
            jev.call(STATE, QUESTIONS)
        self.assertEqual(failed.exception.outcome, "not_sent")
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()

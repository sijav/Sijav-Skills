# TypeSafe Python SDK contract evidence

Checked October 1, 2026. This is public SDK contract research, not a review of
the Sijav implementation. No SDK import, credential read, client construction,
model request, package installation or source change was performed.

## Version and source

The installed distribution is **typesafe-sdk 0.7.1**, author TypeSafe AI,
license MIT, requiring **Python >=3.10**. Dependencies include `httpx2>=2.0.0`,
`pydantic>=2.12.0`, `pydantic-core>=2.41.1` and `tenacity>=9.0.0`.

Installed public source root:

`C:/Users/<user>/AppData/Local/Packages/PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0/LocalCache/local-packages/Python313/site-packages/typesafe_sdk`

The adjacent `typesafe_sdk-0.7.1.dist-info/METADATA` establishes version at line 3,
dependencies at lines 28–32, minimum Python at line 34, and the publisher's
[public repository](https://github.com/typesafe-ai/typesafe-sdk-python) at line 38.

The [official changelog](https://docs.typesafe.ai/sdk/python/changelog) lists
0.7.2, dated September 26, 2026, adding the HTTP/2 extra and its documentation.
It also records that 0.6.0 changed Score criteria from an integer-keyed dictionary
to an ordered sequence, and 0.7.0 changed serialization to Pydantic. Contract
facts below were checked against the installed 0.7.1 source rather than inferred
from older examples. If the package supports Python 3.9 without optional Jev,
its documentation must separately state that this SDK requires Python 3.10+.

## Client and request signatures

The installed `_core/client/sync/client.py`, lines 22–33, declares these
keyword-only constructor options:

```text
TypeSafeClient(*, api_key: str | None = None,
               model: str | None = None,
               retry: RetryPolicy | None = None,
               timeout: float | httpx2.Timeout | None = None,
               headers: Mapping[str, str] | None = None,
               transport: httpx2.BaseTransport | None = None,
               http_client: httpx2.Client | None = None,
               base_url: str | None = None)
```

The same file, lines 129–140, declares:

```text
system_one(state: JSONContent, questions: Mapping[str, Question], *,
           model: str | None = None, retry: RetryPolicy | None = None,
           timeout: float | httpx2.Timeout | None = None,
           extra_headers: Mapping[str, str] | None = None,
           extra_body: Mapping[str, JSONValue | None] | None = None,
           response_model: type[ResponseT] | None = None)
           -> SystemOneResponse | ResponseT
```

`state` and `questions` may be positional or named; the remaining arguments
are keyword-only. Questions may be SDK objects or raw dictionaries. Without
`response_model`, the result is `SystemOneResponse`. Per-call model, retry and
timeout settings override the client's settings. `extra_body` shallow-merges
last and can override the normal state, questions or model. The official
[synchronous client reference](https://docs.typesafe.ai/sdk/python/api/clients/sync)
documents these contracts.

## Retry and timeout controls

`RetryPolicy` is publicly exported from `typesafe_sdk.__init__`.
`_core/retry.py`, lines 52–86, defaults to two retries after the initial attempt,
0.5-second initial backoff, 5-second maximum backoff, 0.25 jitter, HTTP 408/429/5xx
retry eligibility, retry-after headers enabled, connection/timeout retries
enabled, and a 30-second retry budget. Lines 118–126 implement an attempt cap of
`max_retries + 1` and re-raise the last error.

**No SDK retry means `retry=RetryPolicy(max_retries=0)`**, at client construction
or on the call. There is no constructor argument named `max_retries`. Leaving
`retry=None` selects defaults, not zero retries. The
[retry reference](https://docs.typesafe.ai/sdk/python/api/retries) documents this
public control.

HTTP timeout is a separate option: positive finite float seconds or
`httpx2.Timeout`, default **10.0 seconds per HTTP operation** (`constants.py:21`,
`_core/config.py:36–39,63`). A supplied client's timeout is inherited when no
explicit timeout is supplied (`_core/client/sync/client.py:80–87`). The retry
budget uses Tenacity `stop_before_delay`; it does not interrupt an already
running HTTP operation. A float timeout should not be described as an absolute
wall-clock deadline for the entire workflow. See the
[client reference](https://docs.typesafe.ai/sdk/python/api/clients/sync) and
[constants reference](https://docs.typesafe.ai/sdk/python/api/constants).

## Question inputs

`_core/question_types.py:77–86` declares `Noul.instructions: JSONContent | None`.
This accepts a string, JSON object or array, and optional `None`. A structured
framing dictionary is therefore valid; stringification is not required.
`Noul.criteria` optionally describes `true` and `false` using the same content
types. Raw Noul dictionaries are defined at lines 26–35 and may provide a
structured `instructions` value with `type="noul"`.

Choice criteria are label-to-description mappings (lines 90–99). Score criteria
are a nonempty ordered sequence of descriptions, one per level starting at zero
(lines 103–112). All three support structured instructions. `_core/questions.py`
checks nonempty questions, required criteria for Choice/Score dictionary inputs,
and nonempty Score criteria. The
[question reference](https://docs.typesafe.ai/sdk/python/api/types/questions)
documents object and dictionary inputs.

## Answer shapes

`SystemOneResponse.answers` maps question ID to answer object. Cached `.nouls`,
`.choices` and `.scores` views filter it by answer class
(`_core/response_types.py:99–125`).

| Answer | Public fields |
| --- | --- |
| Noul | `type="noul"`, `noul: float`, meaning probability of yes |
| Choice | `type="choice"`, `choice: str`, `confidence: float`, `probabilities: dict[str, float]` |
| Score | `type="score"`, `score: float`, `confidence: float`, `legend`, `probabilities` |

For Score, JSON wire keys are strings, but the public SDK object converts
`legend` and `probabilities` keys to **integers**
(`_core/response_types.py:44–58`). The score is a probability-weighted expected
level and can be noninteger (`_schemas/models.py:107–131`). Noul has no separate
confidence field. Response model and usage are available separately. See the
[answer reference](https://docs.typesafe.ai/sdk/python/api/types/responses).

Parse success alone does not establish that every requested answer was returned:
`answers` defaults to an empty dictionary, and unknown future answer types are
removed from the typed view while remaining in the raw HTTP response
(`_core/response_types.py:79–109`). Callers must check requested IDs and expected
types. The source's float declarations do not by themselves validate semantic
truth or every probability invariant.

## Error classes and metadata

All classes below are exported publicly by `typesafe_sdk.__init__`.
`_core/errors.py:68–116` defines `TypeSafeError` as the base and
`TypeSafeAPIError` for HTTP errors. Its attributes are **`status`**, `body`,
`headers`, `endpoint` and `request_id`; the SDK attribute is not `status_code`.

| Class | Contract and source lines in `_core/errors.py` |
| --- | --- |
| `TypeSafeBadRequestError` | HTTP 400; 118–120 |
| `TypeSafeAuthenticationError` | HTTP 401; 122–124 |
| `TypeSafePermissionDeniedError` | HTTP 403; 126–128 |
| `TypeSafeNotFoundError` | HTTP 404; 130–132 |
| `TypeSafeUnprocessableEntityError` | HTTP 422; 134–136 |
| `TypeSafeRateLimitError` | HTTP 429, optional `retry_after_ms`; 138–145 |
| `TypeSafeInternalServerError` | HTTP 5xx; 148–149,198–200 |
| `TypeSafeAPIConnectionError` | No HTTP response; also subclasses `ConnectionError`; 152–153 |
| `TypeSafeAPITimeoutError` | Subclasses connection error and `TimeoutError`, has `timeout`; 156–163 |
| `TypeSafeAPIResponseValidationError` | Successful HTTP response with invalid required structure, has `field_path`; 176–185 |

The [exception reference](https://docs.typesafe.ai/sdk/python/api/exceptions)
confirms these public names. A connection error has no HTTP status to invent.
`_core/transport.py:79–91` maps HTTP transport timeout exceptions to the SDK
timeout class and other request failures to its connection class. API error
messages can contain server-provided body text; public class names and selected
metadata are more precise than assuming all exception attributes exist.

## Verification limits

These findings establish the installed SDK's signatures, serialization types,
retry implementation and public error interface. They do not establish service
availability, key validity, account model access, live answer correctness, or
whether any Sijav helper complies with the contract. No live Jev request was made.

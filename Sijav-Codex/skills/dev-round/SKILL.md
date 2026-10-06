---
name: sijav-codex-dev-round
description: "How to work through the code to-dos of an area in three passes: build every to-do (and the findings that turn up) with its tests, running none, until none is left; then test the area (the full suite at 100% coverage, the tests checked, failures fixed at their root) and mark what passed as tested, building whatever turns up and testing again; when all are done and tested, test each to-do as a real user, one end-to-end test per story, and mark it e2e tested. Use it when the owner calls $sijav-codex-dev-round or loads the dev rules ($sijav-codex-rules dev); never on your own."
---

# Development round

Codex orchestrates these passes. Claude Opus 5.5 writes implementation and
test code and performs code/technical review; native Codex agents handle the
other delegated work. If Claude is unavailable or its calls are postponed,
keep that work unfinished and report the exact blocker. Do not take over its
coding or technical review.

Work area by area: the front in general, or the back in general (a project may
name its areas). Each area goes through three passes in order, and goes back to
the first whenever work turns up. Done is not tested: when the project keeps its
to-dos on a board, each to-do has two statuses that start false, tested and e2e
tested.

## Responsibility and documentation

The assigned implementation agent executes tests only for its work area, never another stream's
or an unrelated project's work. The orchestrator coordinates and records
results; it does not execute tests or repeat tests the implementer already ran. Start
an area's full suite only after every source to-do and finding in that area
is complete.

Every change includes its documentation update. Keep the affected technical
documents and the application's Markdown/HTML How It Works descriptions and
process diagrams aligned with actual inputs, processing, storage, decisions,
actions and human intervention. Preserve the distinction between changed
source and behavior actually verified.
## 1. Build

- Take the area's to-dos, and every follow-up or finding that becomes a to-do
  on the way, until none is left.
- Build each one and write its tests with it. Test code only; don't write tests
  for rules, prompts or prose.
- Tests follow written scenarios from the user's point of view: real-world,
  logical, and many of them. Together they cover 100% of the area's code.
- Run no tests while building: not the tests you just wrote, not the ones near
  the code you changed, not coverage, not end-to-end runs, not planted faults,
  not the full suite. The worry that a change broke something else is what the
  test pass is for.
- The one exception: when the project closes a to-do by running its exit check
  (a loop's close command, for example), that one command runs, and nothing
  else. A close exit that runs the area's full suite is deferred to the
  test pass; it cannot override the requirement to finish every source
  to-do and finding first. Source-built closure records deferred runtime
  checks explicitly and creates no tested or E2E evidence.

## 2. Test

When none of the area's to-dos is left:

1. Run the area's full test suite, with coverage (it must reach 100%).
2. Check the tests themselves: each follows a real scenario, its steps and
   checks match what the user does and sees, and it fails when the behaviour it
   guards breaks (plant a fault to see it fail when in doubt). Fix a test that
   is wrong or illogical, and say which and why.
3. When a scenario fails, find which one and why its logic fails, and fix the
   root cause in the code. Never change a test just to make it pass; change it
   only when its scenario was wrong, and say so.
4. Mark every done to-do whose tests passed and that was never tested: tested.
5. Work the test pass turns up becomes to-dos: build them (1) and test again,
   until a test pass turns up nothing new.

## 3. Test as a real user

When all the area's to-dos are done and tested:

1. Test each to-do as a real user, in a real user scenario: what the person
   does, from where they start to what they see at the end; not just its exit
   check. To-dos with the same story share one end-to-end test.
2. Mark every to-do that test covers: e2e tested.
3. Whatever fails becomes a to-do: build it (1), test it (2) and test it as a
   real user again.

A to-do that changes no code (a decision, a document, research) has no tests:
mark it in each pass with how its result was checked.

Branches and pushes follow the project's phase: the dev rules, D5
($sijav-codex-rules dev).

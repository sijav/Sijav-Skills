---
name: dev-round
description: "How to work through a round of code to-dos: each to-do writes its code, its tests and an end-to-end test of its story from the user's point of view, and runs none of them; when every to-do of the area is done, run the area's tests (the full suite at 100% coverage, and the user's end-to-end scenarios), check that each test is correct and logical, and fix failing scenarios at their root. Use it when the owner calls /sijav-clauder:dev-round or loads the dev rules (/sijav-clauder:rules dev); never on your own."
---

# Development round

A round is one area's to-dos that were open when it started: the front in
general, or the back in general (a project may name its areas). Follow-ups
found during it wait for the area's next round.

## Each to-do

- Build it, and write its tests with it. Test code only; don't write tests for
  rules, prompts or prose.
- Tests follow written scenarios from the user's point of view: real-world,
  logical, and many of them. By the end of the round, together they cover 100%
  of the area's code, and the users' journeys end to end.
- Each to-do's story gets its own end-to-end test, from the point of view of
  the person in the story: what they do, from where they start to what they
  see at the end. Write it with the to-do; it runs with the rest when the
  round is done.
- Done is not tested. When the project keeps its to-dos on a board, each
  to-do also has two statuses that start false: tested, once its tests pass,
  and e2e tested, once it has been tested as a real user, in a real user
  scenario, not just its exit condition.
- Run no tests during the round: not the tests you just wrote, not the ones
  near the code you changed, not coverage, not end-to-end runs, not planted
  faults, not the full suite. The worry that a change broke something else is
  what the round-end pass is for.
- The one exception: when the project closes a to-do by running its exit check
  (a loop's close command, for example), that one command runs, and nothing
  else.

## When the round is done

When every to-do of the area's round is done:

1. Run the area's full test suite, with coverage (it must reach 100%) and the
   end-to-end scenarios.
2. Check the tests themselves: each to-do's story has its end-to-end test,
   and each test follows a real scenario, its steps and
   checks match what the user does and sees, and it fails when the behaviour it
   guards breaks (plant a fault to see it fail when in doubt). Fix a test that
   is wrong or illogical, and say which and why.
3. When a scenario fails, find which one and why its logic fails, and fix the
   root cause in the code. Never change a test just to make it pass; change it
   only when its scenario was wrong, and say so.
4. Mark each to-do on the board: tested once its tests pass, and e2e tested
   once it has been tested as a real user, in a real user scenario.
5. Then start the area's next round with its follow-ups.

Branches and pushes follow the project's phase: the dev rules, D5
(/sijav-clauder:rules dev).

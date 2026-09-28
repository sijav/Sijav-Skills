---
name: dev-round
description: "How to work through a round of code to-dos: build each one and run only the test files it changed; when the round is done, run the area's full test suite and fix failing scenarios at their root. Use it when the owner calls /sijav-clauder:dev-round or loads the dev rules (/sijav-clauder:rules dev); never on your own."
---

# Development round

A round is the to-dos that were open when it started. Follow-ups found during
it wait for the next round.

## Each to-do

- Build it, then test it. Test code only; don't write tests for rules, prompts
  or prose.
- Tests follow written scenarios: real-world, logical, and many of them.
  Together they cover 100% of the code.
- Run only the test files this to-do changed.

## When the round is done

- Run the area's full test suite (for example the whole server's).
- When a scenario fails, find which one and why its logic fails, and fix the
  root cause in the code. Never change a test just to make it pass; change it
  only when its scenario was wrong, and say so.
- Then start the next round with the follow-ups.

Branches and pushes follow the project's phase: the dev rules, D5 (/sijav-clauder:rules dev).

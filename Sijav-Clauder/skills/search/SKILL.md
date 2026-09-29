---
name: search
description: "A plain web search through codex, on gpt-6-luna at low effort: one question, a short answer, and the links it used. For a quick fact, a current version, an API detail or a doc page. A question that needs many sources and judgement is research, a separate skill."
---

Run from the project folder:

```sh
python "${CLAUDE_SKILL_DIR}/search.py" "What is the current stable version of X, and when was it released?"
```

- codex answers with its web search on, on `gpt-6-luna` at low effort unless you pass `--model` and `--effort`.
- The answer is a few sentences with the link of every page codex used, and it says so when it found nothing.
- The record goes to `<project>/.claude/searches/<time>-<slug>.md` and is printed.
- One question per search. Each search is a fresh codex conversation, so searches can run side by side.
- A search answer is a lead, not evidence: open the link and read the page before you rely on it.

## Switches and fallbacks

- `SIJAV_CODEX=off`: nothing is sent, and the command exits with code 3. Search with your own web tools instead, and say that codex was off.
- codex fails (not installed, or its allowance is used up): the search fails with codex's own words. For a used-up allowance, ask the owner to switch the codex account.

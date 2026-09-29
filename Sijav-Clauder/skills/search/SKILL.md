---
name: search
description: "Look something up on the web through codex: one question, answered with sources (link, date and an exact quote for each claim). For a quick fact, a current version, an API detail or a doc page."
---

Run from the project folder:

```sh
python "${CLAUDE_SKILL_DIR}/search.py" "What is the current stable version of X, and when was it released?"
```

- codex answers with its web search on, on `gpt-6-luna` at high effort unless you pass `--model` and `--effort`. A project's rules may choose other models.
- Every claim comes with its source's link, date and an exact quote, and the answer says what it could not confirm.
- The record goes to `<project>/.claude/searches/<time>-<slug>.md` and is printed.
- One question per search. A question that needs many sources and judgement is for the research skill.
- Nothing from outside counts as evidence until its source is read and quoted with its link: check the quotes you rely on before you use them.
- Each search is a fresh codex conversation, so searches can run side by side.

## Switches and fallbacks

- `SIJAV_CODEX=off`: nothing is sent, and the command exits with code 3. Search with your own web tools instead, and say that codex was off.
- codex fails (not installed, or its allowance is used up): the search fails with codex's own words. For a used-up allowance, ask the owner to switch the codex account.

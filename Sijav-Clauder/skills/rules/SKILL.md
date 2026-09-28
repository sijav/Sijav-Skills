---
name: rules
description: "The owner's rule sets: general, dev (development), design, and the current project's own rules. Load them only when the owner calls /sijav-clauder:rules (for example /sijav-clauder:rules dev); never load them on your own. Also holds the procedure for changing any rule."
---

# Rules

Sessions start with no rules. The owner calls this skill to load a set.

## Loading a set

- `/sijav-clauder:rules`: read `general.md` (in this folder).
- `/sijav-clauder:rules dev`: read `general.md` and `dev.md`, and use the
  sijav-clauder:dev-round skill that `dev.md` points to.
- `/sijav-clauder:rules design`: read `general.md` and `design.md`.
- Then look for the project's own rules: every `.md` file except `log.md` in
  `<project root>\.claude\rulesets\`. Read them too.
  (Not `.claude\rules\`: Claude Code may load that folder into every session.)
- In that project, a project rule wins over a general one it clashes with.
- Say in one line which files you loaded.

## Changing a rule

Every rule set has a `log.md` next to it: the history of each rule, with the
old text, why it existed, the owner's words and the evidence. The log is the
guard; nobody else has to approve a rule.

1. Find the rule by its ID (G1, D2, or a project rule's ID) and its file.
2. Read its entries in that folder's `log.md`.
3. If the change would bring back a problem recorded there, or goes against
   the owner's words, ask the owner first. Otherwise go ahead.
4. Back up the file first: copy it into a dated backup folder with a `.bak`
   ending, outside any `.claude` folder, so the copy can never load as a rule.
5. Edit it, and delete the old rule in the same edit. A rule lives in one
   place only.
6. Add a log entry: date, rule ID, old text, new text, the owner's words and
   the evidence.

Rules do not go into CLAUDE.md or memory files: those load into every
session.

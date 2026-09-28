# Sijav Skills

A Claude Code plugin marketplace with one plugin, **sijav-clauder**: six skills
that work in any project. None of them holds a project's rules, names or paths;
those live in each project (see "What a project adds").

**Website: https://sijav.github.io/Sijav-Skills/**, a guide to the six skills,
the rule sets, and how the loop and the roast work, with flowcharts.

```
Sijav-Skills/                     the marketplace (this git repository)
  .claude-plugin/marketplace.json
  Sijav-Clauder/                  the sijav-clauder plugin
    .claude-plugin/plugin.json
    skills/rules, dev-round, codex, roast, loop, todo
  site/build_site.py              builds the website from the skills' own files
  docs/index.html                 the website, served by GitHub Pages
```

## The skills

Plugin skills are called with the plugin's name first.

| Skill | What it does | When it loads | How to call it |
|---|---|---|---|
| rules | The owner's rule sets: general (G1 to G6), dev (D1 to D5), design (empty for now), plus the current project's own set. Also how to change a rule. | Only when the owner calls it | `/sijav-clauder:rules`, `/sijav-clauder:rules dev`, `/sijav-clauder:rules design` |
| dev-round | How to work through a round of code to-dos: only the changed tests during a to-do, the area's full suite at the end, root-cause fixes. | When the owner calls it or loads the dev rules | `/sijav-clauder:dev-round` |
| codex | Calls codex (GPT-6) through one session per kind of work; sol at medium effort unless told otherwise. | When a task needs codex | `/sijav-clauder:codex` |
| roast | codex checks a plan or finished work and frames typed questions, jev (TypeSafe) judges the logic, codex writes its reading. One record per run. | When a check is wanted | `/sijav-clauder:roast` |
| loop | Engages a project's own Stop-hook loop and follows its law: start, turns, pause, finish. | When the owner calls it | `/sijav-clauder:loop` |
| todo | A project board in a SQLite database, for projects that have `.claude/todo.db`. | In those projects | `/sijav-clauder:todo` |

## Install

Once, from any terminal:

```
claude plugin marketplace add sijav/Sijav-Skills
claude plugin install sijav-clauder@sijav-skills
```

Then start a new session, or run `/reload-plugins` in an open one. To pick up a
newer version later:

```
claude plugin marketplace update sijav-skills
claude plugin update sijav-clauder@sijav-skills
```

To work on the skills, clone this repository and add your clone instead:
`claude plugin marketplace add <path to your clone>`. Claude Code then runs the
skills from the clone (a skill's base directory is
`<your clone>/Sijav-Clauder/skills/<skill>`), so an edit takes effect in the
next session or after `/reload-plugins`. It also keeps a copy of the plugin
folder in its own cache, made at install and update time (seen on 2026-09-28:
every file in `Sijav-Clauder`, ignored ones included). So keep keys outside
`Sijav-Clauder`. To refresh that copy, raise `version` in
`Sijav-Clauder/.claude-plugin/plugin.json` and run
`claude plugin update sijav-clauder@sijav-skills`.

## Switches and fallbacks

Set these per user or per project in Claude Code's settings, under `env`.

| Setting | What it does |
|---|---|
| `SIJAV_JEV=off` | The roast runs without jev: codex reviews alone, and the record says why. The same happens by itself when there is no key, the TypeSafe SDK is missing, or jev fails during a run. |
| `SIJAV_JEV_KEY_FILE` | The file holding the TypeSafe key, outside the plugin folder: `keys/typesafe.key` in this repository, which git ignores. Without it: `TYPESAFE_API_KEY`. |
| `SIJAV_CODEX=off` | codex starts nothing: the runner and the roast exit with code 3, and Claude does the work itself. |

codex signs in with its own login, which the owner manages; nothing here holds a
codex token.

## What a project adds

The plugin holds none of this; each project keeps its own:

- **Its rules:** `.claude/rulesets/<name>.md` in the project root, read by
  `/sijav-clauder:rules` in that project. A project rule wins over a general one
  it clashes with.
- **Its loop:** a law file, `.claude/<name>-loop.local.md`, the hooks that give it
  back after each reply and after a compaction, and the command that starts it
  (for example one that makes the starting session the loop's only session). The
  law says how it pauses (a `.stop` file) and how it finishes.
- **Its board:** `.claude/todo.db`, for the todo skill.
- **Its records:** codex sessions (`.claude/codex-sessions/`) and roast records
  (`.claude/roasts/`) are written into the project, never into this folder.

## What it needs

- Python 3.13; the roast also needs the `typesafe-sdk` package and a TypeSafe key
  in the file `SIJAV_JEV_KEY_FILE` names (without one it runs with codex alone).
- The codex command-line tool, signed in by the owner (or `SIJAV_CODEX=off`).
- Node 22.13 or newer, for `todo.mjs` (or Python for `todo.py`).
- `uv`, to run the tests.

## Tests

From `Sijav-Clauder/skills`:

- `uv run --no-project --with pytest --with typesafe-sdk python -m pytest roast/test_roast.py codex/test_codex_session.py loop/test_compact.py -q`
- `python todo/test-parity.py`
- `node todo/test-subtasks.mjs`

## The website

https://sijav.github.io/Sijav-Skills/ is built from the skills' own files:
`python site/build_site.py` writes `docs/index.html`, which GitHub Pages serves
from the `docs` folder of `main`. Rebuild it and commit it after changing a
rule or a skill. The build refuses to write a page that names a project, a
person or a local path.

## Keeping it clean

- No project names, paths or project rules go into the skills; they belong to
  the project.
- Change a rule the way `rules/SKILL.md` says, and log it in `rules/log.md`. That
  log is the history of each rule, so it keeps the evidence it was written from.

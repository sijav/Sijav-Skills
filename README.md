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

## Before you install

Most of the skills need nothing more than Claude Code. Two need a service you
set up once: **codex**, which reviews work (the codex and roast skills), and
**jev** (TypeSafe), which judges it (the roast). Either can be switched off,
and the skills then work without it.

| You need | For |
|---|---|
| Python 3.13 | the roast, the codex runner, the loop's compaction step, `todo.py` |
| Node 22.13 or newer | the codex command line, and `todo.mjs` |
| codex, signed in | the codex and roast skills |
| a TypeSafe key, and the `typesafe-sdk` package | jev, in the roast |
| `uv` | running the tests |

### Set up codex

1. Install the codex command line: `npm install -g @openai/codex`.
2. Sign in once, yourself: run `codex` in a terminal and follow its sign-in.
   The skills never sign in for you and hold no codex token.
3. Check it: `codex --version` prints a version. The skills find `codex` on
   your PATH.

**Switch codex off** with `SIJAV_CODEX=off` (see "Where the settings go"). The
codex runner and the roast then start nothing and exit with code 3, and Claude
does the work itself. **Switch it back on** by removing the setting, or setting
it to `on`.

### Set up jev (TypeSafe)

1. Get an API key from TypeSafe (docs.typesafe.ai).
2. Save the key in a file of its own, **outside the plugin's folder**: Claude
   Code copies an installed plugin into its own cache, files and all. In a
   clone of this repository, `keys/typesafe.key` works, because git ignores it.
3. Install the TypeSafe library into the Python that runs the skills, the
   `python` on your PATH: `python -m pip install typesafe-sdk`. Check it with
   `python -c "import typesafe_sdk"`, which prints nothing when it works.
4. Tell the skills where the key is: set `SIJAV_JEV_KEY_FILE` to the key
   file's full path. Without it, the roast reads the key from
   `TYPESAFE_API_KEY`.
5. Check it end to end: run one roast (`/sijav-clauder:roast`). The record's
   jev line names the jev model that answered, or says why jev was not used.

**Switch jev off** with `SIJAV_JEV=off`. The roast then runs with codex alone,
and its record says why. It does the same by itself when there is no key, the
library is missing, or jev fails during a run. **Switch it back on** by
removing the setting, or setting it to `on`.

### Where the settings go

In Claude Code's settings, under `env`: your user settings
(`~/.claude/settings.json`) for every project, or a project's
`.claude/settings.json` for that project only. For example, with the key set
and codex switched off:

```json
{
  "env": {
    "SIJAV_JEV_KEY_FILE": "/full/path/to/typesafe.key",
    "SIJAV_CODEX": "off"
  }
}
```

`off`, `0`, `false` or `no` switches a service off; any other value, or no
setting at all, leaves it on. Start a new session after changing a setting.

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

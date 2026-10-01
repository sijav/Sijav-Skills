# Disposition of `dashboard-technical-review.md`

The dashboard was revised in its stage folder only. Tests used only boards
created by the real `todo.py` under the OS temp folder, with history in temp
folders (`SIJAV_TODO_DASHBOARD_HOME`). After the run,
`%LOCALAPPDATA%\sijav-todo-dashboard` does not exist, so the real user cache
was not touched. No real board, source dashboard, other stage, plugin
metadata or global config was opened or edited.

## Findings

### H1. A failed read showed "Nothing is pickable" and the old pick

**Fixed.**

- **Server** (`lib/board.mjs` `readNow` catch):
  - sets `queue.state = 'unavailable'` with the read error;
  - drops the previous order;
  - invalidates any picker run in flight by bumping and settling its
    generation.

  The snapshot then has no `headId`, `rankedIds`, `nextText` or `previous`.
  The next successful read reruns the picker.
- **Page** (`app.js` `pickerReport` / `queueStateNote`):
  - shows the pick panel only when `pickerReady(board)` is true;
  - otherwise shows "The board could not be read … next pick is unknown";
  - "Nothing is pickable" is gone, and "todo.py picks nothing" appears only
    for a current result.
- **Tests:**
  - `live.test.mjs` "a failed board read withdraws the pick and order;
    reading again restores them" (the board is renamed away and back);
  - `render.test.mjs` "after a failed read the main view shows no pick, no
    Next badge and no stale order";
  - `ui.test.mjs` "failed picker or unreadable board: no order, no pick, ID
    order";
  - `board.test.mjs` "a locked or hot board fails the read explicitly within
    the busy timeout".

### M1. An old result kept its "Next" badge and lane order

**Fixed.** `board-ui.mjs` `pickerOrder()` distinguishes three cases:

| Queue state | What is shown |
| --- | --- |
| Current result (`ready`) | Its order, pick and badges. |
| `checking` after a board change | The previous order, as "was #n · re-checking", with a banner. No pick, no head badge, no Next. |
| `error` / `unavailable` / no picker | No order: ID order with a banner. |

**Tests:** `ui.test.mjs` (the previous-order and failure cases),
`render.test.mjs` (failure state), and `live.test.mjs` (a broken `todo.py`
withdraws the pick).

### M2. Legacy rows broke the picker

**Fixed** in `picker.py`.

- **Order:** the head, started list and eligible order come from `choose()`
  calls only (the sequence of its picks).
- **Ranking:** `by_rule()` is called per task inside `try`. Failures go to
  `rankErrors`, those tasks are listed after ranked ones, and the pick is
  unaffected.
- **Test:** `picker.test.mjs` "legacy rows the tool never ranks … do not cost
  a valid pick". The board has no CHECK constraint, a blocked `urgent` task
  and a legacy status `open`, and the output equals the real `todo.py next`.

### M3. The entry check failed through symlinks and junctions

**Fixed.** `server.mjs` `isEntry()` compares `realpathSync.native` of both
paths, case-folded on Windows.

**Tests:** `live.test.mjs` CLI test. `--help` and a real start both work
through a directory junction, and the `isEntry` unit checks pass. A `subst`
drive is not tested; it goes through the same realpath code.

### M4. `npm start` searched for the board from the install folder

**Fixed.** `callerDirectory()` uses `INIT_CWD` when `npm_lifecycle_event`
is set.

**Test:** `live.test.mjs` runs the real
`npm --prefix "<install>/dashboard" start -- --port 0` through the shell from
a project subdirectory and finds that project's board. A simulated-environment
check runs too.

### M5. A `$` in the project path broke the page

**Fixed.** The settings are inserted with a function replacement:
`.replace(token, () => json)`.

**Tests:** the project name in `live.test.mjs` and `render.test.mjs` contains
`$'`, `$&` and `$$`. The render test now uses the settings embedded by the
real server, and its real WebSocket messages, rather than settings built by
the test.

### M6. "Read only" still wrote into the project; one bad journal line lost everything

**Fixed.**

- **History location:** the default is a per-user cache folder,
  `<root>/<project>-<sha256(realpath db)[:16]>`, where the root is
  `SIJAV_TODO_DASHBOARD_HOME`, `%LOCALAPPDATA%`, `~/Library/Caches` or the
  XDG cache. The path is printed at start and shown on the page. `--data-dir`
  is still supported, and a folder inside the installed skill is refused.
- **Journal recovery:** the journal is parsed line by line. Bad or torn lines
  are skipped and reported with their line numbers, and all valid entries are
  kept. A missing final newline is written before the next append.
- **Tests:** `board.test.mjs` (history location; torn and corrupt journal)
  and `live.test.mjs` (project folder listing unchanged; history root printed
  and persists across restart).

### M7. Test helpers pointed at the stage

**Fixed.** `tests/helpers.mjs` `todoDir()` uses `TODO_SKILL_DIR` if set,
otherwise `<dashboard>/../todo.py`, otherwise an explicit error. No stage
path is built in. The demo uses the same resolution.

**Test:** `live.test.mjs` imports the helpers inside a temp install without
`TODO_SKILL_DIR` and gets `<install>/skills/todo/todo.py`. The installed demo
serves with that `todo.py`.

### M8. A test or the demo could open a real board

**Fixed.**

- **Guard:** `tempDir()` refuses any temp folder that has a board above it.
  The no-board CLI test also asserts `findBoard(nowhere) === null` before
  spawning.
- **Child cleanup:** every spawned child is tracked and killed in
  `test.after`, with its whole process tree on Windows (`taskkill /T /F`).
  `stop()` also no longer waits forever on an already-signalled child; that
  bug caused a hang during development.
- **Demo:** it refuses `--db`, `--project`, `--data-dir` and `--todo-py`
  (exit 2) and keeps its history inside its own temp fixture.
- **Test:** `live.test.mjs` M7/M8 test.

### M9. Every push resent the whole history

**Fixed, with one part not fully addressed.**

- **WebSocket:** on connect it sends `reset:true` with the newest page
  (`changePageSize`) and `changesComplete`. Later pushes carry only entries
  with `seq` above what that socket has been sent.
- **`/api/changes`:** pages with `before`/`after`/`limit` (or `limit=all`),
  and `item=<id>` returns one task's full history.
- **Page:** "Show more" and "Load all history" fetch older pages, and the task
  drawer offers "Load this task's full history". Messages received while
  paused are all kept and applied on resume.
- **Coalescing:** pushes in one event-loop turn are merged (`setImmediate`).
- **Transitions:** status transitions are kept incrementally in a slim form,
  without before/after rows.
- **Test:** `live.test.mjs` checks:
  - later pushes are `reset:false` and carry only new `seq` values;
  - each entry is sent once;
  - one change produces at most 4 pushes;
  - pages are contiguous;
  - `item=` filtering works.
- **Not fully addressed:**
  - Each push still carries the full board snapshot (all tables). That is
    bounded by board size, not by history.
  - The slim transitions list is resent whole on each push. It grows with
    history, but only about 150 bytes per status change.

### M10. Node version claim to verify

**Verified and fixed.** Sources: the official Node docs, `sqlite.md` raw
YAML.

| Feature | Added in |
| --- | --- |
| `timeout` option | v24.0.0 and v22.16.0 (PR #57752); in no 23.x release |
| URL/Buffer `path` | v23.10.0 and v22.15.0 |
| Unflagged | v22.13.0 |

- **`engines`:** now `"^22.16.0 || >=24.0.0"`.
- **Runtime:** `server.mjs` `nodeSupported()` refuses other versions with
  exit 2.
- **Test:** unit cases in `live.test.mjs`.
- **Not tested:** the suite ran only on Node v24.16.0; no older Node binary
  was exercised.

## Low (hardening)

### Dropped constants

**Fixed.** Every top-level statement of `todo.py` is classified:

- an upper-case assignment that is a literal or a pure expression is
  executed, for example `POINTS = POINTS + [21]`;
- one computed by a call is refused explicitly;
- a skipped start-up statement that reads or changes a compiled name is
  refused explicitly (for example `POINTS.append(21)`);
- missing required functions, or no `next` branch, are refused.

**Test:** `picker.test.mjs` "an unknown start-up change … is refused
explicitly".

### Real board path given to the tool

**Fixed.** The board is copied with SQLite's backup into a temp project
(`<tmp>/<project name>/.claude/todo.db`). The tool's code runs there with
`BOARD` set to the copy and the working directory inside the copy; the picker
process itself starts in the OS temp folder. The copy is deleted afterwards.

**Test:** a modified `todo.py` whose `next` writes beside `BOARD` and in its
working directory leaves the project, the board folder and the tool folder
unchanged.

### Mislabelled sort keys

**Fixed.** The labels are `ast.unparse` of each element of `by_rule()`'s
single returned tuple. If `by_rule()` does not return a plain tuple, values
are shown as "component n" and the panel says it has no labels.

**Tests:** labels are asserted in `picker.test.mjs` and `render.test.mjs`. A
modified `-task["points"]` shows up as its own label.

### Policy copied, not derived

**Fixed for status groups and severity order. Partly for reason wording.**

- **Defaults:** `defaults.json` no longer has any `rules`.
- **Status groups:** probed per status with rolled-back savepoint rows on the
  copy, using the tool's own `choose()` (started, offered, satisfies a
  parent) and `open_children()` (closed).
- **Severity order:** comes from `SEVERITIES`.
- **Explanations:** the order explanation comes from the tool's docstrings and
  `by_rule()` labels. Fixed policy prose ("highest severity…", "counts as
  now", "every objective is met") was removed.
- **If policy is unknown:** no Python, a changed `todo.py`, or a failed probe
  all leave nothing grouped. Counts show "—", lanes are in ID order, and a
  banner gives the reason. A policy is valid only while the `todo.py` sha256
  matches.
- **Tests:**
  - `picker.test.mjs`: a changed `choose()` makes `wait_for_roast` "other"
    and changes the order;
  - `live.test.mjs` policy watch: the change is pushed live;
  - `render.test.mjs`: the no-Python render claims no groups or counts.
- **Reason wording, not fully derived:** the facts in the "not pickable"
  reasons come from the tool (`block_of()` text, parent statuses against the
  probed satisfying statuses, and offered statuses). The sentence wording
  ("Waits on X (status): title", "Status X: todo.py next does not offer…") is
  the dashboard's. Each reason set is then checked by a rolled-back `choose()`
  probe, and shown as verified or not verified.

### Atomic replacement on Windows

**Not fixed. Documented.** SQLite opens files without `FILE_SHARE_DELETE`,
so another tool's rename over the board can fail while the dashboard or
picker briefly has it open. The test still retries the rename.

### Missing frame and Sec-Fetch protections

**Fixed.**

- **Headers:** CSP `frame-ancestors 'none'; form-action 'none'`,
  `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`.
- **Cross-site requests:** `Sec-Fetch-Site` other than `same-origin`/`none`
  gets 403 on HTTP and on the WebSocket upgrade.
- **Test:** a cross-site `/api/queue/recheck` gets 403.

### Empty first read

**Fixed.** When a board has never been read successfully, the report and
board views show only the error panel ("The board could not be read"), never
zero counts.

**Not tested:** this path is not covered by a dedicated test. The H1 tests
cover a failure after a successful first read.

## "Tests that only check the dashboard against itself"

- **Same driver:** **fixed**. Row preservation is now checked against
  Python's `sqlite3`.
- **Settings injection:** **fixed**. The render test uses the real server's
  page and WebSocket messages.
- **Runtime coverage now added:** the `todo.py` watch, a failed read in the
  UI, a picker error with an old result, legacy severities, and restart
  history.
- **Order tests:** UI order tests still use made-up `rankedIds` as unit tests.
  The real order is checked against `todo.py next` in `picker.test.mjs` and
  through lanes in `render.test.mjs`.
- **No-polling check:** still partly a source regex. `live.test.mjs` also
  measures a 1.5 s quiet period with no messages and no rereads.
- **Hot `-journal`:** **not specifically tested**. A locked board is tested.
- **Browser reconnect after a server restart on a fixed `--port`:** **not
  tested**. With `--port 0` a tab cannot reconnect after a restart; this is
  documented.

## Post-review findings (N1, N2)

`dashboard-postreview.md` was empty when read, so these follow the root's
description of the two findings.

### N1. Inherited npm `INIT_CWD` could select the wrong board

**Fixed.**

- **Server:** `server.mjs` `callerDirectory(env, cwd, root)` uses `INIT_CWD`
  only when all of these hold:
  - `npm_lifecycle_event`, `INIT_CWD` and `npm_package_json` are set;
  - the folder of `npm_package_json` is this dashboard's folder (by real
    path, case-folded on Windows);
  - the process runs in that folder.

  Otherwise the real current directory is used, and `--project`/`--db` still
  decide.
- **Tests:** `tests/helpers.mjs` `cleanEnv()` removes every `npm_*` variable
  and `INIT_CWD` from every child: dashboards, demo, `todo.py`, and the
  helper import. `testEnv()` builds on it. The helpers also check the temp
  folder's ancestors for a board once at load. `todoWith()` refuses to run
  `todo.py` outside the temp folder.
- **Regression** (`live.test.mjs`, "npm variables inherited from an unrelated
  npm process never select another board (N1)"):
  - Setup: two synthetic temp boards, plus an unrelated `package.json` in temp.
    The inherited environment names that package and points `INIT_CWD` at the
    other board.
  - Started from the project: the caller's own board is used.
  - Started from the install folder with `--project`: the named board is used.
  - Started from the install folder with no options: exit 2 "No board",
    never the foreign board.
  - Unit cases cover each condition of `callerDirectory`.
  - The real `npm --prefix <install> start` test still finds the caller's
    board.
- **The suite was run through the real `npm test`** (below), so npm's own
  variables were present in the test process and stripped from its children.

### N2. The reason check omitted parents that were already satisfied

**Fixed.**

- **Change** (`picker.py` `confirm()`): the rolled-back probe now gives
  `choose()` the task plus every task whose status satisfies a parent, after
  the probe's own updates. Already-done parents therefore count as done.
  Picks other than the task (and started tasks) are set aside and `choose()`
  is asked again, as for the eligible order.
- **Regression** (`picker.test.mjs`, "a blocked task with a done parent and
  an unfinished parent … (N2)"):
  - Task MP-020 has parents MP-006 (done) and MP-001 (backlog), and an
    explicit block.
  - Its reasons are exactly blocked, plus parent MP-001 only, plus a
    verified check.
  - On an isolated duplicate, the real helper resolves exactly those reasons
    (`move MP-001 done`, `move MP-020 backlog`). The picker then lists MP-020
    as eligible, and its output equals the real `todo.py next`.
  - With the one critical, 1-point peer that has a lower ID set aside, the
    real `todo.py next` prints MP-020.

## Exact test command and result

**Current run** (after N1/N2), through the real npm script:

```
cd C:/Users/<user>/Documents/Codex/2026-09-30/ther/work/sijav-todo-dashboard-stage
TODO_SKILL_DIR=../sijav-codex-stage/skills/todo npm test > test-output.txt 2>&1
```

- **What it runs:** `npm test` runs `node --test --test-concurrency=1 "tests/*.test.mjs"`.
- **Result:** exit 0; tests 52, pass 52, fail 0, cancelled 0, skipped 0;
  duration_ms 110195.1. That is the 50 earlier checks plus the N1 and N2
  regressions. The complete output, including npm's notice lines, is in
  `test-output.txt`.
- **Before the full run:** the N1 and N2 tests were run on their own while
  developing. The N2 test first failed on a wrong expectation (the head was
  MP-003, an equally ranked peer with a lower ID); the assertion was
  corrected to check eligibility.

**Previous run** (before N1/N2): the same suite through
`node --test … --test-reporter-destination=test-output.txt`. Exit 0, 50/50,
duration_ms 90240.9; that output has been replaced by the current run.
- **Environment:** Node v24.16.0 and Python 3.13.14 from PATH, on Windows 11.
- **Ad hoc checks during development:**
  - a `picker.py` smoke run: output byte-identical to the real `next`, board
    unchanged, about 260 ms;
  - CLI restart reproductions;
  - a stage listing.

  None of these touched a real board.

## Command interface (for integration)

```
node <skills>/todo/dashboard/server.mjs [--project <dir>] [--db <file>] [--port <n>] [--host 127.0.0.1|localhost|::1]
                                        [--data-dir <dir>] [--python <exe>] [--todo-py <file>] [--help]
npm --prefix <skills>/todo/dashboard start -- [same options]
npm ci --omit=dev --prefix <skills>/todo/dashboard        # once, installs pinned ws 8.21.3
node <skills>/todo/dashboard/tests/demo-fixture.mjs [--port <n>]   # disposable visual-QA fixture
npm --prefix <skills>/todo/dashboard test                 # installed: uses ../todo.py; staging: set TODO_SKILL_DIR
```

| | |
| --- | --- |
| Environment variables | `SIJAV_TODO_PYTHON` (Python), `SIJAV_TODO_DASHBOARD_HOME` (history root). Tests also use `TODO_SKILL_DIR` and `TODO_DASHBOARD_WS_DIR`. |
| Exit codes | 2 for a usage, board, port-in-use, `ws`-missing or Node-version error; 1 for an unexpected start failure. |
| Endpoints | `/api/live` (WS), `/api/snapshot`, `/api/changes`, `/api/export`, `/api/health`, `/api/queue/recheck` (GET/HEAD only). |

## Files in this tree

Copy everything except `node_modules/`:

- `server.mjs`, `lib/board.mjs`, `picker.py`, `defaults.json`
- `package.json`, `package-lock.json`
- `README.md`, `review-disposition.md`, `test-output.txt`
- `public/`: `index.html`, `app.js`, `board-ui.mjs`, `ambient-ui.mjs`,
  `work-context.mjs`, `display-mode.mjs`, `styles.css`, `theme.css`,
  `ambient.css`, `favicon.svg`
- `tests/`: `helpers.mjs`, `demo-fixture.mjs`, `board.test.mjs`,
  `picker.test.mjs`, `live.test.mjs`, `ui.test.mjs`, `render.test.mjs`

## Remaining limits and unresolved items

- **Visual QA of new states is pending:** the previous-order and failure
  states, unknown-policy rendering, history loading and the "How todo.py
  decides" panel were checked headlessly only. The earlier browser QA covered
  the build before these fixes.
- **`--port 0` changes on every restart**, so an open tab does not recover.
  A stable address needs an explicit `--port`.
- **Python 3.9+ is required for order and status meaning.** `todo.mjs`
  cannot be used for this.
- **Policy probes depend on the tool's own pieces:** `choose`,
  `open_children`, `block_of`, `all_tasks`, `children`; the
  `blocked(task, reason, since)` and `blocked_by(task, parent)` tables; and
  `SEVERITIES`/`POINTS` for probe rows. If these change incompatibly, the
  policy is reported as unreadable rather than guessed. The reason check
  removes a block by deleting its `blocked` row on the copy. If the tool
  stores blocks elsewhere, that check reports "not verified".
- **Pushes still carry the full board snapshot**, and the slim transitions
  list is sent whole (see M9).
- **One dashboard per history folder; no lock.** Concurrent dashboards on one
  history folder can duplicate or interleave journal entries and overwrite
  each other's baseline. Writes are append-then-rename, not crash-proof: a
  torn last line is skipped and reported, and a lost baseline restarts the
  comparison. No concurrency or crash guarantee is claimed.
- **The npm caller rule relies on npm 7+ setting `npm_package_json`.** Older
  npm versions do not, so their `npm start` falls back to the package folder,
  where there is normally no board; pass `--project` instead.
- **Windows rename-over-board can fail briefly** while the board is open (no
  `FILE_SHARE_DELETE`).
- **History gaps:** history covers only what the dashboard saw while running,
  and edits between reads may merge. If the board's `.claude` folder is
  removed and recreated, the dashboard must be restarted.
- **Only Node v24.16.0 was run.** The 22.16+ support claim rests on the
  official docs, not on a test run.
- **Not tested:** `subst` drives, a hot `-journal`, the empty-first-read UI
  path, and browser reconnect after a restart.

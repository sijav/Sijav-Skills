# To-do board dashboard (read only)

A live, read-only browser view of a project's to-do board: the to-do skill's
`.claude/todo.db`, or a loop board opened with `--db` (see [Loop boards](#loop-boards)).
It shows:

- the full report and the full board, in the tool's own next order;
- every task's complete record;
- changes as per-field line diffs, and every table;
- a full-screen Relax display.

It works for any project that uses the to-do skill, and for any loop board.
Any other SQLite file is refused with the tables it has; it is never shown as
an empty board.

Installed layout:

```
<skills>/todo/
  todo.py, todo.mjs, SKILL.md      the unchanged board tools
  dashboard/
    server.mjs                     CLI, HTTP and WebSocket
    lib/board.mjs                  board lookup, read-only reader, watcher, change history
    picker.py                      runs todo.py's own next and policy on an isolated copy
    loop_picker.py                 runs a loop board's own order and policy on an isolated copy
    defaults.json                  UI labels and limits only (no board policy)
    public/                        browser files
    package.json, package-lock.json
    tests/                         fixture tests and the demo fixture
```

For the to-do skill's board the dashboard uses `../todo.py`, found relative to
its own folder, so it works from any install or cache location. `--todo-py`
overrides it. For a loop board it uses the board's own tool: the `.py` named
after the board file, beside it, or `--tool`. Nothing machine-specific is
shipped.

## Requirements and install

- **Node 22.16+ on the 22 line, or Node 24+.** `node:sqlite`'s busy `timeout`
  option was added in v22.16.0 and v24.0.0 (no 23.x release has it), and URL
  paths in v22.15.0. Sources: the Node API docs, `sqlite.md` "History" for
  `new DatabaseSync()`, PR #57752. Older versions are refused at start.
- **Python 3.9+**, which `todo.py` needs too. Without it the board is still
  shown in full, but the page says the next order and status meaning are
  unknown, lists tasks by ID, and groups nothing as Doing or Done. Nothing is
  guessed.
- **The `ws` package**, pinned in `package-lock.json`. Install it once, inside
  the dashboard folder only:

  ```
  npm ci --omit=dev --prefix "<skills>/todo/dashboard"
  ```

  This never touches a project's own packages. If `ws` is missing, the server
  exits with code 2 and prints this command.

## Start it

From the project, or any subdirectory of it:

```
node "<skills>/todo/dashboard/server.mjs"
node "<skills>/todo/dashboard/server.mjs" --project "D:/path/to/project" --port 8765
node "<skills>/todo/dashboard/server.mjs" --db "<subfolder>/<board>.db" --port 8765
npm --prefix "<skills>/todo/dashboard" start -- --port 8765
```

**`npm start`:** npm runs scripts from the package folder, so the dashboard
looks for the board from the directory npm was called in (`INIT_CWD`), not
from its install folder. `INIT_CWD` is trusted only when npm is running this
package's own script: `npm_package_json` must name this dashboard's
`package.json` (by real path), and the process must run in this folder.
npm variables inherited from any other npm process (a parent `npm test`, a
script of another package) are ignored. The current directory, `--project` or
`--db` decide instead.

**Through links:** the server also starts when called through a symlink,
directory junction or `subst` drive.

| Option | Meaning |
| --- | --- |
| `--project <dir>` | Start the board lookup here instead of the current directory. The nearest **existing** `<dir or ancestor>/.claude/todo.db` is used, the same walk as `todo.py`. A loop board is never looked for: `--db` names it. |
| `--db <file>` | Use this board file, relative to the current directory. It must exist; nothing is ever created. A loop board is always opened this way. Give `--db` more than once to show several boards on one page (see [Several boards](#several-boards)). |
| `--port <n>` | Default `0`: the OS picks a free port. **That port changes on every start**, so an open tab cannot reconnect after a restart. Pass a fixed `--port` for a stable address. A port in use is reported (exit 2). |
| `--host <addr>` | `127.0.0.1` (default), `localhost` or `::1`. Non-loopback addresses are refused. |
| `--data-dir <dir>` | Where change history is kept. Default: a per-user cache folder keyed by the board (below). A folder inside the installed skill is refused. |
| `--python <exe>` | Python for the picker. Default: `SIJAV_TODO_PYTHON`, then `python`, `py -3`, `python3` on Windows (`python3`, `python` elsewhere). |
| `--todo-py <file>` | The `todo.py` whose order and policy are used. Default `../todo.py` beside the dashboard folder. |
| `--tool <file>` | For a loop board: the tool whose order and policy are used. Default: the `.py` named after the board file, beside it (`<name>.db` → `<name>.py`). |
| `--help` | Usage. |

**No board:** with no board at or above the start directory, the server
prints `No board. Nothing at or above … has .claude/todo.db.` and exits with
code 2. It does not fall back to another project and creates
nothing. A missing `--db` file is refused the same way, and so is a file that
is not a board (exit 2, naming the tables it has).

**Startup output** (from a fixture run):

```
Sijav to-do dashboard · read only
URL:        http://127.0.0.1:54383/  (port chosen by the OS; it changes on restart, pass --port for a stable address)
Project:    …\my project
Board:      …\my project\.claude\todo.db  (nearest .claude/todo.db at or above …\my project\apps\web app)
Read-only:  yes · SQLite read-only/query-only connections · no mutation API · board never written
Watching:   …\my project\.claude for todo.db, todo.db-wal, todo.db-shm, todo.db-journal; tool …\skills\todo\todo.py
Live push:  WebSocket /api/live on file events · no polling
Picker:     todo.py …\skills\todo\todo.py via …\python.exe · checking
History:    C:\Users\<you>\AppData\Local\sijav-todo-dashboard\my-project-<16 hex>
No task loop is started. Ctrl+C stops the dashboard.
```

## Change history location

History lives outside the project and outside the skill, in a per-user cache
folder named `<project>-<hash of the board's real path>`, so each board keeps
its own history. The cache root, in order of precedence:

| Where | Folder |
| --- | --- |
| Environment variable | `SIJAV_TODO_DASHBOARD_HOME` |
| Windows | `%LOCALAPPDATA%\sijav-todo-dashboard` |
| macOS | `~/Library/Caches/sijav-todo-dashboard` |
| Other | `$XDG_CACHE_HOME/sijav-todo-dashboard` or `~/.cache/sijav-todo-dashboard` |

It holds two files:

- `baseline.json`: the last successful read.
- `changes.jsonl`: one change per line.

**Damaged lines:** an unreadable or torn line (for example after a crash) is
skipped and reported on the page. Every other entry is kept, and new entries
start on a fresh line. The path is printed at start and shown on the page.

## Endpoints

All endpoints are GET/HEAD only; any other method returns 405. These requests
return 403:

- a `Host` other than the loopback host of the actual port;
- a foreign `Origin`;
- `Sec-Fetch-Site: cross-site`.

The page sets `frame-ancestors 'none'` and `X-Frame-Options: DENY`.

| Path | Content |
| --- | --- |
| `/` and static files | The page, with UI settings, project and board path embedded (inserted literally; `$` in paths is safe). |
| `/api/live` | WebSocket, the page's only data path (see [Paged queries](#paged-queries)). On connect it sends `hello`: each board's details, nothing per task. The page then asks typed questions and gets one reply each. After each file-event change it pushes `changed`: the boards whose details changed, the keys of the tasks that changed, and the history entries not yet sent. History is never resent. |
| `/api/snapshot` | The whole current snapshot, with an ETag, for tools and exports. The page never loads it. |
| `/api/changes` | History, newest first. `?limit=<n>` (default one page) or `?limit=all`; `?before=<seq>` gives older pages; `?after=<seq>` gives newer entries; `?item=<task id>` gives one task's full history. Returns `{changes, total, complete, latestSeq}`. |
| `/api/export` | Snapshot plus the full history, as a download. |
| `/api/health` | `readOnly`, project, db, data dir, Python, todo.py and its hash, and watch state. |
| `/api/queue/recheck` | User action ("Run next again"): rereads the board and reruns the picker once. |

## How "next" and status meaning are decided

`picker.py` never imports or runs `todo.py`, because importing it runs schema
migrations and a command. It parses `todo.py` and classifies every top-level
statement:

- **Compiled:** imports, functions, constants (literals or pure expressions),
  the additive schema statements, and the body of its `next` command.
- **Replaced:** `BOARD = …` and `db = sqlite3.connect(…)`.
- **Skipped:** CLI start-up statements, but only if they neither define nor
  change anything the compiled code uses.
- **Refused:** a constant computed by a call, or a start-up statement that
  touches a compiled name. The page then shows no order rather than one built
  from stale constants.

**Isolated copy:** the board is copied once, read-only (`mode=ro`,
`query_only`; an idle WAL board opened `immutable`), with SQLite's backup
into a temporary project folder. The tool's code runs only there, with `BOARD`
pointing at the copy and the working directory inside it. The real board path
is never given to the tool's code, and the copy is deleted afterwards.

**Order:** the order is the sequence of picks `choose()` makes:

1. started work first, as `choose()` returns it;
2. then repeatedly the task `choose()` picks once the previous picks are set
   aside;
3. then the tasks it cannot pick.

The page shows the exact output of `todo.py next`, byte for byte. As a
self-check, the order must reproduce `choose()`'s pick.

**Status groups:** the groups are probed, not configured. Throwaway rows
inside a rolled-back savepoint on the copy ask the tool which statuses
`choose()`:

- resumes first (Doing);
- offers as new work;
- treats as satisfying a parent;

and which statuses `open_children()` treats as closed. Done means satisfies a
parent and closed; dropped means closed only. Severity order comes from the
tool's `SEVERITIES`. Sort-key labels come from `by_rule()`'s own return
expression, and docstrings are shown as the tool's own description.

**Reasons a task is not offered:** each reason comes from the tool: its
`block_of()`, its parents' statuses, or a status `next` does not offer. A
rolled-back probe then checks that resolving those reasons makes the task
pickable, and the page says whether that check passed.

**Legacy rows:** a row `by_rule()` cannot rank (for example an old severity
on a board without a CHECK constraint) is listed after ranked tasks with the
error. It never costs the pick, just as the real tool ignores it.

**Old schema:** the tool's additive schema runs only on the copy, and the
page says so.

**When the result is not current:**

| State | What is shown |
| --- | --- |
| Board changed, check running | The previous order, labelled "was #n · re-checking". No pick, no Next badge. |
| Picker failed, or board unreadable | No order, no pick and no `next` text. Tasks in ID order, with the reason. |
| `todo.py` changed | The policy is read again; until then nothing is grouped. |

Node and Python each digest the rows they read; a result for a different
snapshot is discarded and recomputed.

## Loop boards

A loop board is an SQLite file with an `item` table (at least `id`, `title`,
`status`, `severity` and `priority`) and a `dep` table of blockers (`item`,
`blocker`). Its own tool, a Python file, writes it.

**Opening it:** a loop board is never looked for; name it with `--db`. It
often sits in a subfolder of its project: start the dashboard from the
project with `--db <subfolder>/<board>.db`, and the page is named after the
project. Its tool is the `.py` named after the board file, beside it
(`<name>.db` → `<name>.py`), or `--tool`.

**Order:** `loop_picker.py` imports the tool, never runs it, and calls
its `board_order(conn)`. Without `board_order`, it sorts `open_items(conn)` by
`sort_key`, with `blocked_ids()` and `parked_ids()` not startable. These
functions may live in a module the tool imports from its own folder. They run
only on an isolated copy, as with `picker.py`, so importing the tool must not
touch a database.

**What `next` adds is not run:** a loop tool's `next` may also check the
machine before it hands out an item (for example, whether a database it needs
is ready). Those checks run commands and read files, which a read-only view
must not do. The page shows `board_order()`'s order and names it so.

**Status meaning** is probed on throwaway rows in a rolled-back savepoint:

- the item table's default status is new work;
- a status `open_items()` no longer lists is closed;
- a status that leaves a blocked item startable satisfies a blocker;
- any other status the tool offers counts as started (Doing).

Severity order comes from the tool's `SEV_RANK`, else from the table's CHECK
list.

**Reasons an item cannot start:** an open blocker ("Waits on 12 (open): …")
or a parked note ("Parked: …"). A rolled-back probe clears them and checks
that the tool then offers the item.

**Findings:** rows of a `finding` table count as open while they keep the
table's default status. Rows of any table with an `item` column are shown
with their item.

**On the page:** the tool is named by its file name and its `board_order()`.
The drawer shows the item's own fields (`created_at`, `closed_at`,
`occurrences`; story, why, exit command, what was done at close) and its
blockers. It leaves out the to-do skill's sections: area and type,
objectives, notes, roasts, and findings filed as tasks.

## Several boards

Two sessions on one project often keep two boards, a loop board and a to-do
board say. Give `--db` once per board and one page shows them all:

```
node "<skills>/todo/dashboard/server.mjs" --db "<subfolder>/<loop board>.db" --db ".claude/todo.db" --port 8765
```

- Each board is read with its own tool and keeps its own change history, under
  its own id: `board` for the first, `board-2` and on for the rest. A board
  shown alone keeps `board`, so its history and links do not change.
- The board list switches between them; "All" shows every board, with each card
  naming its board. Every board's lanes, picks, reasons and task drawer are in
  its own tool's words.
- Changes from every board share one numbering, so the Changes view, paging and
  live pushes work across boards. A task's own history is asked for by its key,
  `<board id>:<task id>`, since two boards may both have a task 12.
- The same board given twice is shown once. `--tool` names one loop board's
  tool, so it is refused when several loop boards are given; each then uses the
  `.py` named after its board file. `--data-dir` keeps one folder per board.
- A loop board's items show their stored `area`, and the area filter selects
  them.

## Read-only guarantees

- **Connections:** every connection is SQLite read-only (`mode=ro`) and
  `query_only`. There is no mutation API and no write path to the board.
- **WAL boards:** an idle WAL board (no `-wal`/`-shm`) is opened `immutable`,
  so reading it creates no sidecar files. That read is discarded and repeated
  if the file changed meanwhile. A board with an active WAL is read normally,
  including uncheckpointed rows; the dashboard never checkpoints.
- **Missing board:** a missing board is never created. A board that
  disappears, is locked beyond the 800 ms busy timeout, or is corrupt is
  reported. Its next pick is withdrawn, and the last successful data stays
  visible, marked stale.
- **If the first read fails:** only the error is shown, never zero counts.
- **Where it writes:** only to the history folder, which is never inside the
  project or the skill.

## Paged queries

The page never gets a board whole: a board of 734 items was a 21.7 MB first
message, and is now about 42 KB. On connect the socket sends `hello`, each
board's details: counts, rules, the tool's order (ids only), the current pick,
its tables' row counts. Everything else the page asks for, as a typed question
`{id, type, ...}` that gets one `{type: "reply", id, data}` (or `error`):

| `type` | Asks for |
| --- | --- |
| `list` | A list's next page, five at a time: `lane` (one board's status lane), `doing`, or `register` (every task in the chosen sort), with the page's filters. Returns the cards and the list's total. |
| `summary` | The report's totals for the chosen boards and filters: counts by status, severity and board, testing, objectives, the filters' choices and the latest activity. |
| `task` | One task's whole record, the tasks it links to, and the tool's evidence for its place in the order. |
| `rows` | A page of one table's stored rows. |
| `changes` | A page of history (`before`, `after`, `limit`, `item`), as `/api/changes` gives it. |
| `relax` | Relax's view, built where every task is. |

- **Lists load as they scroll:** each list shows its first five cards; more
  load five at a time as its "Show more" comes into view (a click does it too).
- **A card carries what it shows:** its story and why, its stored fields with
  long ones cut, counts of its related rows and the tool's reasons for not
  offering it. Opening it asks for the whole record.
- **A change pushes what changed:** the page asks again only for the totals, the
  list windows it shows and the open record.
- **One ordering:** the server answers with the page's own ordering, filter,
  testing and work-classification code (`lib/views.mjs` imports it), so a list
  is in the same order whether the server or the page would sort it.

## Live updates

- **Board watch:** `fs.watch` on the board's directory, filtered to the board
  file and its `-wal`, `-shm` and `-journal`
  files. It survives atomic replacement of the board file.
- **Debounce:** an 80 ms debounce merges bursts of events; it is not a refresh
  interval. A notification that left the size, mtime and identity of the board
  and its sidecars unchanged since the last read is lock housekeeping, and is
  counted and ignored.
- **Tool watch:** the board's tool (`todo.py`, or a loop board's own) is watched too. A
  change to it reruns the picker and re-derives the policy.
- **Pushes:** several state changes in one event-loop turn are pushed once.
- **No polling:** there are no timed rereads. The only browser timers are the
  Relax wall clock, the reconnect delay and the toast. On reconnect the
  server sends `hello` again.
- **Scope:** no other project is watched.

## Views

- **Full report:**
  - totals (shown as — when the tool's policy is unknown) and Doing;
  - the next panel: the pick (Resume or Next), first eligible not-started
    task, counts and current objective, the exact `next` output, "How todo.py
    decides" (derived groups, sort-key labels, docstrings), and the tool path
    and hash;
  - recent work, objectives, observed status transitions;
  - completion and testing;
  - activity, severity, statuses, the complete register, and file and watch
    status.
- **Full board:** one lane per status.
  - Unfinished lanes are in the tool's order, with badges and checked reasons.
  - Lanes of statuses the tool treats as closed show the most recent first.
- **Task drawer:** small metadata at the top: id, status, severity, priority,
  points, area, type, objective, parent task, created/updated/closed, and
  close_output. Below come:
  - story, why, exit, evidence and the dropped reason;
  - next order (position, the labelled `by_rule` key, and checked reasons);
  - block and parents/dependents;
  - provenance and findings (`open_children()`);
  - notes and roasts, and rows of any table with a `task` column;
  - testing evidence, all other fields, a dated timeline, and that task's diffs.

  If only part of the history is loaded, the drawer offers "Load this task's
  full history".
- **Long text:** a story, a why or a stored value longer than a few lines is
  cut at a word, with "Show more". A run of 12 or more comma-separated ids
  (such as a batch of record ids) shows as a count, for example "250 values".
  "Show more" opens the full text and "Show less" folds it again; what you
  opened stays open across live updates. Relax folds without buttons. Ids are
  not turned into names: the board holds the ids, not what they name.
- **Changes:** row-level additions, removals and edits for every table and the
  schema, shown as per-field line diffs. The newest page loads first; "Show
  more" and "Load all history" fetch the rest. Nothing is silently dropped,
  and the header shows recorded vs loaded counts.
- **Database records:** every table, row and column, with the schema.
- **Controls:**
  - Search and filters for area, type, topic, objective, status, testing,
    done-only, severity and blocked/waiting, plus a sort.
  - Dark/light toggle.
  - Pause: incoming changes are kept and applied on resume.
  - Reconnect, Print, Export.
  - Relax: full screen, wake lock where supported, Doing and next.

### Data shown as stored

- **Absent vs empty:** an absent field reads "Not recorded · no such field";
  NULL or empty reads "Not recorded".
- **Work area/type:** only from stored `area`/`work_area` and
  `type`/`work_type`/`category` columns.
- **Tested / E2E tested:** only from stored `tested` / `e2e_tested` fields,
  which today's `todo.py` does not have, so the page shows "Not recorded: this
  board has no tested field". Done never implies tested.
- **Dates:** local human form with the time zone, with the stored value kept.
- **Unused boards:** a board with no task table is never called "finished".

## Visual QA with a demo fixture

```
cd "<skills>/todo/dashboard"
node tests/demo-fixture.mjs --port 8765
```

This builds a disposable project in the OS temp folder using the real
`todo.py`. It contains:

- objectives, blockers and an explicit block;
- findings, including a flattened grandchild;
- notes, roasts, done and dropped tasks;
- a multi-line right-to-left story and a started task.

It serves that project from a subdirectory and keeps its history inside the
same temp folder. It prints:

- the URL;
- `todo.py` commands to run in the fixture, so you can watch live pushes.

The demo refuses `--db`, `--project`, `--data-dir` and `--todo-py`, so it can
never open a real board. Ctrl+C deletes everything. Installed, it uses
`../todo.py`; in a staging folder, set `TODO_SKILL_DIR` to the folder holding
`todo.py`.

## Tests

```
cd "<skills>/todo/dashboard"
npm test
```

(`npm test` runs `node --test --test-concurrency=1 "tests/*.test.mjs"`.) In a
staging folder, run it with `TODO_SKILL_DIR=<folder holding todo.py>`.

**Disposable fixtures only:**

- Every board is created by the real `todo.py` under the OS temp folder, and
  `todo.py` is never run outside it.
- The helpers refuse to run if any ancestor of the temp folder holds a board,
  checked once at load and again for each new temp folder.
- Child processes start with npm's `npm_*` and `INIT_CWD` variables removed,
  so `npm test` cannot steer them to a caller's board.
- History goes to a temp folder (`SIJAV_TODO_DASHBOARD_HOME`).
- Every spawned process is killed, with its whole process tree on Windows.
- Server tests run from a temp "plugin cache/skills/todo" install.

| File | Covers |
| --- | --- |
| `board.test.mjs` | Board discovery and no file creation; per-user history keyed by board; reads checked against Python's `sqlite3` (every table, row and field); byte-identical board; nothing grouped without policy; old and unused boards; idle and active WAL; locked board fails explicitly; row diffs; torn journal recovery. |
| `picker.test.mjs` | Exact real `next` text and pick at every step of working a board; started precedence and blocked started work; a blocked task with a done and an unfinished parent gets exact, probe-confirmed reasons that match how the real tool resolves them; legacy severities and statuses do not cost the pick; a changed `by_rule`/`choose` changes order, labels and groups; refused constants and start-up mutations; tool writes never reach the project; old and unused boards on a copy; missing Python explained. |
| `live.test.mjs` | Installed copy from a subdirectory, with `$'`, `$&` and `$$` in the project path; host, origin, Sec-Fetch and frame headers; WAL push with a quiet period showing no polling; incremental history and pagination; read failure withdraws pick and order then recovers; broken or changed `todo.py` is watched; atomic replacement; CLI output, restart history, refusals, junction start, Node version gate; real `npm start` from the caller's directory; npm variables inherited from an unrelated npm process never select another (synthetic) board; installed helpers use the parent `todo.py`; demo refuses overrides. |
| `views.test.mjs` | The paged queries on a loop board: the first message has no task in it, lists come five at a time in the tool's order with their totals and filters, a card carries its reasons and its story once, a record has its links and evidence, totals and table pages are answered, an unknown request is an error, and a change names only the tasks it touched. |
| `ui.test.mjs` | Order only from a current result; labelled previous order while re-checking; no order on failure; no recency without policy; done most recent first; field line diffs; dates; recorded-only area; Relax states; theme and display controller; no browser polling; long text and id lists fold, open and fold again, escaped. |
| `render.test.mjs` | The real `public/app.js` with the real server's embedded settings and real WebSocket messages: every view, drawer and Relax; after a failed read, no stale pick, badge or text; without Python, no claimed groups or counts; a loop board in its own tool's words, with a long id list folded, its areas on the cards and in the filter, the item's own fields in the drawer and Relax naming its tool; two boards on one page, each in its own tool's words, cards named by board, each drawer its own; no literal `${` in any view. |
| `loop.test.mjs` | Two boards on one page: each read with its own tool and history, one numbering of changes, the same board given twice shown once, `--tool` refused for several loop boards. Loop boards made by a small test loop tool (`tests/loop-fixture`): told apart by their tables, and any other SQLite file refused, also by the CLI; found from their own folder and named after the project folder; items, blockers, parked notes and findings mapped; `loop_picker.py` gives the tool's own head, order, status groups and checked reasons, and refuses a tool whose order it cannot read; an older loop board without parked, exit or created columns; the live server through a change; the board never written and nothing created beside it. |

`test-output.txt` holds the full last run.

## Status

- **Fixture tests:** all pass; see `test-output.txt`.
- **Loop board view:** checked in the browser on a copy of a real loop board
  (728 items): the tool's own head and order, the item drawer, and a 250-id
  list folded to "250 values", opened with Show more and folded with Show
  less.
- **Browser QA:** the root ran the earlier build against a synthetic 12-task
  board in TEMP and it passed. It covered fields, story, area and order, a
  WebSocket field diff after a real `todo.py` move, both themes, and Relax
  (`todo-dashboard-relax.png`).
- **Visually unchecked since then:** the later changes are only
  covered by the headless tests above. They are the labelled previous-order
  and failure states, unknown-policy rendering, history loading and the "How
  todo.py decides" panel.

## Known limits

- **Port 0 changes on every start.** Use `--port` for a stable browser
  address; a tab opened on an old port does not find the restarted server.
- **A loop tool's machine checks are not run.** The page shows
  `board_order()`'s order. If the tool's `next` holds an item back because the
  machine is not ready, the page does not know it.
- **Only the tool file itself is watched.** A change to a module it imports
  (such as a loop tool's `tool/board_order.py`) is picked up at the next board
  change or "Run next again".
- **Python is required for order and status meaning.** `todo.mjs` cannot be
  used for this, because its functions can't be loaded without running it.
- **Probing relies on the tool's own functions and tables:** `choose`,
  `open_children`, `block_of`, `all_tasks`, `children`, the `blocked` and
  `blocked_by` tables, and the `SEVERITIES`/`POINTS` constants for probe rows.
  If a future tool drops them, the page says the policy cannot be read rather
  than guessing.
- **One dashboard per history folder.** There is no lock. Two dashboards for
  the same board (with the default per-board folder or the same
  `--data-dir`) would both append to `changes.jsonl` and overwrite
  `baseline.json`. This can duplicate entries or interleave them; unreadable
  lines are skipped on the next start, but no entry is otherwise repaired.
  Run one dashboard per board, or give each its own `--data-dir`. Writes are
  append-then-rename, which is not crash-proof: a crash mid-write can leave a
  torn last line (skipped and reported) or lose the latest baseline (history
  then continues from the next read).
- **Gaps in observed history:** history covers only what the dashboard saw
  while running; edits between two reads may be merged. Doing start times are
  observation times.
- **Windows renames:** SQLite opens files without `FILE_SHARE_DELETE`, so a
  rename over the board fails while the dashboard or picker has it open
  (briefly). Tools that replace the board should retry.
- **Parent directory removal:** if the board's `.claude` directory itself is
  removed and recreated, the watcher reports an error. Restart the dashboard.
- **Same-tick writes:** change detection relies on file size, mtime (ns) and
  identity. SQLite commits update the mtime.

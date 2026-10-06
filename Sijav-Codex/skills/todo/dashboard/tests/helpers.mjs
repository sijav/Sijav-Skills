// Disposable fixtures only: every board here is created in the OS temp
// directory by the real todo.py, and every directory is removed afterwards.
import { mkdtempSync, mkdirSync, cpSync, existsSync, rmSync, readFileSync, copyFileSync, readdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFileSync, spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { findBoard } from '../lib/board.mjs';

export const STAGE = dirname(dirname(fileURLToPath(import.meta.url)));
/**
 * The unchanged todo.py: TODO_SKILL_DIR when set (staging), otherwise the
 * installed layout's parent folder, <skill>/todo.py beside <skill>/dashboard.
 */
export function todoDir(env = process.env) {
  if (env.TODO_SKILL_DIR) return resolve(env.TODO_SKILL_DIR);
  const parent = dirname(STAGE);
  if (existsSync(join(parent, 'todo.py'))) return parent;
  throw new Error(`No todo.py beside the dashboard (${parent}). Set TODO_SKILL_DIR to the skill folder that holds todo.py.`);
}
export const TODO_DIR = todoDir();
export const TODO_PY = join(TODO_DIR, 'todo.py');
export const PYTHON = process.env.SIJAV_TODO_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const WS_DIR = [join(STAGE, 'node_modules', 'ws'), process.env.TODO_DASHBOARD_WS_DIR].find(p => p && existsSync(join(p, 'package.json')));

// Preflight: if the temp folder itself sits under a board, no test may run.
if (findBoard(tmpdir())) throw new Error(`Refusing to run: ${findBoard(tmpdir())} is above the temp folder ${tmpdir()}.`);

/**
 * The parent environment without npm's run-script variables. Under `npm test`
 * every child would otherwise inherit npm_lifecycle_event / INIT_CWD pointing
 * at the caller's folder; children start clean and get only what a test sets.
 */
export function cleanEnv(extra = {}) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^npm_/i.test(key) && key.toUpperCase() !== 'INIT_CWD'));
  return { ...env, ...extra };
}

const made = [];
/** A new directory under the OS temp folder. Refuses to work if any ancestor already has a board. */
export function tempDir(label = 'todo dash ') {
  const dir = mkdtempSync(join(tmpdir(), label));
  made.push(dir);
  const ancestor = findBoard(dir);
  if (ancestor) throw new Error(`Refusing to use ${dir}: an existing board ${ancestor} is above the temp folder.`);
  return dir;
}
export function cleanup() {
  for (const dir of made.splice(0)) {
    for (let i = 0; i < 5; i++) { try { rmSync(dir, { recursive: true, force: true }); break; } catch { /* Windows handle release */ } }
  }
}
/** Environment for code under test: history goes to a temp folder, never the user's cache. */
export function testEnv(extra = {}) {
  return cleanEnv({ SIJAV_TODO_DASHBOARD_HOME: join(tempDir('todo history '), 'cache'), TODO_SKILL_DIR: TODO_DIR, ...extra });
}

/** Run the real, unchanged todo.py with the given working directory. */
export function todo(cwd, ...args) {
  return todoWith(TODO_PY, cwd, ...args);
}
/** Run a (possibly modified) todo.py copy, only ever inside the temp folder. */
export function todoWith(tool, cwd, ...args) {
  if (!resolve(cwd).toLowerCase().startsWith(resolve(tmpdir()).toLowerCase())) throw new Error(`Refusing to run todo.py outside the temp folder: ${cwd}`);
  return execFileSync(PYTHON, ['-B', tool, ...args], { cwd, encoding: 'utf8', env: cleanEnv({ PYTHONDONTWRITEBYTECODE: '1' }), stdio: ['ignore', 'pipe', 'pipe'] });
}
export const add = (cwd, id, title, extra = []) =>
  todo(cwd, 'add', '--id', id, '--title', title, '--desc', `Story of ${title}`, '--why', `Why ${title}`, '--severity', 'medium', '--points', '3', '--exit', `Exit check for ${title} is observable`, ...extra);

/** A project folder (with spaces) holding a board made by the tool, plus a subdirectory. */
export function project(label = 'fixture project ', name = 'my project') {
  const root = join(tempDir(label), name);
  mkdirSync(join(root, 'apps', 'web app'), { recursive: true });
  todo(root, 'init', '--here');
  return { root, db: join(root, '.claude', 'todo.db'), sub: join(root, 'apps', 'web app') };
}

/** A rich board: objectives, blockers, explicit block, findings with flattening, notes, roasts, done/dropped. */
export function richProject(name) {
  const p = project('fixture project ', name);
  const r = p.root;
  todo(r, 'okr', 'add', '--id', 'MVP', '--name', 'First release', '--description', 'Ship the first version');
  todo(r, 'okr', 'add', '--id', 'LATER', '--name', 'Afterwards', '--description', 'What comes after');
  add(r, 'MP-001', 'Foundation', ['--severity', 'high', '--points', '2', '--area', 'api']);
  add(r, 'MP-002', 'Later critical', ['--severity', 'critical', '--points', '1', '--phase', 'LATER']);
  add(r, 'MP-003', 'Needs foundation', ['--severity', 'critical', '--points', '1', '--parent', 'MP-001', '--area', 'web']);
  add(r, 'MP-004', 'Small medium', ['--points', '1']);
  add(r, 'MP-005', 'Blocked one', ['--severity', 'critical', '--points', '1']);
  todo(r, 'move', 'MP-005', 'blocked', '--reason', 'Waiting for the owner to choose');
  add(r, 'MP-006', 'Shipped thing', ['--severity', 'low', '--points', '5']);
  todo(r, 'move', 'MP-006', 'in_progress');
  todo(r, 'move', 'MP-006', 'done', '--evidence', 'Observed the page render');
  add(r, 'MP-007', 'Finding of shipped', ['--severity', 'high', '--points', '2', '--parent-task', 'MP-006']);
  add(r, 'MP-008', 'Grandchild attempt', ['--severity', 'low', '--points', '1', '--parent-task', 'MP-007']); // flattens to MP-006
  add(r, 'MP-009', 'Dropped idea', ['--severity', 'low', '--points', '1']);
  todo(r, 'move', 'MP-009', 'dropped', '--reason', 'Owner withdrew it');
  todo(r, 'edit', 'MP-004', '--note', 'First note\nsecond line');
  todo(r, 'roast', 'MP-006', '--file', 'roasts/mp-006.md', '--score', '8', '--criticals', '0');
  todo(r, 'roast', 'MP-006', '--file', 'roasts/mp-006.md', '--filed', 'MP-007, MP-008');
  add(r, 'MP-010', 'Second done', ['--severity', 'low', '--points', '1']);
  todo(r, 'move', 'MP-010', 'done', '--evidence', 'Second closure');
  add(r, 'MP-1000', 'Numeric id ordering', ['--severity', 'medium', '--points', '1']);
  return p;
}

/** Run the test loop tool (tests/loop-fixture) copied into a temp project, only ever inside the temp folder. */
export function loopTool(tool, ...args) {
  if (!resolve(tool).toLowerCase().startsWith(resolve(tmpdir()).toLowerCase())) throw new Error(`Refusing to run a loop tool outside the temp folder: ${tool}`);
  return execFileSync(PYTHON, ['-B', tool, ...args], { cwd: dirname(tool), encoding: 'utf8', env: cleanEnv({ PYTHONDONTWRITEBYTECODE: '1' }), stdio: ['ignore', 'pipe', 'pipe'] }).trim();
}
/**
 * A project whose board is a loop board in a subfolder: <root>/loop/board.db, made and filled by the
 * test loop tool's own commands. Board order: #2 critical waits on #1, #3 critical is parked, then #1 high,
 * #4 medium (doing) and #6 low; #5 is closed. #1 has an open finding, #5 a resolved one; #6's why carries a
 * long list of ids. #1 and #6 are in area back, #4 in area front, the rest have none.
 */
export const LONG_IDS = Array.from({ length: 40 }, (_, i) => 'c-' + (0xabc000 + i).toString(16).padStart(12, '0')).join(', ');
export function loopProject(name = 'loop project') {
  const root = join(tempDir('loop fixture '), name), dir = join(root, 'loop');
  cpSync(join(STAGE, 'tests', 'loop-fixture'), dir, { recursive: true });
  const tool = join(dir, 'board.py'), run = (...args) => loopTool(tool, ...args);
  run('init');
  run('add', '--title', 'Foundation', '--severity', 'high', '--priority', '1', '--exit', 'check foundation', '--story', 'Story of Foundation', '--why', 'Why Foundation', '--points', '3', '--created', '1700000000', '--area', 'back');
  run('add', '--title', 'Needs foundation', '--severity', 'critical', '--exit', 'check needs', '--created', '1700000001');
  run('add', '--title', 'Waiting on a person', '--severity', 'critical', '--exit', 'check person', '--created', '1700000002');
  run('add', '--title', 'In progress', '--severity', 'medium', '--exit', 'check progress', '--created', '1700000003', '--area', 'front');
  run('add', '--title', 'Shipped', '--severity', 'low', '--exit', 'check shipped', '--created', '1700000004');
  run('add', '--title', 'Long list', '--severity', 'low', '--priority', '5', '--exit', 'check list', '--why', 'Batch of places. IDS: ' + LONG_IDS, '--created', '1700000005', '--area', 'back');
  run('dep', '2', '1');
  run('park', '3', '--reason', 'Owner decides the wording');
  run('start', '4');
  run('close', '5', '--did', 'Observed it ship', '--at', '1700000100');
  run('finding', '1', '--severity', 'high', '--text', 'First finding', '--at', '1700000010');
  run('resolve', run('finding', '5', '--severity', 'low', '--text', 'Second finding', '--at', '1700000011'));
  return { root, dir, tool, db: join(dir, 'board.db'), run };
}

/** An isolated duplicate of a project's board for running the real `todo.py next`. */
export function duplicateBoard(db) {
  const root = join(tempDir('todo dup '), 'dup project');
  mkdirSync(join(root, '.claude'), { recursive: true });
  for (const suffix of ['', '-wal', '-shm', '-journal']) if (existsSync(db + suffix)) copyFileSync(db + suffix, join(root, '.claude', 'todo.db' + suffix));
  return root;
}

/**
 * A controlled real picker child: a Python bridge, given as the monitor's interpreter, that counts its
 * invocations in `count`, marks each start in `marker` and then runs the real tool with the picker's own
 * arguments. In a "wait" mode the invocation numbered `hold` waits (at most 20 s) for the `release` file;
 * in "wait-error" and "wait-stale-error" that invocation then fails with exit 7. "rows" and "tool" change
 * the board or the tool on every run. Labelled controlled evidence, not a native observation.
 */
export function nativeBridge(mode, dir, { hold = 1 } = {}) {
  const path = join(dir, 'bridge.py'), marker = join(dir, 'started'), release = join(dir, 'release'), count = join(dir, 'count');
  writeFileSync(path, [
    'import os,sys,sqlite3,subprocess,time',
    'from contextlib import closing',
    'args=sys.argv[1:]',
    'db=args[args.index("--db")+1]',
    'tool=args[args.index("--todo")+1]',
    'mode=' + JSON.stringify(mode),
    'marker=' + JSON.stringify(marker),
    'release=' + JSON.stringify(release),
    'count=' + JSON.stringify(count),
    'hold=' + Number(hold),
    'try:',
    '    with open(count) as source: invocation=int(source.read())+1',
    'except FileNotFoundError: invocation=1',
    'with open(count,"w") as out: out.write(str(invocation))',
    'open(marker,"w").close()',
    'if mode=="rows":',
    '    with closing(sqlite3.connect(db)) as conn, conn:',
    '        conn.execute("UPDATE task SET title=title || \'!\' WHERE id=\'MP-001\'")',
    'if mode=="tool":',
    '    with open(tool,"a",encoding="utf-8") as out: out.write("\\n# genuine confined tool-change fixture\\n")',
    'if mode.startswith("wait") and invocation==hold:',
    '    deadline=time.monotonic()+20',
    '    while not os.path.exists(release):',
    '        if time.monotonic()>deadline: raise RuntimeError("parent did not release native fixture child")',
    '        time.sleep(.01)',
    'if mode in ("wait-error","wait-stale-error") and invocation==hold:',
    '    print("native delayed child deliberately fails",file=sys.stderr)',
    '    raise SystemExit(7)',
    'raise SystemExit(subprocess.run([sys.executable,*args]).returncode)',
  ].join('\n') + '\n');
  return { path, marker, release, count };
}

export const sha = path => createHash('sha256').update(readFileSync(path)).digest('hex');
export const listing = dir => readdirSync(dir).sort();

/** The installed layout: <tmp>/plugin cache/skills/todo/{todo.py,todo.mjs,dashboard/...}. */
export function install() {
  const skill = join(tempDir('todo install '), 'plugin cache', 'skills', 'todo');
  mkdirSync(skill, { recursive: true });
  for (const file of ['todo.py', 'todo.mjs', 'SKILL.md']) if (existsSync(join(TODO_DIR, file))) copyFileSync(join(TODO_DIR, file), join(skill, file));
  const dash = join(skill, 'dashboard');
  for (const entry of ['server.mjs', 'picker.py', 'loop_picker.py', 'defaults.json', 'package.json', 'package-lock.json', 'README.md', 'lib', 'public', 'tests']) cpSync(join(STAGE, entry), join(dash, entry), { recursive: true });
  if (!WS_DIR) throw new Error('The ws package is needed for server tests: run npm ci in the dashboard folder or set TODO_DASHBOARD_WS_DIR.');
  cpSync(WS_DIR, join(dash, 'node_modules', 'ws'), { recursive: true });
  return { skill, dash, server: join(dash, 'server.mjs'), serverUrl: pathToFileURL(join(dash, 'server.mjs')).href };
}

/**
 * Start a Node script as its own process with an IPC channel, as a parent that will ask it to
 * stop starts it. Resolves once its output matches `ready`, or once it exits.
 */
export function launch(script, args, { cwd, env = testEnv(), ready = /Ctrl\+C/ } = {}) {
  return new Promise(resolveLaunch => {
    const child = spawn(process.execPath, [script, ...args], { cwd, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe', 'ipc'] });
    let out = '', err = '', settled = false;
    const settle = value => { if (!settled) { settled = true; resolveLaunch(value); } };
    child.stdout.on('data', d => { out += d; if (ready.test(out)) settle({ child, out, err, url: out.match(/^URL:\s+(\S+)/m)?.[1] }); });
    child.stderr.on('data', d => { err += d; });
    child.on('exit', code => settle({ child, code, out, err }));
  });
}

/** Ends a process at once, its whole tree on Windows: nothing it would do on exit happens. */
export function forceKill(child) {
  try { if (process.platform === 'win32') execFileSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], { stdio: 'ignore' }); else child.kill('SIGKILL'); } catch {}
}

/**
 * Ask a launched process to stop by letting go of its IPC channel, and wait for it to exit by
 * itself. One still running after `ms` is killed, and that is a failure: what it does on exit,
 * its clean-up and its coverage among them, is lost. Resolves with its exit code.
 */
export async function stopGracefully(child, ms = 20000) {
  if (child.exitCode != null || child.signalCode != null) return child.exitCode;
  const exited = new Promise(done => child.once('exit', code => done(code)));
  let timer;
  const late = new Promise(done => { timer = setTimeout(done, ms, 'late'); });
  if (child.connected) child.disconnect();
  const code = await Promise.race([exited, late]);
  clearTimeout(timer);
  if (code !== 'late') return code;
  forceKill(child);
  await exited;
  throw new Error(`process ${child.pid} did not exit within ${ms} ms of its IPC channel closing; it was killed, so its own exit work and coverage are lost`);
}

/**
 * The coverage files a process wrote, when this run is measured (NODE_V8_COVERAGE set, which
 * every child started with testEnv() inherits); null when nothing is measured. Node names
 * each file after the process that wrote it, so a missing one is that process's data lost.
 */
export function coverageFilesOf(pid, env = process.env) {
  if (!env.NODE_V8_COVERAGE) return null;
  return existsSync(env.NODE_V8_COVERAGE) ? readdirSync(env.NODE_V8_COVERAGE).filter(name => name.startsWith(`coverage-${pid}-`)) : [];
}

/** Test-side wait for an asynchronous condition; the dashboard itself has no timers that poll. */
export async function until(check, what, ms = 20000) {
  const end = Date.now() + ms;
  for (;;) {
    const value = await check();
    if (value) return value;
    if (Date.now() > end) throw new Error('Timed out waiting for ' + what);
    await new Promise(r => setTimeout(r, 25));
  }
}

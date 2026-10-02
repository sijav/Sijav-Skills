// Disposable fixtures only: every board here is created in the OS temp
// directory by the real todo.py, and every directory is removed afterwards.
import { mkdtempSync, mkdirSync, cpSync, existsSync, rmSync, readFileSync, copyFileSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
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
 * long list of ids.
 */
export const LONG_IDS = Array.from({ length: 40 }, (_, i) => 'c-' + (0xabc000 + i).toString(16).padStart(12, '0')).join(', ');
export function loopProject(name = 'loop project') {
  const root = join(tempDir('loop fixture '), name), dir = join(root, 'loop');
  cpSync(join(STAGE, 'tests', 'loop-fixture'), dir, { recursive: true });
  const tool = join(dir, 'board.py'), run = (...args) => loopTool(tool, ...args);
  run('init');
  run('add', '--title', 'Foundation', '--severity', 'high', '--priority', '1', '--exit', 'check foundation', '--story', 'Story of Foundation', '--why', 'Why Foundation', '--points', '3', '--created', '1700000000');
  run('add', '--title', 'Needs foundation', '--severity', 'critical', '--exit', 'check needs', '--created', '1700000001');
  run('add', '--title', 'Waiting on a person', '--severity', 'critical', '--exit', 'check person', '--created', '1700000002');
  run('add', '--title', 'In progress', '--severity', 'medium', '--exit', 'check progress', '--created', '1700000003');
  run('add', '--title', 'Shipped', '--severity', 'low', '--exit', 'check shipped', '--created', '1700000004');
  run('add', '--title', 'Long list', '--severity', 'low', '--priority', '5', '--exit', 'check list', '--why', 'Batch of places. IDS: ' + LONG_IDS, '--created', '1700000005');
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

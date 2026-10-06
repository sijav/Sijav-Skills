// Visual QA only: builds a disposable fixture board in the OS temp directory
// with the real, unchanged todo.py, then serves it with this dashboard. Its
// history is kept inside the same disposable folder. Ctrl+C stops the server
// and deletes everything. It never opens another board:
//   node tests/demo-fixture.mjs [--port <n>] [--host <addr>] [--python <exe>]
import { dirname, join } from 'node:path';
import { startDashboard, describe, parseArgs } from '../server.mjs';
import { richProject, add, todo, cleanup, TODO_PY } from './helpers.mjs';

const refused = ['--db', '--project', '--data-dir', '--todo-py'];
const given = process.argv.slice(2).find(arg => refused.includes(arg.split('=')[0]));
if (given) {
  console.error(`The demo does not accept ${given.split('=')[0]}: it only ever serves its own disposable fixture. Use server.mjs for a real board.`);
  process.exit(2);
}
let options;
try { options = parseArgs(process.argv.slice(2)); } catch (error) { console.error(error.message); process.exit(2); }
const p = richProject();
add(p.root, 'MP-011', 'Long multi-line story with right-to-left text', ['--severity', 'high', '--points', '3', '--area', 'web',
  '--desc', 'As a reader I want the complete story kept.\nSecond line of the story.\nمتن فارسی برای آزمایش جهت راست به چپ', '--why', 'Owner wants every stored field visible']);
todo(p.root, 'move', 'MP-007', 'in_progress');
todo(p.root, 'edit', 'MP-007', '--note', 'Started from the demo fixture');
const started = await startDashboard({ port: options.port, host: options.host, python: options.python, todoPy: TODO_PY, db: p.db, dataDir: join(dirname(p.root), 'dashboard history') }, { cwd: p.sub });
console.log(describe(started));
console.log(`\nDisposable fixture project: ${p.root}`);
console.log('Make a live change (watch it arrive without reloading), for example:');
console.log(`  cd "${p.root}" && python "${TODO_PY}" move MP-004 in_progress`);
console.log(`  cd "${p.root}" && python "${TODO_PY}" move MP-007 done --evidence "Seen in the browser"`);
// Ctrl+C, a stop signal, or a parent that started it with an IPC channel letting go of it
// (on Windows a signal from another process is a forced kill). It stops once.
let stopping = null;
const stop = () => (stopping ??= started.close().then(() => { cleanup(); process.exit(0); }));
process.on('SIGINT', stop); process.on('SIGTERM', stop); process.on('disconnect', stop);

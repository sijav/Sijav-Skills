"""A small loop board tool for the dashboard's tests.

The board is board.db beside this file (or --db): an `item` table, a `dep`
table of blockers and a `finding` table, written only by this file's commands.
The order lives in tool/board_order.py. Importing this file touches no database.
"""

import argparse
import os
import sqlite3
from contextlib import closing
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "tool"))
from board_order import board_order  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS item (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  why TEXT,
  story TEXT,
  severity TEXT NOT NULL CHECK (severity IN ('critical','high','medium','low')),
  priority INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','doing','closed')),
  exit_cmd TEXT NOT NULL,
  occurrences INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  closed_at INTEGER,
  close_did TEXT,
  close_output TEXT,
  points INTEGER,
  parked TEXT,
  tested INTEGER NOT NULL DEFAULT 0,
  tested_how TEXT,
  e2e_tested INTEGER NOT NULL DEFAULT 0,
  e2e_how TEXT,
  area TEXT
);
CREATE TABLE IF NOT EXISTS dep (item INTEGER NOT NULL, blocker INTEGER NOT NULL, PRIMARY KEY (item, blocker));
CREATE TABLE IF NOT EXISTS finding (
  id INTEGER PRIMARY KEY,
  item INTEGER NOT NULL,
  severity TEXT NOT NULL,
  text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved')),
  at INTEGER NOT NULL
);
"""


def connect(path):
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        return conn
    except BaseException:
        conn.close()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.path.join(HERE, "board.db"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    add = commands.add_parser("add")
    add.add_argument("--title", required=True)
    add.add_argument("--severity", required=True)
    add.add_argument("--priority", type=int, default=0)
    add.add_argument("--exit", required=True)
    add.add_argument("--why")
    add.add_argument("--story")
    add.add_argument("--points", type=int)
    add.add_argument("--created", type=int, required=True)
    add.add_argument("--area")
    dep = commands.add_parser("dep")
    dep.add_argument("item", type=int)
    dep.add_argument("blocker", type=int)
    start = commands.add_parser("start")
    start.add_argument("item", type=int)
    close = commands.add_parser("close")
    close.add_argument("item", type=int)
    close.add_argument("--did", required=True)
    close.add_argument("--at", type=int, required=True)
    park = commands.add_parser("park")
    park.add_argument("item", type=int)
    park.add_argument("--reason", required=True)
    finding = commands.add_parser("finding")
    finding.add_argument("item", type=int)
    finding.add_argument("--severity", required=True)
    finding.add_argument("--text", required=True)
    finding.add_argument("--at", type=int, required=True)
    resolve = commands.add_parser("resolve")
    resolve.add_argument("finding", type=int)
    commands.add_parser("next")
    args = parser.parse_args(argv)

    with closing(connect(args.db)) as conn, conn:
        if args.command == "add":
            cursor = conn.execute(
                "INSERT INTO item (title, why, story, severity, priority, exit_cmd, points, created_at, area)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (args.title, args.why, args.story, args.severity, args.priority, args.exit, args.points, args.created,
                 args.area))
            print(cursor.lastrowid)
        elif args.command == "dep":
            conn.execute("INSERT INTO dep (item, blocker) VALUES (?,?)", (args.item, args.blocker))
        elif args.command == "start":
            conn.execute("UPDATE item SET status='doing' WHERE id=?", (args.item,))
        elif args.command == "close":
            conn.execute("UPDATE item SET status='closed', close_did=?, closed_at=? WHERE id=?", (args.did, args.at, args.item))
        elif args.command == "park":
            conn.execute("UPDATE item SET parked=? WHERE id=?", (args.reason, args.item))
        elif args.command == "finding":
            cursor = conn.execute("INSERT INTO finding (item, severity, text, at) VALUES (?,?,?,?)",
                                  (args.item, args.severity, args.text, args.at))
            print(cursor.lastrowid)
        elif args.command == "resolve":
            conn.execute("UPDATE finding SET status='resolved' WHERE id=?", (args.finding,))
        elif args.command == "next":
            startable = [row for row, ok in board_order(conn) if ok]
            print(f"NEXT {startable[0][0]} {startable[0][1]}" if startable else "nothing startable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

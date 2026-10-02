"""The order of a small loop board, for the dashboard's tests.

Shaped like a loop board's own order module: functions that take a connection
and never write. Every item that is not closed is open; an item with a blocker
that is not closed, or with a parked note, cannot be started.
"""

SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def open_items(conn):
    """Every item that is not closed, `doing` included."""
    return conn.execute(
        "SELECT id,title,severity,priority,occurrences,created_at FROM item WHERE status!='closed'"
    ).fetchall()


def blocked_ids(conn):
    """Items with at least one blocker that is not closed."""
    return {row[0] for row in conn.execute(
        "SELECT d.item FROM dep d JOIN item b ON b.id=d.blocker WHERE b.status!='closed'")}


def parked_ids(conn):
    """Open items waiting on a person, with the reason in `parked`."""
    return {row[0] for row in conn.execute("SELECT id FROM item WHERE status!='closed' AND parked IS NOT NULL")}


def sort_key(row):
    # severity, then priority (lowest first), then most repeated, then oldest.
    _id, _title, severity, priority, occurrences, created = row
    return (SEV_RANK[severity], priority, -occurrences, created)


def board_order(conn):
    """Every open item in order, each paired with whether it can be started."""
    skip = blocked_ids(conn) | parked_ids(conn)
    return [(row, row[0] not in skip) for row in sorted(open_items(conn), key=sort_key)]

#!/usr/bin/env python3
"""Offline evaluation for the F1 command breakdown engine.

Runs `core.cmd_parse.breakdown` over the distinct commands in a dashboard
database and prints aggregates only: the read/write/unknown split of the
steps, how many commands are fully read, and the verbs behind the `unknown`
steps. The reviewer runs it against a *copy* of the live database; its output
is not committed.

The database is opened read-only (`file:…?mode=ro`), so a mistyped path can
never modify production data.

Usage:
  python3 scripts/breakdown_report.py --db PATH [--since YYYY-MM-DD] [--show-unknown N]

`--show-unknown N` prints example `unknown` steps. That output can contain
real command text, so it exists only for local use and is capped at N lines.
"""

import argparse
import os
import pathlib
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARD = os.path.join(ROOT, 'dashboard')
if DASHBOARD not in sys.path:
    sys.path.insert(0, DASHBOARD)

from core.cmd_parse import breakdown  # noqa: E402

TOP_UNKNOWN = 20
EXAMPLE_CHARS = 160


def _fail(message):
    print('error: %s' % message, file=sys.stderr)
    raise SystemExit(2)


def _open_readonly(path):
    if not os.path.isfile(path):
        _fail('no such database file: %s' % path)
    uri = pathlib.Path(path).resolve().as_uri() + '?mode=ro'
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        _fail('cannot open %s read-only: %s' % (path, exc))
    conn.row_factory = sqlite3.Row
    return conn


def _since_epoch(value):
    try:
        parsed = datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except ValueError:
        _fail('--since must be YYYY-MM-DD, got %r' % value)
    return int(parsed.timestamp())


def _load_commands(conn, since, include_integration):
    where = []
    params = []
    if since is not None:
        where.append('created_at >= ?')
        params.append(since)
    if not include_integration:
        # MCP calls are stored in `requests` too, with the tool name as the
        # "command". They are not shell commands (review round 1, R11).
        where.append("(status IS NULL OR status NOT LIKE 'integration-%')")
    sql = 'SELECT DISTINCT command FROM requests'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    try:
        rows = conn.execute(sql, tuple(params)).fetchall()
    except sqlite3.Error as exc:
        _fail('cannot read the requests table: %s' % exc)
    return [row['command'] for row in rows
            if row['command'] and row['command'].strip()]


def _count_excluded(conn, since):
    sql = "SELECT COUNT(*) FROM requests WHERE status LIKE 'integration-%'"
    params = ()
    if since is not None:
        sql += ' AND created_at >= ?'
        params = (since,)
    try:
        return conn.execute(sql, params).fetchone()[0]
    except sqlite3.Error:
        return 0


def _verb(text):
    """The first word of a step, without quotes or a path prefix."""
    words = (text or '').split()
    word = words[0] if words else ''
    word = word.strip('\'"`')
    word = os.path.basename(word)
    return word.lower() or '(empty)'


def _percent(part, whole):
    return 0.0 if not whole else 100.0 * part / whole


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Aggregate breakdown statistics over a dashboard database.')
    parser.add_argument('--db', required=True, help='path to a dashboard SQLite database')
    parser.add_argument('--since', help='only commands on/after this UTC date (YYYY-MM-DD)')
    parser.add_argument('--show-unknown', type=int, default=0, metavar='N',
                        help='print N example unknown steps (local use only)')
    parser.add_argument('--include-integration', action='store_true',
                        help='also analyse MCP/integration rows (default: skipped)')
    args = parser.parse_args(argv)

    since = _since_epoch(args.since) if args.since else None
    conn = _open_readonly(args.db)
    try:
        commands = _load_commands(conn, since, args.include_integration)
        excluded = 0 if args.include_integration else _count_excluded(conn, since)
    finally:
        conn.close()

    counts = {'read': 0, 'write': 0, 'unknown': 0}
    total_steps = 0
    fully_read = 0
    empty = 0
    unknown_verbs = Counter()
    unknown_examples = []

    for command in commands:
        result = breakdown(command)
        steps = result['steps']
        if not steps:
            empty += 1
        total_steps += len(steps)
        command_read = True
        for step in steps:
            kind = step['kind']
            counts[kind] = counts.get(kind, 0) + 1
            if kind != 'read':
                command_read = False
            if kind == 'unknown':
                unknown_verbs[_verb(step['text'])] += 1
                if len(unknown_examples) < max(0, args.show_unknown):
                    unknown_examples.append(step['text'])
        if steps and command_read:
            fully_read += 1

    print('breakdown report')
    print('  database: %s' % os.path.abspath(args.db))
    if args.since:
        print('  since:    %s (UTC)' % args.since)
    print('  commands: %d analysed, %d without steps' % (len(commands), empty))
    if not args.include_integration:
        print('  excluded: %d integration (MCP) rows — --include-integration to analyse them'
              % excluded)
    print('  steps:    %d' % total_steps)
    for kind in ('read', 'write', 'unknown'):
        print('    %-7s %6d  %5.1f%%' % (kind, counts[kind], _percent(counts[kind], total_steps)))
    print('  commands where every step is read: %d (%.1f%%)'
          % (fully_read, _percent(fully_read, len(commands))))

    if unknown_verbs:
        print('  top %d unknown verbs:' % TOP_UNKNOWN)
        for rank, (verb, count) in enumerate(unknown_verbs.most_common(TOP_UNKNOWN), 1):
            print('    %2d. %-28s %5d' % (rank, verb[:28], count))

    if unknown_examples:
        print('  example unknown steps (local use only):')
        for text in unknown_examples:
            flat = ' '.join(text.split())
            if len(flat) > EXAMPLE_CHARS:
                flat = flat[:EXAMPLE_CHARS - 1] + '…'
            print('    - %s' % flat)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())

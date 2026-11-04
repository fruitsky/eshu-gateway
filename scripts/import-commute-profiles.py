#!/usr/bin/env python3
"""Import legacy commute profiles into Eshu's DB.

Usage:
    python3 scripts/import-commute-profiles.py /path/to/profiles.json [--dry-run]

The legacy file may contain `chat_id`; it is deliberately dropped. `notify_at`
is mapped to `notify_times`. Every profile is validated before anything is
written. Run on the dashboard host (the script uses dashboard/eshu.db by
default).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

DASHBOARD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "dashboard")
sys.path.insert(0, DASHBOARD)

import db.core as db_core  # noqa: E402
from db.core import init_db  # noqa: E402
from db import commute as store  # noqa: E402
from core.commute import normalize_for_import  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Import legacy commute profiles")
    parser.add_argument("path", help="profiles.json to import")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and print; do not write")
    parser.add_argument("--db", default=None,
                        help="Optional SQLite path (default: dashboard/eshu.db)")
    args = parser.parse_args()

    if args.db:
        db_core.DB_PATH = args.db

    try:
        with open(args.path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read {args.path}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(raw, dict):
        print("error: profiles file must be a JSON object", file=sys.stderr)
        return 2

    init_db()
    prepared = []
    for person, profile in raw.items():
        try:
            prepared.append((person, normalize_for_import(profile)))
        except Exception as exc:  # noqa: BLE001 - report every bad profile
            print(f"error: {person}: {exc}", file=sys.stderr)
            return 2

    for person, profile in prepared:
        print(f"{person}: {profile['from']['name']} → {profile['to']['name']}, "
              f"days={','.join(profile['days'])}, "
              f"alerts={','.join(profile['notify_times'])}")
        if not args.dry_run:
            store.save_profile(person, profile, agent="import")
    print("dry run: nothing written" if args.dry_run else f"imported {len(prepared)} profile(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

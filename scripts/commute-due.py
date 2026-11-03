#!/usr/bin/env python3
"""Call Eshu's commute scheduler for one person.

This is the dumb trigger the harness cron runs every minute. All decisions
(day gate, notify times, dedupe, text) live in Eshu.

Usage:
    ESHU_URL=http://192.168.1.114:8000 ESHU_TOKEN=... \
        python3 scripts/commute-due.py --person megan

Prints the message to stdout when `send` is true; prints nothing on a quiet
slot. Diagnostics go to stderr.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="Eshu commute scheduler trigger")
    parser.add_argument("--person", required=True)
    parser.add_argument("--url", default=os.environ.get("ESHU_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.environ.get("ESHU_TOKEN", ""))
    parser.add_argument("--at", default=None, help="Optional HH:MM override")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compose but do not mark the slot sent")
    args = parser.parse_args()

    if not args.token:
        print("error: ESHU_TOKEN is not set", file=sys.stderr)
        return 2

    body = json.dumps({
        "person": args.person,
        "at": args.at,
        "dry_run": bool(args.dry_run),
    }).encode("utf-8")
    request = urllib.request.Request(
        args.url.rstrip("/") + "/api/commute/due",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {args.token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        print(f"error: HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}",
              file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if not payload.get("send"):
        return 0
    text = str(payload.get("text") or "").strip()
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

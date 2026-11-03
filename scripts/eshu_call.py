#!/usr/bin/env python3
"""Generic authenticated client for Eshu REST capabilities.

One client per agent host, not one script per capability. Capability-specific
shims pass the path, body, and output field; all logic stays in Eshu.

Examples:
    eshu_call.py --path /api/commute/due --body '{"person":"gui"}' \
        --print-field text --when-field send

    ESHU_URL=http://192.168.1.114:8000 \
    ESHU_TOKEN_FILE=/path/to/token \
    eshu_call.py --path /api/commute/due --body '{"person":"megan"}'
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request

VERSION = "1.0.0"
DEFAULT_TIMEOUT = 30


def _die(message: str, code: int = 2) -> int:
    print(message, file=sys.stderr)
    return code


def _field(data, path: str):
    """Read a dotted field, e.g. 'send' or 'result.text'."""
    value = data
    for part in (path or "").split("."):
        if not part:
            continue
        if isinstance(value, dict):
            if part not in value:
                raise KeyError(part)
            value = value[part]
        elif isinstance(value, list):
            try:
                value = value[int(part)]
            except (ValueError, IndexError) as exc:
                raise KeyError(part) from exc
        else:
            raise KeyError(part)
    return value


def _read_token(args) -> str:
    token = args.token or ""
    if args.token_file:
        try:
            mode = stat.S_IMODE(os.stat(args.token_file).st_mode)
            if mode & 0o077:
                print(
                    f"warning: {args.token_file} is mode {oct(mode)}; use 0600",
                    file=sys.stderr,
                )
            with open(args.token_file, "r", encoding="utf-8") as handle:
                token = handle.read().strip()
        except OSError as exc:
            raise ValueError(f"cannot read token file: {exc}") from exc
    return token


def _read_body(args) -> tuple[bytes | None, dict | None]:
    raw = args.body
    if args.body_file:
        try:
            with open(args.body_file, "r", encoding="utf-8") as handle:
                raw = handle.read()
        except OSError as exc:
            raise ValueError(f"cannot read body file: {exc}") from exc
    if raw is None:
        return None, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"body is not valid JSON: {exc}") from exc
    return raw.encode("utf-8"), parsed


def _build_url(base: str, path: str) -> str:
    path = (path or "").strip()
    if not path:
        raise ValueError("--path is required")
    if "://" in path:
        raise ValueError("--path must be a path, not a full URL")
    if not path.startswith("/"):
        path = "/" + path
    base = (base or "").strip().rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("--url must be an http(s) base URL")
    return base + path


def _print_value(value):
    if isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False, indent=2))
    elif value is None:
        print("")
    else:
        print(str(value))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Call an Eshu REST capability with an agent token.")
    parser.add_argument("--url", default=os.environ.get("ESHU_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--path", required=True,
                        help="Eshu path, e.g. /api/commute/due")
    parser.add_argument("--method", default=None,
                        help="HTTP method (default: POST with a body, else GET)")
    parser.add_argument("--body", default=None, help="JSON body string")
    parser.add_argument("--body-file", default=None, help="Read the JSON body from a file")
    parser.add_argument("--token", default=os.environ.get("ESHU_TOKEN", ""))
    parser.add_argument("--token-file", default=os.environ.get("ESHU_TOKEN_FILE", ""))
    parser.add_argument("--header", action="append", default=[],
                        help="Extra header as 'Name: Value' (repeatable)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--json", action="store_true", help="Print the full JSON response")
    parser.add_argument("--raw", action="store_true", help="Print the raw response body")
    parser.add_argument("--print-field", default=None,
                        help="Print only this dotted field on success")
    parser.add_argument("--when-field", default=None,
                        help="Only print when this dotted field is truthy")
    parser.add_argument("--when-value", default=None,
                        help="Only print when --when-field equals this value")
    parser.add_argument("--error-field", default="message",
                        help="Field to print from an error response")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args(argv)

    try:
        token = _read_token(args)
        body, _parsed = _read_body(args)
        url = _build_url(args.url, args.path)
    except ValueError as exc:
        return _die(f"error: {exc}", 2)

    if not token:
        return _die("error: set ESHU_TOKEN, --token, or --token-file", 2)

    method = (args.method or ("POST" if body is not None else "GET")).upper()
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": f"eshu-call/{VERSION}",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    for item in args.header:
        name, _, value = item.partition(":")
        if name.strip():
            headers[name.strip()] = value.strip()

    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        message = raw.decode("utf-8", "replace")
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                message = str(
                    data.get(args.error_field)
                    or data.get("detail")
                    or data.get("message")
                    or message
                )
        except (ValueError, TypeError):
            pass
        return _die(f"error: HTTP {exc.code}: {message}", 3)
    except Exception as exc:  # noqa: BLE001
        return _die(f"error: {type(exc).__name__}: {exc}", 3)

    text = raw.decode("utf-8", "replace")
    if args.raw:
        sys.stdout.write(text)
        if text and not text.endswith("\n"):
            sys.stdout.write("\n")
        return 0

    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        return _die(f"error: response is not JSON: {exc}", 4)

    if args.when_field:
        try:
            gate = _field(data, args.when_field)
        except KeyError:
            return _die(f"error: response has no field {args.when_field!r}", 4)
        if args.when_value is not None:
            matched = str(gate).lower() == str(args.when_value).lower()
        else:
            matched = bool(gate)
        if not matched:
            return 0

    if args.json:
        _print_value(data)
        return 0

    if args.print_field:
        try:
            _print_value(_field(data, args.print_field))
        except KeyError:
            return _die(f"error: response has no field {args.print_field!r}", 4)
        return 0

    _print_value(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

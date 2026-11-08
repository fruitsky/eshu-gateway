"""F18: the two fingerprints the request context matches on.

`command_sha` identifies the exact bytes of a command across hosts (so the
sidebar can say "seen before"). `shape_sha` drops the arguments and keeps the
sequence of (verb, kind) over F1's steps (so it can say "like this").

Both are hashes of strings the dashboard already stores; no raw text is added
to the index.
"""
import hashlib

from core.cmd_parse import breakdown


def command_sha(command: str) -> str:
    return hashlib.sha256((command or '').encode('utf-8', 'surrogatepass')).hexdigest()


def shape_of(breakdown_result: dict) -> str:
    """`verb:kind` per step, joined — arguments are dropped on purpose."""
    parts = []
    for step in (breakdown_result or {}).get('steps') or []:
        verb = ((step.get('text') or '').split() or [''])[0]
        parts.append('%s:%s' % (verb, step.get('kind') or 'unknown'))
    return '|'.join(parts)


def shape_sha(command: str) -> str:
    return hashlib.sha256(shape_of(breakdown(command)).encode('utf-8')).hexdigest()


def fingerprints(command: str):
    """(command_sha, shape_sha) for one command."""
    return command_sha(command), shape_sha(command)

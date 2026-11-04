# `eshu_call.py` — generic Eshu REST client

One client per agent host, not one script per capability. Capability logic
stays in Eshu; the client only authenticates, sends JSON, and prints the field
the caller asked for.

## Install once

The client is served by Eshu as a versioned artifact:

```bash
curl -fsS \
  -H "Authorization: Bearer $ESHU_TOKEN" \
  -D /tmp/eshu-call.headers \
  -o /tmp/eshu-call.py \
  http://192.168.1.114:8000/api/clients/eshu-call.py

grep -i x-client- /tmp/eshu-call.headers
sha256sum /tmp/eshu-call.py
```

Install it beside the agent profile's scripts, e.g.:

```bash
install -o household -g household -m 755 \
  /tmp/eshu-call.py \
  /home/household/.hermes/profiles/household/scripts/eshu_call.py
```

The endpoint returns `X-Client-Version` and `X-Client-SHA256`; verify the hash
before installing.

## Usage

```bash
ESHU_URL=http://192.168.1.114:8000 \
ESHU_TOKEN_FILE=/home/household/.hermes/profiles/household/commute-token \
python3 eshu_call.py \
  --path /api/commute/due \
  --body '{"person":"megan"}' \
  --print-field text \
  --when-field send
```

- Prints `text` only when `send` is true; silent otherwise.
- `--json` prints the whole response.
- `--raw` prints the response body.
- `--body-file` reads JSON from a file.
- `--token-file` reads a mode-600 token file; a group/world-readable file
  produces a warning.
- `--error-field` chooses which field to put on stderr for an API error.
- Exit codes: `0` success/silent, `2` config, `3` HTTP/API error, `4` bad
  response shape.

## Shims

A shim contains only the endpoint and the person:

```python
#!/usr/bin/env python3
import sys
from eshu_call import main

raise SystemExit(main([
    "--path", "/api/commute/due",
    "--body", '{"person": "gui"}',
    "--print-field", "text",
    "--when-field", "send",
]))
```

For a shadow shim, add `"dry_run": true` to the body. For Megan, change the
person and filename.

The shim can rely on `ESHU_URL` and `ESHU_TOKEN_FILE` from the environment, or
pass `--url` / `--token-file` explicitly.

## What it replaces

- Capability-specific clients such as `scripts/commute-due.py` (which remains
  as a compatibility path).
- Future per-capability client scripts: adding a scheduler capability becomes a
  new shim, not a new HTTP implementation.
- The old local cron shims eventually, once the Eshu scheduler is cut over.

It does **not** replace Eshu MCP tools, the `household-trains` skill, or
server-side admin scripts such as `scripts/import-commute-profiles.py`.

## Why this shape

- Eshu owns validation, state, and capability logic.
- The host owns only the schedule trigger, token file, and delivery target.
- A new capability does not require copying code from the capability server.
- The client is transport-only, so a bug there cannot change what a commute
  alert means.

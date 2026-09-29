# Contributing to Eshu Gateway

Thanks for your interest. This is the short version; the full developer
workflow (feature flags, installer regeneration, deploy) lives in
[`docs/DEV_GUIDE.md`](docs/DEV_GUIDE.md).

## Setup

```bash
git clone https://github.com/fruitsky/eshu-gateway.git
cd eshu-gateway
pip install -r requirements-dev.txt   # runtime + test dependencies
```

## Checks before you commit

```bash
bash scripts/pre-commit.sh            # syntax checks (bash, python, node)
python3 -m pytest tests/ -q           # full test suite
```

Optionally install the git hook so the syntax checks run automatically:

```bash
bash scripts/setup-git-hooks.sh
```

## Pull requests

- Branch off `master`, keep commits focused and the message descriptive.
- Make sure `scripts/pre-commit.sh` and the test suite both pass.
- If you touched `dashboard/eshu-gateway.sh`, `eshu-poller.sh`, or
  `eshu-logger.sh`, regenerate the installer and commit the result:
  `python3 dashboard/gen_installer.py`.
- New features and shipped behavior changes should be noted in
  [`CHANGELOG.md`](CHANGELOG.md).

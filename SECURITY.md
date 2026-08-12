# Security Policy

## Supported versions

Eshu Gateway is a homelab-focused hobby project. Only the latest `master` is
actively supported; bug fixes ship to `master` and are deployed to the fleet via
the dashboard's Build → Edge → Fleet pipeline.

## Reporting a vulnerability

If you find a security issue — a way to bypass the policy gate, escalate
privileges through the gateway, leak credentials, or anything similar — please
**do not open a public issue**. Report it privately so we can fix it before it
is disclosed:

- **Preferred:** email the repository maintainer (see the GitHub profile for
  `fruitsky/eshu-gateway`).
- **Alternative:** open a GitHub issue with the `security` label, keeping the
  details vague, and we will follow up.

Please include:

- The affected version (commit hash or the dashboard's reported version).
- A description of the issue and steps to reproduce.
- Your suggested fix, if you have one.

## Disclosure

We aim to acknowledge reports within 5 business days and ship a fix as soon as
practical. The fix ships to `master`, then to the fleet through the normal
deploy pipeline. We will credit responsible reporters (unless you prefer to stay
anonymous).

## Scope

This policy covers the Eshu Gateway codebase in this repository. Third-party
dependencies should be reported to their respective maintainers.

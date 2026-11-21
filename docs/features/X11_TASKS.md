# X11 Stage 5 — the operator's task map

The agent screens are organised around what the operator is trying to do, not
around the database. Each task has one obvious place and one save path.

Screenshots are in `docs/screenshots/x11/stage5/` at 1440 and 390. Click
counts come from `tests/ui/x11_tasks.cjs`; typing and filling a field are not
counted. "Open the agent" counts as one click.

| Task | Path | Clicks | Screenshot |
|---|---|---:|---|
| T1 Create an agent | Agents → **New agent** (modal) | 7 | `t1-name-1440.png`, `t1-integrations-1440.png`, `t1-machines-1440.png`, `t1-review-1440.png`, `t1-token-1440.png` (and `-390`) |
| T2 Let an agent run commands | Agents → agent → **Run commands** (`#agent/<id>/run`) | 6 | `run-commands-1440.png`, `run-commands-390.png` |
| T3 Let an agent use an integration | agent → **Tools** → integration (`#agent/<id>/tools/<name>`) | 7 | `integration-sonarr-1440.png`, `integration-sonarr-390.png` |
| T4 Let an agent switch devices | agent → **Tools** → Home Assistant → **Devices** | 7 | `ha-devices-1440.png`, `ha-devices-390.png` |
| T5 Protect a device / home-wide limits | agent → **Tools** → Home Assistant → **Home-wide**; also Integrations → Home Assistant → **Home-wide rules** (`#homewide`) | 7 | `ha-homewide-1440.png`, `ha-homewide-390.png` |
| T6 See what an agent did | Agents → agent → **Activity** (`#agent/<id>/activity`) | 2 | `agents-1440.png`, `agents-390.png` |
| T7 Stop an agent | agent → **Overview** → **Danger zone** | 2 | `agents-1440.png` (the card opens it) |
| T8 Set up end-to-end testing | agent → **Overview** → **Use automated testing…** | 6 | `t8-step1-1440.png`, `t8-step2-1440.png`, `t8-step3-1440.png` (and `-390`) |
| T9 Let an agent fetch websites | agent → **Websites** (`#agent/<id>/websites`) | 5 | `websites-1440.png`, `websites-390.png` |

## T1 — Create an agent

1. Agents → **New agent**.
2. Name it and choose a purpose: **Household helper**, **Ops assistant**,
   **Observer**, **Test agent** or **Start empty**.
3. Tick the integrations. The template pre-ticks these; nothing is special.
4. Tick machines (optional). Picking one adds `ssh_exec` automatically.
5. Read the plain-words review, then **Create agent**.
6. Copy the token. The same step shows the MCP URL and the `Authorization:
   Bearer` header. The token is shown once.

The create is a normal `POST /api/agents` followed by a normal S5 preview and
apply. `Test agent` is only a head start; T8 finishes it.

## T2 — Run commands

One section owns SSH: the machine list, the search, **Any machine**, and the
three modes. There is no second tool switch. Picking a machine adds
`ssh_exec` to an allowlist agent in the same save; removing the last machine
removes it. The page says **Pick the machines this agent may run commands
on.** The three modes are **Follow each machine's rules** (normal),
**Always ask me** (approval) and **Read-only**.

## T3 — Use an integration

The Tools overview is still the tile grid with the access bar. Opening one
integration gives the tool rows and two questions at the top:

* **Allow all reading** sets every reading row to run.
* **Changes ask me first** sets every changing row to ask.

Both still go through the normal Check → Apply save.

## T4 — Devices

Home Assistant → **Devices** keeps the Stage 4 room grid and the pill cycle:
ask → run → ask. The protected-device link goes to the Home-wide tab.

## T5 — Home-wide rules

All of the old "Rules for all agents" content now lives in Home Assistant →
**Home-wide**, under the banner **Applies to every agent**:

* protected devices, with the picker;
* the limits for every agent;
* the never-allowed reference and the operator's words;
* the "how permissions work" text.

The same page is reachable from Integrations → Home Assistant → **Home-wide
rules** (`#homewide`). The Agents page has no top-level tab bar.

## T6 — What happened, and what needs me

The Agents page has three linked counters: **Waiting for you**, **Paused
permissions** and **Devices waiting to join**. Each card carries the type
badge, status, the latest activity in words, up to four integration access
bars, the machines it can reach, and an amber needs-you line. The whole card
is the link.

## T7 — Stop an agent

Overview → **Danger zone** keeps **Revoke token** and **Delete agent**, with
the existing confirmations.

## T8 — Automated testing

Overview → **Use automated testing…**:

1. Pick exactly one machine. The panel lists exactly what will change: test
   agent on, the HA reads + `call_service` + `ssh_exec` allowlist, **Follow
   each machine's rules** on that machine, and no ungated approvals.
2. **Apply** performs one S5 apply and the existing test-agent switch.
3. The command list is loaded from `/api/e2e/commands`, which returns
   `scripts/e2e/run.py COMMANDS`, so it cannot drift. The machine is already
   filled and the lifetime is 7 days. **Create test approval token** shows the
   `e2e_` token once, with Copy.

`tests/test_x11_stage5.py` passes the resulting policy through the suite's own
`run.check_test_agent`, so the T8 result is tested against the preflight, not
a copy of its rules.

## T9 — Websites

One **Websites** section: on/off, the allowed-site list, and the normal save.
There is no separate tool switch. An allowlist agent gets `web_fetch_url`
when the setting is on and at least one site is listed; the tool key is
removed when it is off or the list is empty.

## Saving

Every section with unsaved edits says **Not saved** beside its title. The
sticky save bar names the changed sections ("2 changes in Home Assistant")
and offers **Save** / **Discard**. Leaving the agent, switching agent or
closing the tab asks first. After Apply, a short confirmation names what was
saved, and it is still there after a reload. A change with no risk items gets
a one-sentence preview and **Apply**, with no extra headings.

The task tests are `tests/ui/x11_tasks.cjs` (run by
`tests/test_ui_views.py`); the screenshot harness is
`tests/ui/x11_shots.cjs`.

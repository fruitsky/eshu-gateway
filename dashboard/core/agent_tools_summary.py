"""X11 Stage 4: one counting rule for the agent Tools overview.

The one-line summary, every integration tile and the Overview ledger all read
the same numbers from here. Keeping the counting on the server means a Python
test can prove the sentence matches the tiles, and the UI never invents a
second rule.

Read-only: this module never writes, never carries a secret and never calls an
agent-facing endpoint.
"""
import json
import time

from core import allowances
from core.agent_config import get_agent_config, ssh_host_words
from core.agent_policy import safe_ident, static_tool_decision
from core.seeds import seed_label
from db.agent_policies import get_agent_policy
from db.agent_tokens import get_agent
from db.gateways import get_trigger_freeze
from db.integrations import (
    get_integration_config, get_integrations, get_tools,
)

KIND_WORDS = {
    'ha': 'Home',
    'ha_ssh': 'Files',
    'sonarr': 'TV',
    'radarr': 'TV',
    'prowlarr': 'TV',
    'bazarr': 'TV',
    'jellyfin': 'TV',
    'seerr': 'TV',
    'trains': 'Trains',
    'proxmox': 'Servers',
    'pihole': 'Network',
    'omada': 'Network',
    'npm': 'Network',
    'pulse': 'Health',
    'webfetch': 'Web',
    'commute': 'Commute',
    'custom': 'Other',
    'ssh': 'SSH',
    'websites': 'Web',
}

# A two-letter mark is a quiet visual anchor, not an icon set. Two named
# integrations can share a kind (Sonarr and Radarr are both TV), so the mark
# follows the name and only falls back to the kind.
MARK_BY_NAME = {
    'Home Assistant': 'HA',
    'Sonarr': 'SO',
    'Radarr': 'RA',
    'Prowlarr': 'PR',
    'Bazarr': 'BA',
    'Jellyfin': 'JE',
    'Seerr': 'SE',
    'Pi-hole': 'PI',
    'Omada': 'OM',
    'Nginx Proxy Manager': 'NP',
    'Proxmox': 'PX',
    'Pulse': 'PU',
    'Trains': 'TR',
    'Commute': 'CO',
    'Web fetch': 'WF',
    'Files (SSH add-on)': 'FI',
    'SSH': 'SS',
    'Websites': 'WB',
}


def _mark(name, kind):
    if name in MARK_BY_NAME:
        return MARK_BY_NAME[name]
    for key, value in MARK_BY_NAME.items():
        if key.lower() == (name or '').lower():
            return value
    letters = ''.join(c for c in (name or '') if c.isalnum())
    return (letters[:2] or (kind or '??')[:2]).upper()


def _kind_words(kind):
    return KIND_WORDS.get((kind or 'custom').lower(),
                         (kind or 'Other').replace('_', ' ').title())


def _live_permission(permission, now):
    if permission.get('suspended_at'):
        return False
    expires = int(permission.get('expires_at') or 0)
    return not expires or expires > now


def _permission_rows(agent_id, config):
    """The live HA permissions with their pins and waiting joiners.

    The S5 document omits `entities` for a label permission because the label
    is its source of truth. Here the pins are what matters: the label's
    current members can include waiting joiners that were never accepted.
    """
    try:
        rows = [r for r in allowances.list_allowances()
                if int(r.get('agent_id') or 0) == int(agent_id)
                and (r.get('integration') or '') == 'ha']
    except Exception:  # noqa: BLE001 - a summary must not fail a page
        rows = []
    if rows:
        return rows
    return ((config.get('home_assistant') or {}).get('permissions') or [])


def _permission_pins(permission):
    try:
        pins = json.loads(permission.get('entities_json') or '[]')
    except (TypeError, ValueError):
        pins = permission.get('entities') or []
    out = []
    for pin in pins:
        if isinstance(pin, dict):
            entity_id = pin.get('entity_id')
        else:
            entity_id = pin
        entity_id = str(entity_id or '').lower()
        if entity_id:
            out.append(entity_id)
    return out


def _permission_waiting(permission):
    try:
        pending = json.loads(permission.get('pending_json') or '[]')
    except (TypeError, ValueError):
        pending = permission.get('pending') or []
    out = []
    for pin in pending:
        if isinstance(pin, dict):
            entity_id = pin.get('entity_id')
        else:
            entity_id = pin
        entity_id = str(entity_id or '').lower()
        if entity_id:
            out.append(entity_id)
    return out


def _device_noun(device_rows):
    categories = {d.get('category') or '' for d in device_rows}
    if categories == {'switch'}:
        return 'plugs'
    if categories == {'light'}:
        return 'lights'
    if categories == {'cover'}:
        return 'covers'
    return 'devices'


def _ha_scope(agent_id, config, devices):
    """(pinned devices, waiting entity ids) for the live HA permissions."""
    now = int(time.time())
    by_id = {d['id']: d for d in devices}
    pinned = {}
    waiting = {}
    for permission in _permission_rows(agent_id, config):
        if not _live_permission(permission, now):
            continue
        pins = _permission_pins(permission)
        for entity_id in pins:
            pinned[entity_id] = by_id.get(entity_id) or {
                'id': entity_id, 'category': '', 'labels': [],
            }
        for entity_id in _permission_waiting(permission):
            if entity_id not in pinned:
                waiting[entity_id] = True
    return [pinned[e] for e in pinned], sorted(waiting)


def _files_parent(integration, enabled_ha):
    """The enabled HA integration a Files row belongs to, or ''."""
    config = get_integration_config(integration)
    parent = (config.get('ha_integration') or '').strip()
    if not parent and len(enabled_ha) == 1:
        parent = enabled_ha[0]['name']
    if any(h['name'] == parent for h in enabled_ha):
        return parent
    return ''


DISPLAY_BY_KIND = {
    'commute': 'Commute',
    'trains': 'TfL',
    'webfetch': 'Web fetch',
}


def _display_name(name, kind):
    """A curated integration display name, else the name with a capital."""
    known = DISPLAY_BY_KIND.get((kind or '').lower())
    if known:
        return known
    text = str(name or '')
    return text[:1].upper() + text[1:] if text else text


def _tile(name, kind, total=0, run=0, ask=0, depends=0, frozen=0,
          waiting=0, extra='', in_use=None, virtual=False, display=None):
    # `frozen` is a state, not an outcome: a frozen write is still counted in
    # its run/ask/depends bucket so the frozen and unfrozen decisions match.
    none = max(0, total - run - ask - depends)
    usable = total - none
    return {
        'key': name,
        'name': name,
        'display': display or _display_name(name, kind),
        'kind': kind,
        'kind_words': _kind_words(kind),
        'mark': _mark(name, kind),
        'total': total,
        'run': run,
        'ask': ask,
        'depends': depends,
        'frozen': frozen,
        'none': none,
        'usable': usable,
        'waiting': waiting,
        'in_use': bool(usable) if in_use is None else bool(in_use),
        'extra': extra,
        'virtual': virtual,
    }


def build(agent_id):
    """The summary payload for one agent. Raises AllowanceError from config."""
    from core import ha_devices

    config = get_agent_config(agent_id)
    agent = get_agent(agent_id) or {}
    agent['policy'] = get_agent_policy(agent_id)
    frozen = bool(get_trigger_freeze())
    integrations = [i for i in get_integrations() if i.get('enabled')]
    enabled_ha = [i for i in integrations
                  if (i.get('kind') or '').lower() == 'ha']
    files_by_parent = {}
    standalone_files = []
    for integration in integrations:
        if (integration.get('kind') or '').lower() != 'ha_ssh':
            continue
        parent = _files_parent(integration, enabled_ha)
        if parent:
            files_by_parent.setdefault(parent, []).append(integration)
        else:
            standalone_files.append(integration)

    try:
        devices = ha_devices.devices()
    except Exception:  # noqa: BLE001 - a summary must not fail a page
        devices = []
    ha_rows, waiting = _ha_scope(agent_id, config, devices)
    noun = _device_noun(ha_rows)

    tiles = []
    tool_outcomes = {}
    frozen_tools = {}

    def _count(owner, tools):
        outcomes = []
        frozen_count = 0
        for tool in tools:
            namespaced = '%s_%s' % (
                safe_ident(owner['name']).lower(), tool['name'])
            decision = static_tool_decision(
                agent, owner, tool['name'], tool, frozen=frozen)
            tool_outcomes[namespaced] = decision['outcome']
            frozen_tools[namespaced] = bool(decision['frozen'])
            if decision['frozen']:
                frozen_count += 1
            outcomes.append(decision['outcome'])
        return outcomes, frozen_count

    for integration in integrations:
        kind = (integration.get('kind') or 'custom').lower()
        if kind == 'ha_ssh' and integration not in standalone_files:
            continue
        tools = [t for t in get_tools(integration['id']) if t.get('enabled')]
        outcomes, frozen_count = _count(integration, tools)
        if kind == 'ha':
            for child in files_by_parent.get(integration['name'], []):
                child_tools = [t for t in get_tools(child['id'])
                               if t.get('enabled')]
                child_outcomes, child_frozen = _count(child, child_tools)
                outcomes.extend(child_outcomes)
                frozen_count += child_frozen
        total = len(outcomes)
        run = outcomes.count('run')
        ask = outcomes.count('ask')
        depends = outcomes.count('depends')
        extra = ''
        in_use = None
        waiting_count = 0
        if kind == 'ha':
            waiting_count = len(waiting)
            if ha_rows:
                extra = '%d %s run on their own' % (len(ha_rows), noun)
            if waiting_count:
                extra = (extra + '; ' if extra else '') + (
                    '%d waiting to join' % waiting_count)
            in_use = bool(total or ha_rows or waiting_count)
        tiles.append(_tile(integration['name'], kind, total, run, ask,
                           depends, frozen_count, waiting_count,
                           extra=extra, in_use=in_use))

    # Unlinked Files rows are integrations too; their tools still count.
    for integration in standalone_files:
        tools = [t for t in get_tools(integration['id']) if t.get('enabled')]
        outcomes, frozen_count = _count(integration, tools)
        tiles.append(_tile(
            integration['name'], 'ha_ssh', len(outcomes),
            outcomes.count('run'), outcomes.count('ask'),
            outcomes.count('depends'), frozen_count))

    ssh = (config.get('ssh') or {})
    hosts = ssh.get('hosts') or []
    mode = ssh.get('mode') or 'approval'
    # The SSH tile is the same static decision the agent's `ssh_exec` gets:
    # allowlist, caller, approval and gate mode. Running commands is not
    # implied by a host list alone. `ssh_mode` maps to the decision this way:
    # approval -> always ask; normal -> depends on the gateway's policy;
    # read_only -> a read-only run.
    ssh_tool = {
        'name': 'exec',
        'read_only': mode == 'read_only',
        'always_gate': mode == 'approval',
    }
    ssh_owner = {
        'name': 'ssh',
        'gate_mode': 'all' if mode == 'approval' else 'destructive',
    }
    ssh_decision = static_tool_decision(agent, ssh_owner, 'exec', ssh_tool,
                                        frozen=frozen)
    if not hosts:
        ssh_decision = {'outcome': 'none', 'allowed': False, 'frozen': False,
                        'reason': 'no SSH hosts'}
    ssh_outcome = ssh_decision['outcome']
    tool_outcomes['ssh_exec'] = ssh_outcome
    frozen_tools['ssh_exec'] = bool(ssh_decision.get('frozen'))
    ssh_extra = ('No hosts yet' if not hosts
                 else 'Any machine' if hosts == ['*']
                 else 'On ' + ssh_host_words(hosts))
    tiles.append(_tile(
        'SSH', 'ssh', 1,
        1 if ssh_outcome == 'run' else 0,
        1 if ssh_outcome == 'ask' else 0,
        1 if ssh_outcome == 'depends' else 0,
        1 if ssh_decision.get('frozen') else 0,
        extra=ssh_extra, in_use=ssh_outcome != 'none', virtual=True))

    web = (config.get('websites') or {})
    sites = web.get('allow') or []
    web_extra = ('%d sites allowed' % len(sites) if sites
                 else 'No sites allowed')
    tiles.append(_tile('Websites', 'websites', 1,
                       1 if web.get('enabled') and sites else 0, 0,
                       extra=web_extra, in_use=bool(web.get('enabled')),
                       virtual=True))

    tiles.sort(key=lambda t: (not t['in_use'], t['name'].lower()))
    tools_total = sum(t['usable'] for t in tiles)
    run_total = sum(t['run'] for t in tiles)
    ask_total = sum(t['ask'] for t in tiles)
    depends_total = sum(t['depends'] for t in tiles)
    frozen_total = sum(t['frozen'] for t in tiles)
    waiting_total = len(waiting)
    used = sum(1 for t in tiles if t['in_use'])
    name = (config.get('agent') or {}).get('name') or 'This agent'
    if tools_total:
        if frozen_total:
            summary = ('%s has %d tools in %d of %d integrations. '
                       '%d run on their own; %d ask you first'
                       % (name, tools_total, used, len(tiles), run_total,
                          ask_total))
            if depends_total:
                summary += '; %d depend on the call' % depends_total
            summary += ('. Emergency Freeze is on: %d write%s paused.'
                        % (frozen_total, '' if frozen_total == 1 else 's'))
        else:
            summary = ('%s can use %d tools in %d of %d integrations. '
                       '%d ask you first; %d run on their own'
                       % (name, tools_total, used, len(tiles), ask_total,
                          run_total))
            if depends_total:
                summary += '; %d depend on the call' % depends_total
        if ha_rows:
            summary += ', plus %d %s' % (len(ha_rows), noun)
        if waiting_total:
            summary += '; %d waiting to join' % waiting_total
        summary += '.'
    elif ha_rows or waiting_total:
        if ha_rows:
            summary = ('%s has no tools switched on. It can still use %d %s '
                       'through Home Assistant.' % (name, len(ha_rows), noun))
        else:
            summary = ('%s has no tools switched on. %d device%s waiting to '
                       'join.' % (name, waiting_total,
                                  '' if waiting_total == 1 else 's'))
        if waiting_total and ha_rows:
            summary += ' %d waiting to join.' % waiting_total
    else:
        summary = '%s has no tools switched on yet.' % name
    return {
        'agent': {'id': int(agent_id), 'name': name},
        'summary': summary,
        'frozen': frozen,
        'tool_outcomes': tool_outcomes,
        'frozen_tools': frozen_tools,
        'totals': {
            'tools': tools_total,
            'run': run_total,
            'ask': ask_total,
            'depends': depends_total,
            'frozen': frozen_total,
            'integrations': len(tiles),
            'in_use': used,
            'devices': len(ha_rows),
            'device_noun': noun,
            'waiting': waiting_total,
        },
        'tiles': tiles,
    }

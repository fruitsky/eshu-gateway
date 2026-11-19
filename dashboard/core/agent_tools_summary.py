"""X11 Stage 4: one counting rule for the agent Tools overview.

The one-line summary, every integration tile and the Overview ledger all read
the same numbers from here. Keeping the counting on the server means a Python
test can prove the sentence matches the tiles, and the UI never invents a
second rule.

Read-only: this module never writes, never carries a secret and never calls an
agent-facing endpoint.
"""
import time

from core.agent_config import get_agent_config
from core.agent_policy import safe_ident
from core.seeds import seed_label
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


def tool_outcome(tool, config):
    """The one outcome rule: none, ask or run.

    Defaults are the tool's own nature: a read runs, a change asks. An
    explicit policy row can make a read ask or a change run, except that an
    ``always_gate`` tool can never be made to run.
    """
    tools = (config or {}).get('tools') or {}
    name = tool.get('name') or ''
    if (tools.get('mode') or 'all') == 'allowlist':
        if name not in (tools.get('allow') or []):
            return 'none'
    approval = (tools.get('approval') or {}).get(name)
    if approval == 'gated':
        return 'ask'
    if approval == 'ungated' and not tool.get('always_gate'):
        return 'run'
    return 'run' if tool.get('read_only') else 'ask'


def _live_permission(permission, now):
    if permission.get('suspended_at'):
        return False
    expires = int(permission.get('expires_at') or 0)
    return not expires or expires > now


def _permission_entities(permission, devices):
    label = permission.get('label_id') or ''
    if label:
        return [d['id'] for d in devices
                if any((item.get('id') or '') == label
                       for item in (d.get('labels') or []))]
    return [str(e) for e in (permission.get('entities') or [])]


def _device_noun(device_rows):
    categories = {d.get('category') or '' for d in device_rows}
    if categories == {'switch'}:
        return 'plugs'
    if categories == {'light'}:
        return 'lights'
    if categories == {'cover'}:
        return 'covers'
    return 'devices'


def _ha_devices(config, devices):
    now = int(time.time())
    rows = {}
    for permission in ((config.get('home_assistant') or {}).get('permissions')
                       or []):
        if not _live_permission(permission, now):
            continue
        for entity_id in _permission_entities(permission, devices):
            entity_id = str(entity_id or '').lower()
            if entity_id:
                rows[entity_id] = True
    by_id = {d['id']: d for d in devices}
    return [by_id[e] for e in rows if e in by_id]


def _files_parent(integration, enabled_ha):
    """The enabled HA integration a Files row belongs to, or ''."""
    config = get_integration_config(integration)
    parent = (config.get('ha_integration') or '').strip()
    if not parent and len(enabled_ha) == 1:
        parent = enabled_ha[0]['name']
    if any(h['name'] == parent for h in enabled_ha):
        return parent
    return ''


def _tile(name, kind, total=0, run=0, ask=0, extra='', in_use=None,
          virtual=False):
    none = max(0, total - run - ask)
    usable = run + ask
    return {
        'key': name,
        'name': name,
        'kind': kind,
        'kind_words': _kind_words(kind),
        'mark': _mark(name, kind),
        'total': total,
        'run': run,
        'ask': ask,
        'none': none,
        'usable': usable,
        'in_use': bool(usable) if in_use is None else bool(in_use),
        'extra': extra,
        'virtual': virtual,
    }


def build(agent_id):
    """The summary payload for one agent. Raises AllowanceError from config."""
    from core import ha_devices

    config = get_agent_config(agent_id)
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

    tiles = []
    for integration in integrations:
        kind = (integration.get('kind') or 'custom').lower()
        if kind == 'ha_ssh' and integration not in standalone_files:
            continue
        rows = []
        for tool in get_tools(integration['id']):
            if not tool.get('enabled'):
                continue
            namespaced = '%s_%s' % (
                safe_ident(integration['name']).lower(), tool['name'])
            rows.append({
                'name': namespaced,
                'read_only': bool(tool.get('read_only')),
                'always_gate': bool(tool.get('always_gate')),
            })
        if kind == 'ha':
            for child in files_by_parent.get(integration['name'], []):
                for tool in get_tools(child['id']):
                    if not tool.get('enabled'):
                        continue
                    rows.append({
                        'name': '%s_%s' % (
                            safe_ident(child['name']).lower(), tool['name']),
                        'read_only': bool(tool.get('read_only')),
                        'always_gate': bool(tool.get('always_gate')),
                    })
        outcomes = [tool_outcome(row, config) for row in rows]
        run = outcomes.count('run')
        ask = outcomes.count('ask')
        total = len(outcomes)
        extra = ''
        in_use = None
        if kind == 'ha':
            try:
                devices = _ha_devices(config, ha_devices.devices())
            except Exception:  # noqa: BLE001 - a summary must not fail a page
                devices = []
            noun = _device_noun(devices)
            extra = ('%d %s run on their own' % (len(devices), noun)
                     if devices else '')
            in_use = bool(total or devices)
        tiles.append(_tile(integration['name'], kind, total, run, ask,
                           extra=extra, in_use=in_use))

    # Unlinked Files rows are integrations too; their tools still count.
    for integration in standalone_files:
        rows = [t for t in get_tools(integration['id']) if t.get('enabled')]
        outcomes = [tool_outcome({
            'name': '%s_%s' % (safe_ident(integration['name']).lower(),
                               t['name']),
            'read_only': bool(t.get('read_only')),
            'always_gate': bool(t.get('always_gate')),
        }, config) for t in rows]
        tiles.append(_tile(integration['name'], 'ha_ssh', len(rows),
                           outcomes.count('run'), outcomes.count('ask')))

    ssh = (config.get('ssh') or {})
    hosts = ssh.get('hosts') or []
    mode = ssh.get('mode') or 'approval'
    ssh_extra = ('%d hosts' % len(hosts) if hosts else 'No hosts yet')
    if len(hosts) == 1 and hosts[0] == '*':
        ssh_extra = 'Any gateway'
    ssh_run = 1 if hosts and mode == 'normal' else 0
    ssh_ask = 1 if hosts and mode != 'normal' else 0
    tiles.append(_tile('SSH', 'ssh', 1, ssh_run, ssh_ask, extra=ssh_extra,
                       in_use=bool(hosts), virtual=True))

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
    used = sum(1 for t in tiles if t['in_use'])
    try:
        devices = _ha_devices(config, ha_devices.devices())
    except Exception:  # noqa: BLE001
        devices = []
    noun = _device_noun(devices)
    name = (config.get('agent') or {}).get('name') or 'This agent'
    if tools_total:
        summary = ('%s can use %d tools in %d of %d integrations. '
                   '%d ask you first; %d run on their own'
                   % (name, tools_total, used, len(tiles), ask_total,
                      run_total))
        if devices:
            summary += ', plus %d %s' % (len(devices), noun)
        summary += '.'
    elif devices:
        summary = ('%s has no tools switched on. It can still use %d %s '
                   'through Home Assistant.' % (name, len(devices), noun))
    else:
        summary = '%s has no tools switched on yet.' % name
    return {
        'agent': {'id': int(agent_id), 'name': name},
        'summary': summary,
        'totals': {
            'tools': tools_total,
            'run': run_total,
            'ask': ask_total,
            'integrations': len(tiles),
            'in_use': used,
            'devices': len(devices),
            'device_noun': noun,
        },
        'tiles': tiles,
    }

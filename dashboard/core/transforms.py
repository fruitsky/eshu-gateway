"""Response transforms for tools that need more than field projection.

Seed catalogs tag a tool with `transform: <name>`. When set, the shaping layer
(`core.integration_proxy._apply_shaping`) runs the registered transform against
the parsed upstream body and returns its JSON output instead of doing plain
field projection. Transforms own the whole result — compact projections,
merges, downsampling, and error envelopes (e.g. `invalid_range`). This is the
escape hatch for multi-part / keyed responses (fleet summary, charts series,
backup merge, health+version) that a flat `fields` list can't express.

Transforms are single-request where possible: the only one that makes an extra
upstream call is `pulse_health` (version fetch). Pulse responses carry no
envelope, so transforms receive the body JSON directly.
"""
import json
import urllib.parse
import urllib.request

from core.integration_proxy import (
    DEFAULT_TIMEOUT,
    MAX_BODY_BYTES,
    _auth_headers,
    _guard_ssrf,
    _ssl_context,
)

PULSE_CHART_RANGES = ('5m', '15m', '30m', '1h', '4h', '12h', '24h', '7d')


def _err(code: str, message: str) -> str:
    return json.dumps({'error': code, 'message': message})


def _get_json(integration, path: str, params: dict = None):
    """Small internal GET against the integration, returning parsed JSON.
    Raises on network/parse failure — callers decide how to degrade."""
    path = (path or '').lstrip('/')
    base_url = (integration.get('base_url') or '').rstrip('/')
    _guard_ssrf(base_url, path)
    url = base_url + '/' + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method='GET',
                                 headers=_auth_headers(integration))
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT,
                                context=_ssl_context(integration)) as resp:
        raw = resp.read(MAX_BODY_BYTES + 1)
    return json.loads(raw.decode('utf-8', errors='replace'))


def _compact_resource(item: dict, full: bool) -> dict:
    """Compact projection of one Pulse resource entry."""
    out = {}
    for key in ('id', 'name', 'type', 'status'):
        if item.get(key) is not None:
            out[key] = item[key]
    cpu = item.get('cpu') or {}
    mem = item.get('memory') or {}
    disk = item.get('disk') or {}
    net = item.get('network') or {}
    if cpu.get('current') is not None:
        out['cpuPct'] = cpu['current']
    if mem.get('current') is not None:
        out['memPct'] = mem['current']
    if mem.get('total') is not None:
        out['memUsed'] = mem.get('used') or 0
        out['memFree'] = mem.get('free') or 0
        out['memTotal'] = mem['total']
    if disk.get('current') is not None:
        out['diskPct'] = disk['current']
    if disk.get('total') is not None:
        out['diskUsed'] = disk.get('used') or 0
        out['diskFree'] = disk.get('free') or 0
        out['diskTotal'] = disk['total']
    pd = item.get('platformData') or {}
    ips = pd.get('ipAddresses') or []
    if ips:
        out['ip'] = ips
    alerts = item.get('alerts') or []
    if alerts:
        out['alerts'] = [
            {'type': a.get('type'), 'level': a.get('level'),
             'message': a.get('message')}
            for a in alerts if isinstance(a, dict)]
    if full:
        extras = {
            'node': pd.get('node'), 'osName': pd.get('osName'),
            'uptime': item.get('uptime'), 'lastBackup': pd.get('lastBackup'),
            'tags': item.get('tags'), 'vmid': pd.get('vmid'),
            'cpus': pd.get('cpus'), 'template': pd.get('template'),
            'trafficIn': net.get('rxBytes') or 0,
            'trafficOut': net.get('txBytes') or 0,
        }
        out.update({k: v for k, v in extras.items() if v is not None})
    return out


def _epoch(value):
    """Best-effort epoch-seconds sort key for mixed timestamp types (int/float
    epoch, ISO-8601 strings). Non-numeric junk falls back to 0."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except (ValueError, TypeError):
            pass
        try:
            from datetime import datetime
            return datetime.fromisoformat(value).timestamp()
        except (ValueError, TypeError):
            return 0
    return 0


def _slice(items: list, limit):
    if limit is None:
        return items
    try:
        n = int(limit)
        if n >= 0:
            return items[:n]
    except (TypeError, ValueError):
        pass
    return items


# ── Pulse transforms ────────────────────────────────────────────────────

def _health(integration, tool, args, data):
    out = {}
    if isinstance(data, dict):
        for key in ('status', 'uptime'):
            if data.get(key) is not None:
                out[key] = data[key]
    try:
        version = _get_json(integration, '/version')
        if isinstance(version, dict):
            for key in ('version', 'channel', 'deploymentType'):
                if version.get(key) is not None:
                    out[key] = version[key]
    except Exception:
        pass  # version is best-effort; health alone still returns
    return json.dumps(out)


def _fleet_summary(integration, tool, args, data):
    a = args or {}
    full = bool(a.get('full'))
    if not isinstance(data, dict) or not isinstance(data.get('resources'), list):
        return json.dumps(data)
    needle = str(a.get('search') or '').lower()
    out = []
    for item in data['resources']:
        if not isinstance(item, dict):
            continue
        if needle and needle not in str(item.get('name') or '').lower() \
                and needle not in str(item.get('id') or '').lower():
            continue
        out.append(_compact_resource(item, full))
    result = {'count': len(out), 'resources': _slice(out, a.get('limit'))}
    if full and isinstance(data.get('stats'), dict):
        result['stats'] = data['stats']
    return json.dumps(result)


def _get_resource(integration, tool, args, data):
    if not isinstance(data, dict):
        return json.dumps(data)
    item = data.get('resource') if isinstance(data.get('resource'), dict) else data
    return json.dumps(_compact_resource(item, bool((args or {}).get('full'))))


def _list_alerts(integration, tool, args, data):
    a = args or {}
    items = data if isinstance(data, list) else []
    needle = str(a.get('search') or '').lower()
    if needle:
        items = [x for x in items if isinstance(x, dict) and (
            needle in str(x.get('message') or '').lower()
            or needle in str(x.get('resourceName') or '').lower()
            or needle in str(x.get('id') or '').lower())]
    keys = ('id', 'level', 'type', 'resourceId', 'resourceName', 'node',
            'message', 'value', 'threshold', 'startTime', 'lastSeen',
            'acknowledged')
    out = []
    for x in items:
        if not isinstance(x, dict):
            continue
        row = {k: x.get(k) for k in keys}
        out.append({k: v for k, v in row.items() if v is not None})
    return json.dumps(_slice(out, a.get('limit')))


def _metric_points(series: dict, key: str) -> list:
    val = series.get(key)
    if isinstance(val, dict):
        pts = val.get('points')
        if isinstance(pts, list):
            return pts
    if isinstance(val, list):
        return val
    return []


def _downsample(points: list, max_points: int) -> list:
    if len(points) <= max_points:
        return points
    step = (len(points) + max_points - 1) // max_points
    return [p for i, p in enumerate(points) if i % step == 0]


def _project_series(series: dict, metric: str, max_points: int) -> dict:
    """Project one resource's series to the requested metric(s), downsampled
    to max_points each. Returns {} when nothing matches."""
    if metric:
        keys = [metric] if metric in series else []
    else:
        keys = [k for k in ('cpu', 'disk', 'diskread', 'diskwrite',
                            'memory', 'netin', 'netout') if k in series]
    out = {}
    for k in keys:
        out[k] = {'points': _downsample(_metric_points(series, k), max_points)}
    return out


def _charts(integration, tool, args, data):
    a = args or {}
    rng = a.get('range')
    if rng not in PULSE_CHART_RANGES:
        return _err('invalid_range',
                    f"range must be one of: {', '.join(PULSE_CHART_RANGES)}")
    if not isinstance(data, dict):
        return json.dumps(data)
    metric = a.get('metric')
    try:
        max_points = int(a.get('maxPoints') or 200)
    except (TypeError, ValueError):
        max_points = 200
    if max_points <= 0:
        max_points = 200

    resource = a.get('resource')
    if resource:
        host_series = (data.get('hostData') or {}).get(resource)
        node_series = (data.get('nodeData') or {}).get(resource)
        if host_series is not None:
            series = host_series
        elif node_series is not None:
            series = node_series
        else:
            series = (data.get('data') or {}).get(resource)
        if not isinstance(series, dict):
            return json.dumps({})
        return json.dumps({resource: _project_series(series, metric, max_points)})

    # No resource filter: one compact series per monitored resource,
    # downsampled, so the response stays bounded instead of raw charts.
    out = {}
    for res_id, series in (data.get('data') or {}).items():
        if not isinstance(series, dict):
            continue
        proj = _project_series(series, metric, max_points)
        if proj:
            out[res_id] = proj
    return json.dumps(out)


def _backups(integration, tool, args, data):
    a = args or {}
    if not isinstance(data, dict):
        return json.dumps(data)
    vmid = a.get('vmid')
    entries = []
    for b in data.get('pbsBackups') or []:
        if not isinstance(b, dict):
            continue
        vmid_val = b.get('vmid')
        if vmid_val in (None, '', 0):
            continue
        entries.append({
            'vmid': str(vmid_val), 'source': 'pbs', 'time': b.get('backupTime'),
            'size': b.get('size'), 'protected': b.get('protected'),
            'verified': b.get('verified'), 'datastore': b.get('datastore'),
            'status': 'ok',
        })
    for t in data.get('backupTasks') or []:
        if not isinstance(t, dict):
            continue
        vmid_val = t.get('vmid')
        if vmid_val in (None, '', 0):
            # Storage-level tasks (vmid 0) are not guest backups.
            continue
        entries.append({
            'vmid': str(vmid_val), 'source': 'task', 'time': t.get('start'),
            'size': None, 'protected': None, 'verified': None,
            'datastore': None, 'status': t.get('status'),
        })
    if vmid is not None:
        entries = [e for e in entries if e['vmid'] == str(vmid)]
    entries.sort(key=lambda e: _epoch(e.get('time')), reverse=True)
    return json.dumps(_slice(entries, a.get('limit')))


def _list_storage(integration, tool, args, data):
    a = args or {}
    if isinstance(data, dict):
        items = data.get('resources') or []
    else:
        items = data if isinstance(data, list) else []
    needle = str(a.get('search') or '').lower()
    out = []
    for x in items:
        if not isinstance(x, dict) or x.get('type') != 'storage':
            continue
        if needle and needle not in str(x.get('name') or '').lower() \
                and needle not in str(x.get('id') or '').lower():
            continue
        disk = x.get('disk') or {}
        row = {'id': x.get('id'), 'name': x.get('name'),
               'status': x.get('status')}
        if disk.get('total') is not None:
            row['used'] = disk.get('used') or 0
            row['free'] = disk.get('free') or 0
            row['total'] = disk['total']
            row['pct'] = disk.get('current')
        out.append(row)
    return json.dumps(_slice(out, a.get('limit')))


def _list_nodes(integration, tool, args, data):
    items = data if isinstance(data, list) else []
    full = bool((args or {}).get('full'))
    out = []
    for x in items:
        if not isinstance(x, dict):
            continue
        row = {k: x.get(k) for k in ('id', 'type', 'name', 'host', 'status',
                                     'isCluster', 'clusterName')}
        row = {k: v for k, v in row.items() if v is not None}
        if full:
            endpoints = []
            for ep in x.get('clusterEndpoints') or []:
                if not isinstance(ep, dict):
                    continue
                endpoints.append({
                    'node': ep.get('NodeName'), 'ip': ep.get('IP'),
                    'online': ep.get('Online'),
                    'pulseReachable': ep.get('PulseReachable'),
                    'error': ep.get('PulseError'),
                })
            if endpoints:
                row['endpoints'] = endpoints
        out.append(row)
    return json.dumps(out)


# ── Jellyfin transforms ─────────────────────────────────────────────────
# The whole Jellyfin API is C#-serialized PascalCase; these map source keys to
# the compact camelCase projections and unwrap the non-obvious shapes
# (NowPlayingQueueFullItems[0], VirtualFolders-as-array, ...).

def _sysinfo(item, full):
    row = {
        'version': item.get('Version'), 'serverName': item.get('ServerName'),
        'os': item.get('OperatingSystem'), 'arch': item.get('SystemArchitecture'),
        'cachePath': item.get('CachePath'), 'logPath': item.get('LogPath'),
        'transcodePath': item.get('TranscodingTempPath'),
        'webPath': item.get('WebPath'), 'id': item.get('Id'),
    }
    row = {k: v for k, v in row.items() if v is not None}
    if full:
        for k in ('VersionName', 'OperatingSystemDisplayName',
                  'HasPendingRestart', 'SystemUpdateLevel'):
            if item.get(k) is not None:
                row[k[0].lower() + k[1:]] = item[k]
    return row


def _jellyfin_system_info(integration, tool, args, data):
    if not isinstance(data, dict):
        return json.dumps(data)
    return json.dumps(_sysinfo(data, bool((args or {}).get('full'))))


def _jellyfin_libraries(integration, tool, args, data):
    a = args or {}
    items = data if isinstance(data, list) else []
    needle = str(a.get('search') or '').lower()
    full = bool(a.get('full'))
    out = []
    for x in items:
        if not isinstance(x, dict):
            continue
        if needle and needle not in str(x.get('Name') or '').lower():
            continue
        row = {'name': x.get('Name'), 'type': x.get('CollectionType'),
               'locations': x.get('Locations') or [], 'itemId': x.get('ItemId')}
        if full:
            for k in ('Id', 'PrimaryImageItemId', 'ItemCount', 'ParentId',
                      'RefreshStatus'):
                if x.get(k) is not None:
                    row[k[0].lower() + k[1:]] = x[k]
        out.append(row)
    return json.dumps(_slice(out, a.get('limit')))


def _jellyfin_sessions(integration, tool, args, data):
    a = args or {}
    items = data if isinstance(data, list) else []
    needle = str(a.get('search') or '').lower()
    active_only = bool(a.get('activeOnly'))
    out = []
    for s in items:
        if not isinstance(s, dict):
            continue
        if active_only and not s.get('IsActive'):
            continue
        if needle and needle not in str(s.get('DeviceName') or '').lower() \
                and needle not in str(s.get('UserName') or '').lower():
            continue
        row = {
            'deviceName': s.get('DeviceName'), 'client': s.get('Client'),
            'version': s.get('ApplicationVersion'), 'userName': s.get('UserName'),
            'isActive': s.get('IsActive'), 'lastActivity': s.get('LastActivityDate'),
        }
        queue = s.get('NowPlayingQueueFullItems') or []
        if queue and isinstance(queue[0], dict):
            item = queue[0]
            row['nowPlaying'] = {'name': item.get('Name'), 'type': item.get('Type')}
        ps = s.get('PlayState') or {}
        if isinstance(ps, dict) and ps:
            row['playState'] = {'isPaused': ps.get('IsPaused'),
                                'repeatMode': ps.get('RepeatMode'),
                                'playbackOrder': ps.get('PlaybackOrder')}
        ti = s.get('TranscodingInfo')
        if isinstance(ti, dict) and ti:
            row['transcode'] = {'bitrate': ti.get('Bitrate'),
                                'transcodeReasons': ti.get('TranscodeReasons'),
                                'videoDirect': ti.get('IsVideoDirect'),
                                'audioDirect': ti.get('IsAudioDirect')}
        out.append({k: v for k, v in row.items() if v is not None})
    return json.dumps(_slice(out, a.get('limit')))


def _jellyfin_scheduled_tasks(integration, tool, args, data):
    a = args or {}
    items = data if isinstance(data, list) else []
    needle = str(a.get('search') or '').lower()
    category = a.get('category')
    out = []
    for x in items:
        if not isinstance(x, dict):
            continue
        if category and x.get('Category') != category:
            continue
        if needle and needle not in str(x.get('Name') or '').lower():
            continue
        last = x.get('LastExecutionResult') or {}
        row = {'id': x.get('Id'), 'name': x.get('Name'), 'state': x.get('State'),
               'category': x.get('Category'),
               'lastStatus': last.get('Status') if isinstance(last, dict) else None,
               'lastProgress': last.get('Progress') if isinstance(last, dict) else None,
               'lastEnd': last.get('EndTimeUtc') if isinstance(last, dict) else None}
        out.append({k: v for k, v in row.items() if v is not None})
    return json.dumps(_slice(out, a.get('limit')))


def _jellyfin_plugins(integration, tool, args, data):
    a = args or {}
    items = data if isinstance(data, list) else []
    needle = str(a.get('search') or '').lower()
    out = []
    for x in items:
        if not isinstance(x, dict):
            continue
        if needle and needle not in str(x.get('Name') or '').lower():
            continue
        out.append({'name': x.get('Name'), 'version': x.get('Version'),
                    'status': x.get('Status')})
    return json.dumps(_slice(out, a.get('limit')))


def _jellyfin_activity_log(integration, tool, args, data):
    a = args or {}
    if not isinstance(data, dict):
        return json.dumps(data)
    needle = str(a.get('search') or '').lower()
    entries = []
    for e in data.get('Items') or []:
        if not isinstance(e, dict):
            continue
        if needle and needle not in str(e.get('Name') or '').lower():
            continue
        entries.append({'name': e.get('Name'), 'type': e.get('Type'),
                        'date': e.get('Date'), 'severity': e.get('Severity')})
    return json.dumps({'total': data.get('TotalRecordCount'), 'entries': entries})


def _jellyfin_logs(integration, tool, args, data):
    a = args or {}
    items = data if isinstance(data, list) else []
    needle = str(a.get('search') or '').lower()
    out = []
    for x in items:
        if not isinstance(x, dict):
            continue
        if needle and needle not in str(x.get('Name') or '').lower():
            continue
        out.append({'name': x.get('Name'), 'size': x.get('Size')})
    return json.dumps(_slice(out, a.get('limit')))


def _jellyfin_users(integration, tool, args, data):
    a = args or {}
    items = data if isinstance(data, list) else []
    needle = str(a.get('search') or '').lower()
    out = []
    for x in items:
        if not isinstance(x, dict):
            continue
        if needle and needle not in str(x.get('Name') or '').lower():
            continue
        out.append({'id': x.get('Id'), 'name': x.get('Name'),
                    'isAdmin': x.get('IsAdministrator')})
    return json.dumps(_slice(out, a.get('limit')))


_JELLYFIN_LOG_CAP = 100 * 1024


def _jellyfin_get_log(integration, tool, args, raw_body):
    """Raw-text transform: return the tail of a log file, capped in size."""
    a = args or {}
    try:
        tail = int(a.get('tailLines') or 200)
    except (TypeError, ValueError):
        tail = 200
    if tail <= 0:
        tail = 0
    lines = raw_body.splitlines()
    if tail:
        lines = lines[-tail:]
    content = '\n'.join(lines)
    if len(content) > _JELLYFIN_LOG_CAP:
        content = content[-_JELLYFIN_LOG_CAP:]
    return json.dumps({'name': a.get('name'), 'lines': len(lines),
                       'content': content})


TRANSFORMS = {
    'pulse_health': _health,
    'pulse_fleet_summary': _fleet_summary,
    'pulse_get_resource': _get_resource,
    'pulse_list_alerts': _list_alerts,
    'pulse_get_charts': _charts,
    'pulse_list_backups': _backups,
    'pulse_list_storage': _list_storage,
    'pulse_list_nodes': _list_nodes,
    'jellyfin_system_info': _jellyfin_system_info,
    'jellyfin_libraries': _jellyfin_libraries,
    'jellyfin_sessions': _jellyfin_sessions,
    'jellyfin_scheduled_tasks': _jellyfin_scheduled_tasks,
    'jellyfin_plugins': _jellyfin_plugins,
    'jellyfin_activity_log': _jellyfin_activity_log,
    'jellyfin_logs': _jellyfin_logs,
    'jellyfin_users': _jellyfin_users,
}

# Transforms that consume the raw body as text (non-JSON endpoints).
TRANSFORMS_RAW = {
    'jellyfin_get_log': _jellyfin_get_log,
}


def apply_transform(name: str, integration, tool: dict, args: dict, body: str):
    """Run a registered transform against a raw upstream body.

    Returns the transformed JSON string, or None when `name` is not a known
    transform (the caller falls through to the standard shaping path)."""
    raw_fn = TRANSFORMS_RAW.get(name)
    if raw_fn:
        try:
            return raw_fn(integration, tool, args or {}, body)
        except Exception as e:
            return _err('transform_failed', f"{type(e).__name__}: {e}")
    fn = TRANSFORMS.get(name)
    if not fn:
        return None
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        # Never ship an unparseable blob (e.g. a truncated upstream body) —
        # report it instead so the caller doesn't dump raw bytes to the model.
        return _err('transform_parse_failed',
                    'Upstream response could not be parsed as JSON')
    try:
        return fn(integration, tool, args or {}, data)
    except Exception as e:
        return _err('transform_failed', f"{type(e).__name__}: {e}")

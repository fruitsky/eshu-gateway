"""S1: the read-only Home Assistant device inventory for the permissions UI.

The picker needs friendly names, real areas, current state, labels and the
two safety flags (risky device class, protected by the operator). The three
registries and the state machine are read over HA's WebSocket API, which is
the only place the entity/device/area/label registries exist. Nothing here is
exposed to an agent: the two HTTP routes in ``main.py`` require an operator
session.

The result is cached for 60 seconds. The key includes the database path so a
test or a second dashboard instance can never inherit another database's
inventory.
"""
import copy
import threading
import time

from core import allowances as _allowances

CACHE_SECONDS = 60
_RISKY_CLASSES = {'garage', 'garage_door', 'gate', 'door', 'lock'}
_CACHE = {}
_LOCK = threading.Lock()

# X11 Stage 4: the useful-device filter. These constants are deliberately in
# one place: the S1 response labels every entity, so the agent page, the
# operator picker and the summary sentence cannot disagree about what counts.
CATEGORY_BY_DOMAIN = {
    'switch': 'switch',
    'input_boolean': 'switch',
    'light': 'light',
    'cover': 'cover',
    'climate': 'climate',
    'fan': 'fan',
    'media_player': 'media',
    'sensor': 'sensor',
    'binary_sensor': 'sensor',
}

# Add-on and system integrations whose entities are plumbing, not devices the
# household agent should be offered. Kept as a constant (with this comment)
# because HA adds new platform names over time; update the list when a new
# system platform shows up in the registry.
SYSTEM_PLATFORMS = {
    'analytics', 'browser_mod', 'cloud', 'default_config', 'hacs', 'hassio',
    'lovelace', 'supervisor', 'system_health', 'update',
}

# Helper entities are the operator's own scaffolding (input_boolean, counters,
# timers) rather than a physical device.
HELPER_DOMAINS = {
    'counter', 'input_boolean', 'input_datetime', 'input_number',
    'input_select', 'input_text', 'schedule', 'timer',
}

# Fallback only: entity_category is the first test. Some integrations do not
# set a category on a device's sub-setting switches, so these suffixes catch
# the common ones (child lock, LED, auto-off, auto-update).
SWITCH_SETTING_SUFFIXES = (
    'child_lock', 'led', 'auto_off', 'auto_update', 'indicator',
    'night_light', 'backlight', 'power_on_behavior', 'startup_mode',
    'switch_mode',
)

SENSOR_KIND_BY_CLASS = {
    'temperature': 'temperature',
    'humidity': 'humidity',
    'absolute_humidity': 'humidity',
    'moisture': 'humidity',
    'motion': 'motion',
    'occupancy': 'motion',
    'presence': 'motion',
    'moving': 'motion',
    'door': 'door',
    'window': 'door',
    'opening': 'door',
    'garage_door': 'door',
    'power': 'power',
    'energy': 'power',
    'current': 'power',
    'voltage': 'power',
    'battery': 'battery',
}


def _sensor_kind(reg, attrs):
    """Temperature, humidity, motion, door, power, battery or other."""
    device_class = str((attrs or {}).get('device_class') or '').lower()
    if device_class in SENSOR_KIND_BY_CLASS:
        return SENSOR_KIND_BY_CLASS[device_class]
    unit = str((attrs or {}).get('unit_of_measurement') or '').lower()
    if unit in ('°c', '°f', 'c', 'f', 'kelvin'):
        return 'temperature'
    if unit in ('w', 'kw', 'wh', 'kwh', 'va', 'kva'):
        return 'power'
    return 'other'


def _hidden_reason(entity_id, reg, platform):
    """Why an entity is not useful, in the order the operator would expect.

    `entity_category` wins over the suffix fallback, because a badly named
    diagnostic switch is still a diagnostic switch.
    """
    entity_id = str(entity_id or '').lower()
    reg = reg or {}
    if str(reg.get('entity_category') or '').lower() in ('config', 'diagnostic'):
        return 'settings'
    if reg.get('disabled_by') or reg.get('hidden_by'):
        return 'disabled'
    if str(platform or '').lower() in SYSTEM_PLATFORMS:
        return 'system'
    domain = entity_id.split('.', 1)[0]
    if domain in HELPER_DOMAINS:
        return 'helper'
    if domain in ('switch', 'input_boolean'):
        name = (str(reg.get('name') or '') + ' ' +
                str(reg.get('original_name') or '') + ' ' + entity_id).lower()
        for suffix in SWITCH_SETTING_SUFFIXES:
            if suffix in name:
                return 'sub_setting'
    return ''


def classify_entity(entity_id, reg=None, attrs=None, platform=''):
    """X11 Stage 4: category, sensor kind and the useful-device flag.

    Pure and exportable so the synthetic registry tests can exercise every
    hidden reason without a Home Assistant instance.
    """
    entity_id = str(entity_id or '').lower()
    reg = reg or {}
    attrs = attrs or {}
    domain = entity_id.split('.', 1)[0]
    category = CATEGORY_BY_DOMAIN.get(domain, '')
    sensor_kind = _sensor_kind(reg, attrs) if category == 'sensor' else ''
    reason = _hidden_reason(entity_id, reg, platform)
    useful = bool(category) and not reason
    return {
        'category': category,
        'sensor_kind': sensor_kind,
        'useful': useful,
    }


def _cache_key(integration):
    from db.core import DB_PATH
    return (DB_PATH, int(integration.get('id') or 0),
            str(integration.get('base_url') or ''))


def clear_cache():
    with _LOCK:
        _CACHE.clear()


def _entity_name(reg, state):
    attrs = (state or {}).get('attributes') or {}
    return (str(attrs.get('friendly_name') or '').strip()
            or str((reg or {}).get('name') or '').strip()
            or str((reg or {}).get('original_name') or '').strip()
            or str((reg or {}).get('entity_id') or ''))


def _build(integration):
    from core.ha_ws import ha_ws_request
    from core.secret_scrub import scrub_payload

    entity_rows = ha_ws_request(
        integration, 'config/entity_registry/list', {}) or []
    device_rows = ha_ws_request(
        integration, 'config/device_registry/list', {}) or []
    area_rows = ha_ws_request(
        integration, 'config/area_registry/list', {}) or []
    label_rows = ha_ws_request(
        integration, 'config/label_registry/list', {}) or []
    state_rows = ha_ws_request(integration, 'get_states', {}) or []

    areas = {str(r.get('area_id') or ''): str(r.get('name') or '')
             for r in area_rows if isinstance(r, dict)}
    labels = {str(r.get('label_id') or ''): str(r.get('name') or '')
              for r in label_rows if isinstance(r, dict)}
    devices = {str(r.get('id') or ''): r for r in device_rows
               if isinstance(r, dict)}
    states = {str(r.get('entity_id') or ''): r for r in state_rows
              if isinstance(r, dict)}
    protected = _allowances.protected_entities()

    out = []
    for reg in entity_rows:
        if not isinstance(reg, dict) or not reg.get('entity_id'):
            continue
        ent = str(reg.get('entity_id')).lower()
        domain = ent.split('.', 1)[0]
        state = states.get(ent) or {}
        attrs = state.get('attributes') or {}
        device = devices.get(str(reg.get('device_id') or '')) or {}
        area_id = str(reg.get('area_id') or device.get('area_id') or '')
        label_ids = []
        for lid in (reg.get('labels') or []):
            lid = str(lid or '')
            if lid and lid not in label_ids:
                label_ids.append(lid)
        platform = str(reg.get('platform') or '')
        classified = classify_entity(ent, reg, attrs, platform)
        out.append({
            'id': ent,
            'name': _entity_name(reg, state),
            'area': areas.get(area_id, ''),
            'area_id': area_id,
            'domain': domain,
            'state': str(state.get('state') or 'unknown'),
            'unit': str(attrs.get('unit_of_measurement') or ''),
            'device_class': str(attrs.get('device_class') or '').lower(),
            'labels': [{'id': lid, 'name': labels.get(lid, lid)}
                       for lid in label_ids],
            'category': classified['category'],
            'sensor_kind': classified['sensor_kind'],
            'useful': classified['useful'],
            'settings_entity': bool(reg.get('entity_category')),
            'disabled': bool(reg.get('disabled_by')),
            'risky': str(attrs.get('device_class') or '').lower()
                     in _RISKY_CLASSES,
            'protected': ent in protected,
        })
    out.sort(key=lambda d: (d['area'].lower(), d['name'].lower(), d['id']))
    labels_out = [{'id': lid, 'name': name}
                  for lid, name in sorted(labels.items(), key=lambda x: x[1].lower())]
    payload = scrub_payload({'devices': out, 'labels': labels_out})
    return payload['devices'], payload['labels']


def inventory():
    """Return (devices, labels), cached for 60 seconds."""
    integration = _allowances._ha_integration()
    if not integration:
        raise _allowances.AllowanceError(
            503, 'The device list needs exactly one enabled Home Assistant '
                 'integration.')
    key = _cache_key(integration)
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > now:
            return copy.deepcopy(hit[1][0]), copy.deepcopy(hit[1][1])
    try:
        devices, labels = _build(integration)
    except _allowances.AllowanceError:
        raise
    except Exception as exc:  # noqa: BLE001 - translate to a UI-safe refusal
        raise _allowances.AllowanceError(
            503, 'Could not read the Home Assistant devices: %s' % exc)
    with _LOCK:
        _CACHE[key] = (now + CACHE_SECONDS, (devices, labels))
    return copy.deepcopy(devices), copy.deepcopy(labels)


def devices():
    return inventory()[0]


def labels():
    return inventory()[1]


def names_for(entity_ids):
    """Friendly names for a preview; never fails a save over a name lookup."""
    wanted = [str(e or '').lower() for e in (entity_ids or [])]
    if not wanted:
        return {}
    try:
        rows = {d['id']: d['name'] for d in devices()}
    except Exception:
        rows = {}
    return {e: rows.get(e, e) for e in wanted}


def cached_names_for(entity_ids):
    """Names from the current S1 cache only; never opens a WebSocket.

    A preview must not turn a name lookup into a new HA connection. The UI
    loads S1 first, so its cache is normally warm; without it the caller
    falls back to the entity id, which is still unambiguous.
    """
    wanted = [str(e or '').lower() for e in (entity_ids or [])]
    if not wanted:
        return {}
    from db.core import DB_PATH
    rows = {}
    with _LOCK:
        for key, (_expires, (devices, _labels)) in _CACHE.items():
            if key[0] != DB_PATH:
                continue
            for row in devices:
                rows.setdefault(row['id'], row['name'])
    return {e: rows[e] for e in wanted if e in rows}


def cached_label_names():
    from db.core import DB_PATH
    out = {}
    with _LOCK:
        for key, (_expires, (_devices, labels)) in _CACHE.items():
            if key[0] != DB_PATH:
                continue
            for row in labels:
                out.setdefault(row['id'], row['name'])
    return out

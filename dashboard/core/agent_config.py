"""S2/S5: one agent's HA permissions and whole JSON configuration.

The GUI and the JSON editor call the same functions here, so the JSON cannot
do anything the GUI refuses. Every apply validates first, then writes in one
``BEGIN IMMEDIATE``; a stale config version is a 409 before any write.

The config version is one monotonic counter per agent
(``agent_config_state``). It is bumped by this module, by the legacy policy
save and by the website-policy endpoints, so an editor opened before any of
those writes cannot later overwrite them.
"""
import hashlib
import json
import time

from core import allowances as _allowances
from core import ha_devices
from core.web_fetch_policy import validate_policy as validate_web_policy
from db.agent_policies import (
    get_agent_config_version, bump_agent_config_version, get_agent_policy,
    validate_policy, _validate_test_agent_policy, write_policy_row,
)
from db.agent_tokens import get_agent
from db.web_fetch import get_web_fetch_policy

SCHEMA_VERSION = 1
DEFAULT_RATE_PER_HOUR = 30

_PERMISSION_KEYS = {'name', 'domain', 'service', 'entities', 'label_id',
                    'rate_per_hour', 'window', 'expires_at',
                    'suspended_at', 'suspended_reason'}
_TOOL_KEYS = {'mode', 'allow', 'approval', 'argument_rules',
              'rate_limit_per_min'}
_SSH_KEYS = {'mode', 'hosts'}
_WEBSITE_KEYS = {'enabled', 'allow', 'deny'}
_HA_KEYS = {'permissions'}
_TOP_KEYS = {'schema_version', 'agent', 'tools', 'ssh', 'websites',
             'home_assistant'}


def _error(status, message, code, **extra):
    raise _allowances.AllowanceError(status, message, {'code': code, **extra})


def _check_keys(value, allowed, where):
    if not isinstance(value, dict):
        _error(400, '%s must be an object.' % where, 'invalid_config')
    extra = sorted(set(value) - set(allowed))
    if extra:
        _error(400, '%s has an unknown field: %s' % (where, ', '.join(extra)),
               'unknown_field', fields=extra, field=where)


def _version_row(conn, agent_id):
    row = conn.execute(
        'SELECT version FROM agent_config_state WHERE agent_id = ?',
        (int(agent_id),)).fetchone()
    return int(row['version']) if row else 1


def _check_version(agent_id, version):
    if version is None:
        _error(400, 'version is required: reload the config before saving.',
               'version_required')
    try:
        wanted = int(version)
    except (TypeError, ValueError):
        _error(400, 'version must be an integer.', 'invalid_version')
    current = get_agent_config_version(agent_id)
    if wanted != current:
        _error(409, 'The agent config changed since it was loaded; reload it.',
               'config_changed', version=current)
    return current


def _agent_or_404(agent_id):
    agent = get_agent(int(agent_id))
    if not agent:
        _error(404, 'Agent token not found', 'not_found')
    return agent


# ── HA permissions ────────────────────────────────────────────────────────

def _row_from_db(row):
    cond = json.loads(row.get('conditions_json') or '{}')
    pins = json.loads(row.get('entities_json') or '[]')
    entities = [p.get('entity_id') for p in pins if p.get('entity_id')]
    return {
        'id': int(row['id']),
        'version': int(row.get('version') or 1),
        'name': row.get('name') or '',
        'domain': (cond.get('domain', {}).get('in') or [''])[0],
        'service': (cond.get('service', {}).get('in') or [''])[0],
        'entities': entities,
        'label_id': row.get('source_label_id') or '',
        'rate_per_hour': int(row.get('rate_per_hour') or 0),
        'window': row.get('window') or '',
        'expires_at': int(row.get('expires_at') or 0),
        'suspended_at': int(row.get('suspended_at') or 0),
        'suspended_reason': row.get('suspended_reason') or '',
        'pending': json.loads(row.get('pending_json') or '[]'),
        'revoked_at': int(row.get('revoked_at') or 0),
    }


def _current_ha_rows(agent_id, conn=None):
    import db.core as _core
    _allowances._init_allowance_tables()
    sql = ("SELECT * FROM allowances WHERE agent_id = ? AND integration = 'ha' "
           "AND revoked_at = 0 ORDER BY id")
    if conn is None:
        with _core.db_conn() as own:
            rows = own.execute(sql, (int(agent_id),)).fetchall()
    else:
        rows = conn.execute(sql, (int(agent_id),)).fetchall()
    return [_row_from_db(dict(r)) for r in rows]


def _public_permission(item):
    return {k: item[k] for k in
            ('id', 'version', 'name', 'domain', 'service', 'entities',
             'label_id', 'rate_per_hour', 'window', 'expires_at',
             'suspended_at', 'suspended_reason')}


def get_ha_permissions(agent_id):
    _agent_or_404(agent_id)
    return {
        'agent_id': int(agent_id),
        'version': get_agent_config_version(agent_id),
        'permissions': [_public_permission(r)
                        for r in _current_ha_rows(agent_id)],
    }


def _permission_signature(item):
    label = (item.get('label_id') or '').strip()
    if label:
        return ('label', item['domain'], item['service'], label,
                int(item['rate_per_hour']), item['window'] or '',
                int(item.get('expires_at') or 0))
    return ('devices', item['domain'], item['service'],
            tuple(sorted(item.get('entities') or [])),
            int(item['rate_per_hour']), item['window'] or '',
            int(item.get('expires_at') or 0))


def _permission_from_row(row):
    return _permission_signature(row)


def _normalise_permission(raw, index, registry):
    where = 'permissions[%d]' % index
    if not isinstance(raw, dict):
        _error(400, '%s must be an object.' % where, 'invalid_permission')
    _check_keys(raw, _PERMISSION_KEYS, where)
    domain = str(raw.get('domain') or '').strip().lower()
    service = str(raw.get('service') or '').strip().lower()
    if not domain or not service:
        _error(400, '%s needs a domain and a service.' % where,
               'invalid_permission')
    label = str(raw.get('label_id') or '').strip()
    provided = {k for k in ('rate_per_hour', 'window', 'expires_at')
                if k in raw and raw.get(k) is not None}
    rate = raw.get('rate_per_hour', DEFAULT_RATE_PER_HOUR)
    try:
        rate = int(rate)
    except (TypeError, ValueError):
        _error(400, '%s.rate_per_hour must be an integer.' % where,
               'invalid_permission')
    window = str(raw.get('window') or '').strip()
    expires_at = raw.get('expires_at', 0)
    try:
        expires_at = int(expires_at or 0)
    except (TypeError, ValueError):
        _error(400, '%s.expires_at must be an integer.' % where,
               'invalid_permission')
    if label:
        # The label is the source of truth; entities may be present in a
        # round-tripped document, but they are not trusted.
        pins = _allowances._resolve_label_pins(label, domain, registry)
        entities = [p['entity_id'] for p in pins]
        if not entities:
            _error(400, '%s: label %r has no %s entities.'
                   % (where, label, domain), 'empty_label')
    else:
        entities = raw.get('entities')
        if not isinstance(entities, list):
            _error(400, '%s.entities must be a list.' % where,
                   'invalid_permission')
    entities, pins, risk = _allowances._validate_allowance(
        domain, service, entities, rate_per_hour=rate, window=window,
        expires_at=expires_at, risk_ack=True, registry=registry,
        return_risk=True, source_label=label)
    item = {
        'name': str(raw.get('name') or '')[:80],
        'domain': domain,
        'service': service,
        'entities': entities,
        'label_id': label,
        'rate_per_hour': rate,
        'window': window,
        'expires_at': expires_at,
        '_provided': provided,
        'pins': pins,
        'risk': risk,
    }
    if not item['name']:
        row = _allowances.catalogue_row(_allowances.INTEGRATION,
                                        '%s.%s' % (domain, service))
        item['name'] = (row or {}).get('action_label') or service
    item['signature'] = _permission_signature(item)
    return item


def prepare_permissions(raw_permissions, registry=None):
    if not isinstance(raw_permissions, list):
        _error(400, 'permissions must be a list.', 'invalid_permission')
    if len(raw_permissions) > 64:
        _error(400, 'At most 64 permissions are allowed.', 'invalid_permission')
    if registry is None:
        registry = _allowances._ha_registry()
    prepared = [_normalise_permission(raw, i, registry)
                for i, raw in enumerate(raw_permissions)]
    # Two rows that match the same call make the matcher ambiguous. Refuse
    # duplicates and overlaps here, before anything is written.
    seen = set()
    by_action = {}
    for item in prepared:
        signature = item['signature']
        if signature in seen:
            _error(400, 'Two permissions describe the same thing.',
                   'duplicate_permission')
        seen.add(signature)
        key = (item['domain'], item['service'])
        entities = set(item['entities'])
        for other in by_action.setdefault(key, []):
            if entities & other:
                _error(400, 'Permissions for %s.%s overlap; that would make '
                       'a call ambiguous.' % key, 'overlapping_permission')
        by_action[key].append(entities)
    return prepared


def _group_key(item):
    return (item['domain'], item['service'], item.get('label_id') or '')


def _diff_permissions(current, prepared):
    used = set()
    keep = []
    changed = []
    added = []
    for item in prepared:
        candidates = [r for r in current
                      if r['id'] not in used and _group_key(r) == _group_key(item)]
        exact = next((r for r in candidates
                      if _permission_from_row(r) == item['signature']), None)
        if exact is not None:
            used.add(exact['id'])
            keep.append((exact, item))
            continue
        if candidates:
            old = candidates[0]
            used.add(old['id'])
            # X4 for list items: a field absent from a changed permission
            # keeps the stored value. The 30/h and any-time defaults are for
            # a new permission only.
            provided = item.get('_provided') or set()
            if 'rate_per_hour' not in provided:
                item['rate_per_hour'] = old['rate_per_hour']
            if 'window' not in provided:
                item['window'] = old['window']
            if 'expires_at' not in provided:
                item['expires_at'] = old.get('expires_at') or 0
            item['signature'] = _permission_signature(item)
            if item['signature'] == _permission_from_row(old):
                keep.append((old, item))
            else:
                changed.append((old, item))
            continue
        added.append(item)
    changed_ids = {old['id'] for old, _new in changed}
    removed = [r for r in current
               if r['id'] not in used and r['id'] not in changed_ids]
    return {'keep': keep, 'changed': changed, 'added': added,
            'removed': removed}


def _action(item):
    row = _allowances.catalogue_row(_allowances.INTEGRATION,
                                    '%s.%s' % (item['domain'], item['service']))
    return (row or {}).get('action_label') or item.get('service') or ''


def _name_map(items):
    entities = []
    for item in items:
        entities.extend(item.get('entities') or [])
    wanted = [str(e or '').lower() for e in entities]
    try:
        registry = _allowances._ha_registry()
    except Exception:
        registry = {}
    names = {}
    for ent in wanted:
        reg = registry.get(ent) or {}
        names[ent] = (reg.get('name') or reg.get('original_name') or ent)
    names.update(ha_devices.cached_names_for(wanted))
    return names


def _label_name(label_id):
    return ha_devices.cached_label_names().get(label_id, label_id)


def _display_permission(item, names=None):
    names = names or {}
    label = item.get('label_id') or ''
    return {
        'name': item.get('name') or '',
        'domain': item['domain'],
        'service': item['service'],
        'action': _action(item),
        'label_id': label,
        'label_name': _label_name(label) if label else '',
        'entities': list(item.get('entities') or []),
        'entity_names': [names.get(e, e) for e in (item.get('entities') or [])],
        'count': len(item.get('entities') or []),
        'rate_per_hour': int(item.get('rate_per_hour') or 0),
        'window': item.get('window') or '',
        'expires_at': int(item.get('expires_at') or 0),
    }


def _risk_items(prepared):
    out = []
    for item in prepared:
        risk = item.get('risk') or {}
        if risk.get('risky_row'):
            out.append({
                'kind': 'category', 'domain': item['domain'],
                'service': item['service'],
                'reason': 'this action is marked risky for every device',
            })
        for row in risk.get('risky_entities') or []:
            out.append({
                'kind': 'device', 'entity_id': row.get('entity_id'),
                'device_class': row.get('device_class'),
                'reason': 'a %s needs the second confirmation'
                          % (row.get('device_class') or 'risky device'),
            })
    return out


def _resumes(diff):
    out = []
    for old, new in diff['changed']:
        if not old.get('suspended_at'):
            continue
        reason = old.get('suspended_reason') or 'suspended'
        out.append({
            'id': old['id'],
            'name': new.get('name') or old.get('name') or '',
            'domain': new['domain'],
            'service': new['service'],
            'reason': reason,
            'suspended_at': int(old.get('suspended_at') or 0),
            'will_pin_to_current_ha': reason == 'ha_changed',
        })
    return out


def _waiting(diff):
    """Label members already held in pending_json, plus members new since."""
    out = []
    for old, new in diff['changed']:
        if not (old.get('label_id') or new.get('label_id')):
            continue
        stored = {str(e) for e in old.get('entities') or []
                  if isinstance(e, str)}
        current = {p.get('entity_id'): p for p in new.get('pins') or []}
        waiting = {}
        for p in old.get('pending') or []:
            e = p.get('entity_id')
            if e in current:
                waiting[e] = {'entity_id': e,
                              'registry_id': current[e].get('registry_id') or ''}
        for e, p in current.items():
            if e not in stored and e not in waiting:
                waiting[e] = {'entity_id': e,
                              'registry_id': p.get('registry_id') or ''}
        if waiting:
            out.append({
                'permission_id': old['id'],
                'name': new.get('name') or old.get('name') or '',
                'entities': [waiting[e] for e in sorted(waiting)],
            })
    return out


def _waiting_view(waiting, names):
    return [{
        'permission_id': row['permission_id'],
        'name': row['name'],
        'entities': [{'entity_id': p['entity_id'],
                      'name': names.get(p['entity_id'], p['entity_id'])}
                     for p in row['entities']],
    } for row in waiting]


def _section_fingerprint(state):
    """The tools, SSH and websites sections exactly as this write will store
    them, so an S5 preview also binds what the document says outside HA."""
    payload = {
        'tools': _policy_doc(state['policy']),
        'ssh': {'mode': state['policy'].get('ssh_mode') or 'approval',
                'hosts': list(state['policy'].get('ssh_hosts_allow') or [])},
        'websites': dict(state['web']),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()


def _preview_hash(version, prepared, risks, resumes, waiting, sections=None):
    permissions = []
    for item in prepared or []:
        pins = sorted((p.get('entity_id') or '', p.get('registry_id') or '')
                      for p in item.get('pins') or [])
        permissions.append({
            'domain': item['domain'],
            'service': item['service'],
            'label_id': item.get('label_id') or '',
            'rate_per_hour': int(item.get('rate_per_hour') or 0),
            'window': item.get('window') or '',
            'expires_at': int(item.get('expires_at') or 0),
            'devices': [list(p) for p in pins],
        })
    payload = {
        'version': int(version),
        'permissions': permissions,
        'risky': sorted(risks or [],
                        key=lambda d: json.dumps(d, sort_keys=True)),
        'resumes': sorted(resumes or [],
                          key=lambda d: json.dumps(d, sort_keys=True)),
        'waiting': sorted(waiting or [],
                          key=lambda d: json.dumps(d, sort_keys=True)),
    }
    if sections is not None:
        payload['sections'] = sections
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _waiting_text(waiting, names):
    if not waiting:
        return ''
    parts = []
    for row in waiting:
        for p in row['entities']:
            parts.append(names.get(p['entity_id'], p['entity_id']))
    return ' Will also add, waiting to join: %s.' % ', '.join(parts)


def _permission_summary(agent_name, diff, waiting=None):
    added = diff['added']
    changed = diff['changed']
    removed = diff['removed']
    if not added and not changed and not removed:
        return '%s is unchanged.' % agent_name
    names = _name_map(added + [new for _old, new in changed] + removed)
    groups = {}
    for item in added:
        groups.setdefault(_action(item), []).extend(item.get('entities') or [])
    for _old, item in changed:
        groups.setdefault(_action(item), []).extend(item.get('entities') or [])
    parts = []
    for action in sorted(groups):
        n = len(groups[action])
        parts.append('%s %d device%s' % (action.lower(), n,
                                         '' if n == 1 else 's'))
    if not parts:
        text = '%s will no longer be able to use the removed permissions.' % agent_name
    else:
        text = '%s will be able to %s.' % (agent_name, ' and '.join(parts))
    if removed:
        labels = ['%s (%s)' % (r.get('name') or _action(r), _action(r).lower())
                  for r in removed]
        text += ' Removed: %s.' % ', '.join(labels)
    if changed:
        labels = ['%s (%s)' % (new.get('name') or _action(new),
                               _action(new).lower()) for _old, new in changed]
        text += ' Changed: %s.' % ', '.join(labels)
    if diff['added']:
        labels = ['%s (%s)' % (p.get('name') or _action(p), _action(p).lower())
                  for p in diff['added']]
        if any(p.get('label_id') for p in diff['added']):
            labels = ['%s: follow label %s' % (p.get('name') or _action(p),
                                               _label_name(p['label_id']))
                      if p.get('label_id') else x
                      for p, x in zip(diff['added'], labels)]
        text += ' Added: %s.' % ', '.join(labels)
    if keep := diff['keep']:
        text += ' Unchanged: %d.' % len(keep)
    text += _waiting_text(waiting or [], names)
    return text


def _permission_view(diff, names):
    return {
        'add': [_display_permission(p, names) for p in diff['added']],
        'change': [{'before': _display_permission(old, names),
                    'after': _display_permission(new, names)}
                   for old, new in diff['changed']],
        'remove': [_display_permission(r, names) for r in diff['removed']],
        'keep': [{'id': old['id'], 'name': new.get('name') or old.get('name')}
                 for old, new in diff['keep']],
    }


def _write_ha_diff(conn, agent_id, diff, actor, now, prepared=None):
    _allowances._recheck_conn(conn, prepared or [])
    ha_id, ha_fp = _allowances._ha_pin()
    revoked = []
    created = []
    for old, new in diff['changed']:
        conn.execute('UPDATE allowances SET revoked_at = ? WHERE id = ? '
                     'AND revoked_at = 0', (now, old['id']))
        revoked.append(old['id'])
        created.append(_allowances._insert_allowance(
            conn, agent_id, new.get('name'), new['domain'], new['service'],
            new['entities'], new['pins'],
            rate_per_hour=new['rate_per_hour'], window=new['window'],
            expires_at=int(new.get('expires_at') or 0),
            version=int(old.get('version') or 1) + 1, actor=actor, now=now,
            source_label_id=new.get('label_id') or '',
            ha_integration_id=ha_id, ha_base_url_fp=ha_fp))
    for old in diff['removed']:
        conn.execute('UPDATE allowances SET revoked_at = ? WHERE id = ? '
                     'AND revoked_at = 0', (now, old['id']))
        revoked.append(old['id'])
    for new in diff['added']:
        created.append(_allowances._insert_allowance(
            conn, agent_id, new.get('name'), new['domain'], new['service'],
            new['entities'], new['pins'],
            rate_per_hour=new['rate_per_hour'], window=new['window'],
            expires_at=new.get('expires_at') or 0,
            version=1, actor=actor, now=now,
            source_label_id=new.get('label_id') or '',
            ha_integration_id=ha_id, ha_base_url_fp=ha_fp))
    return revoked, created


def _audit_ha(actor, agent_id, revoked, created, resumes=None):
    from db.audit import record_audit_event
    if not revoked and not created and not resumes:
        return
    resumed = ', '.join(
        '#%s:%s' % (r.get('id'), r.get('reason') or 'suspended')
        for r in resumes or []) or 'none'
    record_audit_event(
        'allowance_ha_permissions_applied',
        details='agent #%d HA permissions by %s: revoked %s, created %s, '
                'resumed %s'
                % (int(agent_id), actor,
                   ','.join(map(str, revoked)) or 'none',
                   ','.join(map(str, created)) or 'none', resumed))


def apply_ha_permissions(actor, agent_id, permissions, preview=False,
                         risk_ack=False, version=None, preview_hash=None,
                         resume_ack=False):
    agent = _agent_or_404(agent_id)
    current_version = _check_version(agent_id, version)
    registry = _allowances._ha_registry()
    prepared = prepare_permissions(permissions, registry=registry)
    current = _current_ha_rows(agent_id)
    diff = _diff_permissions(current, prepared)
    names = _name_map(prepared + current)
    risks = _risk_items(prepared)
    resumes = _resumes(diff)
    waiting = _waiting(diff)
    changed = bool(diff['added'] or diff['changed'] or diff['removed'])
    summary = _permission_summary(agent.get('name') or 'agent', diff, waiting)
    computed = _preview_hash(current_version, prepared, risks, resumes, waiting)
    if preview:
        return {
            'preview': True, 'agent': {'id': int(agent_id),
                                       'name': agent.get('name') or ''},
            'version': current_version,
            'changed': changed, 'summary': summary,
            'permissions': _permission_view(diff, names),
            'risky': risks, 'risk_ack_required': bool(risks),
            'resumes': resumes, 'resume_ack_required': bool(resumes),
            'waiting': _waiting_view(waiting, names),
            'preview_hash': computed,
        }
    if not preview_hash:
        _error(400, 'preview_hash is required: reload the preview.',
               'preview_hash_required')
    if preview_hash != computed:
        _error(409, 'The preview changed; reload it.', 'preview_changed')
    if risks and not risk_ack:
        _error(400, 'This change needs an explicit risk confirmation.',
               'risk_ack_required', risky=risks)
    if resumes and not resume_ack:
        _error(400, 'This change resumes a suspended permission; confirm it.',
               'resume_ack_required', resumes=resumes)
    if not changed:
        return {'status': 'ok', 'version': current_version,
                'changed': False, 'summary': summary,
                'preview_hash': computed,
                'permissions': [_public_permission(r)
                                for r in _current_ha_rows(agent_id)]}
    import db.core as _core
    now = int(time.time())
    with _core.db_conn() as conn:
        try:
            conn.execute('BEGIN IMMEDIATE')
            tx_version = _version_row(conn, agent_id)
            if int(version) != tx_version:
                conn.rollback()
                _error(409, 'The agent config changed since it was loaded; '
                       'reload it.', 'config_changed', version=tx_version)
            tx_current = _current_ha_rows(agent_id, conn=conn)
            tx_diff = _diff_permissions(tx_current, prepared)
            tx_resumes = _resumes(tx_diff)
            tx_waiting = _waiting(tx_diff)
            tx_hash = _preview_hash(tx_version, prepared, risks,
                                    tx_resumes, tx_waiting)
            if preview_hash != tx_hash:
                conn.rollback()
                _error(409, 'The preview changed; reload it.',
                       'preview_changed')
            if tx_resumes and not resume_ack:
                conn.rollback()
                _error(400, 'This change resumes a suspended permission; '
                       'confirm it.', 'resume_ack_required',
                       resumes=tx_resumes)
            revoked, created = _write_ha_diff(
                conn, agent_id, tx_diff, actor, now, prepared=prepared)
            new_version = bump_agent_config_version(agent_id, conn)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
    _audit_ha(actor, agent_id, revoked, created, resumes=resumes)
    return {'status': 'ok', 'version': new_version, 'changed': True,
            'summary': summary, 'preview_hash': computed,
            'permissions': [_public_permission(r)
                            for r in _current_ha_rows(agent_id)]}


# ── S5: the whole per-agent document ──────────────────────────────────────

def _policy_doc(policy):
    return {
        'mode': policy.get('tools_mode') or 'all',
        'allow': list(policy.get('tools') or []),
        'approval': dict(policy.get('approval') or {}),
        'argument_rules': dict(policy.get('arg_policy') or {}),
        'rate_limit_per_min': int(policy.get('rate_limit_per_min') or 0),
    }


def _web_doc(policy):
    policy = policy or {}
    return {
        'enabled': bool(policy.get('enabled')),
        'allow': list(policy.get('allow') or []),
        'deny': list(policy.get('deny') or []),
    }


def _slim_permission(row):
    item = {
        'name': row.get('name') or '',
        'domain': row['domain'],
        'service': row['service'],
        'rate_per_hour': int(row.get('rate_per_hour') or 0),
        'window': row.get('window') or '',
        'expires_at': int(row.get('expires_at') or 0),
        'suspended_at': int(row.get('suspended_at') or 0),
        'suspended_reason': row.get('suspended_reason') or '',
    }
    if row.get('label_id'):
        item['label_id'] = row['label_id']
    else:
        item['entities'] = list(row.get('entities') or [])
    return item


def get_agent_config(agent_id):
    agent = _agent_or_404(agent_id)
    policy = get_agent_policy(agent_id)
    web = get_web_fetch_policy(agent.get('name') or '')
    rows = _current_ha_rows(agent_id)
    return {
        'schema_version': SCHEMA_VERSION,
        'version': get_agent_config_version(agent_id),
        'agent': {'id': int(agent_id), 'name': agent.get('name') or '',
                  'revoked': bool(agent.get('revoked'))},
        'tools': _policy_doc(policy),
        'ssh': {'mode': policy.get('ssh_mode') or 'approval',
                'hosts': list(policy.get('ssh_hosts_allow') or [])},
        'websites': _web_doc(web),
        'home_assistant': {'permissions': [_slim_permission(r) for r in rows]},
    }


def _policy_view(clean):
    return {k: clean.get(k) for k in
            ('tools_mode', 'tools', 'arg_policy', 'approval',
             'rate_limit_per_min', 'ssh_hosts_allow', 'ssh_mode')}


def _policy_changed(current, clean):
    return _policy_view(current) != _policy_view(clean)


def _web_changed(current, clean):
    return _web_doc(current) != clean


def _write_web(conn, principal, web):
    conn.execute(
        'INSERT OR REPLACE INTO web_fetch_policies '
        '(principal, enabled, allow_json, deny_json, updated_at) '
        'VALUES (?, ?, ?, ?, ?)',
        (principal, 1 if web['enabled'] else 0,
         json.dumps(web['allow']), json.dumps(web['deny']),
         int(time.time())))


def _summary_sections(agent_name, policy_info, web_info, ha_diff,
                      waiting=None):
    changed = []
    if policy_info:
        tools = policy_info['tools']
        ssh = policy_info['ssh']
        bits = []
        if tools.get('changed'):
            if tools['mode'] == 'all':
                bits.append('all tools')
            else:
                bits.append('%d chosen tool%s'
                            % (len(tools['allow']),
                               '' if len(tools['allow']) == 1 else 's'))
        if ssh.get('changed'):
            hosts = len(ssh['hosts'])
            if not hosts:
                bits.append('SSH off')
            elif '*' in ssh['hosts']:
                bits.append('SSH on any gateway')
            else:
                bits.append('SSH on %d host%s'
                            % (hosts, '' if hosts == 1 else 's'))
        if bits:
            changed.append({'section': 'tools', 'summary': '; '.join(bits)})
    if web_info and web_info.get('changed'):
        changed.append({
            'section': 'websites',
            'summary': ('websites enabled with %d allowed host%s'
                        % (len(web_info['allow']),
                           '' if len(web_info['allow']) == 1 else 's'))
                       if web_info['enabled'] else 'websites disabled',
        })
    ha_summary = ''
    if ha_diff is not None:
        ha_summary = _permission_summary(agent_name, ha_diff, waiting)
        if (ha_diff['added'] or ha_diff['changed'] or ha_diff['removed']):
            changed.append({'section': 'home_assistant',
                            'summary': ha_summary})
    parts = [c['summary'] for c in changed]
    if not parts:
        summary = '%s is unchanged.' % agent_name
    elif len(parts) == 1 and parts[0].startswith(agent_name):
        summary = parts[0]
    else:
        summary = '%s: %s' % (agent_name, ' '.join(parts))
    return changed, summary


def _validate_config(agent_id, document, version):
    agent = _agent_or_404(agent_id)
    _check_version(agent_id, version)
    if not isinstance(document, dict):
        _error(400, 'document must be an object.', 'invalid_config')
    _check_keys(document, _TOP_KEYS, 'document')
    schema = document.get('schema_version')
    if (not isinstance(schema, int) or isinstance(schema, bool)
            or schema != SCHEMA_VERSION):
        _error(400, 'schema_version must be %d.' % SCHEMA_VERSION,
               'schema_version')
    current_policy = get_agent_policy(agent_id)
    clean = dict(current_policy)
    policy_touched = False
    if 'tools' in document:
        _check_keys(document['tools'], _TOOL_KEYS, 'tools')
        policy_touched = True
    if 'ssh' in document:
        _check_keys(document['ssh'], _SSH_KEYS, 'ssh')
        policy_touched = True
    if policy_touched:
        tools = document.get('tools') or {}
        ssh = document.get('ssh') or {}
        mode = tools.get('mode', current_policy['tools_mode'])
        allow = tools.get('allow', current_policy['tools'])
        approval = tools.get('approval', current_policy['approval'])
        rules = tools.get('argument_rules', current_policy['arg_policy'])
        rate = tools.get('rate_limit_per_min',
                         current_policy['rate_limit_per_min'])
        ssh_mode = ssh.get('mode', current_policy['ssh_mode'])
        hosts = ssh.get('hosts', current_policy['ssh_hosts_allow'])
        try:
            clean = validate_policy(mode, allow, rules, approval, rate,
                                    hosts, ssh_mode)
        except ValueError as exc:
            _error(400, str(exc), 'invalid_config')
        clean['test_agent'] = bool(current_policy.get('test_agent'))
        if clean['test_agent']:
            try:
                _validate_test_agent_policy(clean)
            except ValueError as exc:
                _error(400, str(exc), 'invalid_config')

    current_web = get_web_fetch_policy(agent.get('name') or '')
    web = _web_doc(current_web)
    web_touched = 'websites' in document
    if web_touched:
        _check_keys(document['websites'], _WEBSITE_KEYS, 'websites')
        raw = document['websites']
        enabled = raw.get('enabled', bool((current_web or {}).get('enabled')))
        if not isinstance(enabled, bool):
            _error(400, 'websites.enabled must be true or false.',
                   'invalid_config')
        try:
            allow, deny = validate_web_policy(
                raw.get('allow', (current_web or {}).get('allow') or []),
                raw.get('deny', (current_web or {}).get('deny') or []))
        except ValueError as exc:
            _error(400, str(exc), 'invalid_config')
        web = {'enabled': enabled, 'allow': allow, 'deny': deny}

    ha_touched = 'home_assistant' in document
    prepared = None
    ha_diff = None
    if ha_touched:
        _check_keys(document['home_assistant'], _HA_KEYS, 'home_assistant')
        raw = document['home_assistant']
        if 'permissions' not in raw:
            ha_touched = False
        else:
            registry = _allowances._ha_registry()
            prepared = prepare_permissions(raw['permissions'], registry=registry)
            ha_diff = _diff_permissions(_current_ha_rows(agent_id), prepared)
    return {
        'agent': agent,
        'policy': clean,
        'policy_touched': policy_touched,
        'policy_changed': policy_touched and _policy_changed(current_policy, clean),
        'web': web,
        'current_web': current_web,
        'web_touched': web_touched,
        'web_changed': web_touched and _web_changed(current_web, web),
        'ha_touched': ha_touched,
        'prepared': prepared,
        'ha_diff': ha_diff,
    }


def _ha_risk(prepared):
    return _risk_items(prepared or [])


def _audit_changes(current_policy, policy, policy_touched, current_web, state):
    """Before → after for the widening fields Security asked for."""
    import json as _json
    parts = []
    if policy_touched:
        if policy['tools_mode'] != current_policy.get('tools_mode'):
            parts.append('tools.mode %s->%s'
                         % (current_policy.get('tools_mode'),
                            policy['tools_mode']))
        if policy['ssh_mode'] != current_policy.get('ssh_mode'):
            parts.append('ssh.mode %s->%s'
                         % (current_policy.get('ssh_mode'),
                            policy['ssh_mode']))
        old_hosts = list(current_policy.get('ssh_hosts_allow') or [])
        new_hosts = list(policy.get('ssh_hosts_allow') or [])
        if old_hosts != new_hosts:
            parts.append('ssh.hosts %s->%s'
                         % (_json.dumps(old_hosts),
                            _json.dumps(new_hosts)))
        old_approval = current_policy.get('approval') or {}
        for key, value in (policy.get('approval') or {}).items():
            if value == 'ungated' and old_approval.get(key) != 'ungated':
                parts.append('approval[%s] %s->ungated'
                             % (key, old_approval.get(key) or 'inherit'))
    if state['web_changed']:
        old = _web_doc(current_web)
        new = state['web']
        if old['enabled'] != new['enabled']:
            parts.append('websites.enabled %s->%s'
                         % (old['enabled'], new['enabled']))
        if old['allow'] != new['allow']:
            parts.append('websites.allow %s->%s'
                         % (_json.dumps(old['allow']),
                            _json.dumps(new['allow'])))
        if old['deny'] != new['deny']:
            parts.append('websites.deny %s->%s'
                         % (_json.dumps(old['deny']),
                            _json.dumps(new['deny'])))
    return '; '.join(parts)


def check_or_apply_config(actor, agent_id, document, preview=False,
                          risk_ack=False, version=None, preview_hash=None,
                          resume_ack=False):
    state = _validate_config(agent_id, document, version)
    agent = state['agent']
    policy = state['policy']
    current_policy = get_agent_policy(agent_id)
    policy_info = None
    if state['policy_touched']:
        tools_changed = (
            policy['tools_mode'] != current_policy.get('tools_mode')
            or list(policy['tools']) != list(current_policy.get('tools') or [])
            or dict(policy['approval']) != dict(current_policy.get('approval') or {})
            or dict(policy['arg_policy']) != dict(current_policy.get('arg_policy') or {})
            or int(policy['rate_limit_per_min'])
            != int(current_policy.get('rate_limit_per_min') or 0))
        tools_info = {
            'changed': tools_changed,
            'mode': policy['tools_mode'],
            'allow': policy['tools'],
        }
        ssh_old_mode = current_policy.get('ssh_mode')
        ssh_old_hosts = current_policy.get('ssh_hosts_allow') or []
        ssh_changed = (policy['ssh_mode'] != ssh_old_mode
                       or list(policy['ssh_hosts_allow']) != list(ssh_old_hosts))
        policy_info = {
            'tools': tools_info,
            'ssh': {'changed': ssh_changed, 'mode': policy['ssh_mode'],
                    'hosts': policy['ssh_hosts_allow']},
        }
    web_info = None
    if state['web_touched']:
        web_info = dict(state['web'])
        web_info['changed'] = state['web_changed']
    ha_diff = state['ha_diff']
    prepared = state['prepared']
    names = _name_map((prepared or []) + _current_ha_rows(agent_id))
    ha_view = (_permission_view(ha_diff, names) if ha_diff is not None else None)
    risks = _ha_risk(prepared)
    resumes = _resumes(ha_diff) if ha_diff is not None else []
    waiting = _waiting(ha_diff) if ha_diff is not None else []
    current_version = get_agent_config_version(agent_id)
    changed_sections, summary = _summary_sections(
        agent.get('name') or 'agent', policy_info, web_info, ha_diff, waiting)
    changed = bool(changed_sections)
    sections = _section_fingerprint(state)
    computed = _preview_hash(current_version, prepared or [], risks,
                             resumes, waiting, sections=sections)
    result = {
        'agent': {'id': int(agent_id), 'name': agent.get('name') or ''},
        'version': current_version,
        'preview': bool(preview),
        'changed': changed,
        'summary': summary,
        'sections': changed_sections,
        'risky': risks,
        'risk_ack_required': bool(risks),
        'resumes': resumes,
        'resume_ack_required': bool(resumes),
        'waiting': _waiting_view(waiting, names),
        'preview_hash': computed,
    }
    if ha_view is not None:
        result['home_assistant'] = ha_view
    if preview:
        return result
    if not preview_hash:
        _error(400, 'preview_hash is required: reload the preview.',
               'preview_hash_required')
    if preview_hash != computed:
        _error(409, 'The preview changed; reload it.', 'preview_changed')
    if risks and not risk_ack:
        _error(400, 'This change needs an explicit risk confirmation.',
               'risk_ack_required', risky=risks)
    if resumes and not resume_ack:
        _error(400, 'This change resumes a suspended permission; confirm it.',
               'resume_ack_required', resumes=resumes)
    if not changed:
        result['status'] = 'ok'
        result['document'] = get_agent_config(agent_id)
        return result
    import db.core as _core
    now = int(time.time())
    with _core.db_conn() as conn:
        try:
            conn.execute('BEGIN IMMEDIATE')
            tx_version = _version_row(conn, agent_id)
            if int(version) != tx_version:
                conn.rollback()
                _error(409, 'The agent config changed since it was loaded; '
                       'reload it.', 'config_changed', version=tx_version)
            tx_diff = None
            tx_resumes = []
            tx_waiting = []
            if state['ha_touched'] and prepared is not None:
                tx_current = _current_ha_rows(agent_id, conn=conn)
                tx_diff = _diff_permissions(tx_current, prepared)
                tx_resumes = _resumes(tx_diff)
                tx_waiting = _waiting(tx_diff)
            tx_hash = _preview_hash(tx_version, prepared or [], risks,
                                    tx_resumes, tx_waiting,
                                    sections=sections)
            if preview_hash != tx_hash:
                conn.rollback()
                _error(409, 'The preview changed; reload it.',
                       'preview_changed')
            if tx_resumes and not resume_ack:
                conn.rollback()
                _error(400, 'This change resumes a suspended permission; '
                       'confirm it.', 'resume_ack_required',
                       resumes=tx_resumes)
            revoked = []
            created = []
            if state['policy_changed']:
                write_policy_row(conn, agent_id, policy)
            if state['web_changed']:
                _write_web(conn, agent.get('name') or '', state['web'])
            if state['ha_touched'] and prepared is not None:
                revoked, created = _write_ha_diff(
                    conn, agent_id, tx_diff, actor, now, prepared=prepared)
            new_version = bump_agent_config_version(agent_id, conn)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
    from db.audit import record_audit_event
    if revoked or created or resumes:
        _audit_ha(actor, agent_id, revoked, created, resumes=resumes)
    changes = _audit_changes(current_policy, policy, state['policy_touched'],
                             state['current_web'], state)
    record_audit_event(
        'agent_config_updated',
        details='agent #%d config by %s: %s; changes: %s'
                % (int(agent_id), actor,
                   '; '.join(c['section'] for c in changed_sections) or 'none',
                   changes or 'none'))
    result['status'] = 'ok'
    result['version'] = new_version
    result['document'] = get_agent_config(agent_id)
    return result

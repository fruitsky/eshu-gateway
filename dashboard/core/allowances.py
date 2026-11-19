"""F27: the allowance ceiling — the catalogue, the DB selection, the checks.

The ceiling is the outer limit on what an allowance may ever name. It never
approves anything; it only refuses to let an agent (or an allowance) reach a
tool, a domain or a service outside it.

* **Catalogue in code.** Types, actions, plain labels, risk text, defaults
  and the `stop` exemption ship in `CATALOGUE`. The DB stores a selection by
  catalogue id; a row that is no longer in the catalogue is ignored.
* **Never lists in code.** `NEVER_DOMAINS` and `NEVER_SERVICES` (BV1 adds
  `open`) are read-only and are checked after the ceiling.
* **Destructive words.** Built-ins (BD1 + AZ6) plus the operator's words,
  matched on `[a-z0-9]` runs so `_` separates (`alarm_disarm`). A catalogue
  row can exempt one named built-in word (`exempt_words`); operator words
  are never exempt.
* **Seeded once.** `allowance_ceiling_v1`; new catalogue rows in a later
  release ship disabled. An empty ceiling allows nothing and is never
  re-seeded.
"""
import hashlib
import re
import time

INTEGRATION = 'ha'
INIT_MARKER = 'allowance_ceiling_v1'

# Built-in destructive words: BD1's list plus AZ6's `restart`. `open` is
# deliberately not here (BV1): it would cap cover.open_cover.
DESTRUCTIVE_WORDS = ('delete', 'remove', 'purge', 'format', 'reset', 'stop',
                     'shutdown', 'reboot', 'disable', 'unlock', 'disarm',
                     'restart')

NEVER_DOMAINS = {
    'script': 'runs a script someone else wrote',
    'automation': 'changes what HA does on its own',
    'shell_command': 'runs a shell command on the HA host',
    'hassio': 'manages the Supervisor and add-ons',
    'homeassistant': 'restarts or stops HA itself',
    'python_script': 'runs Python inside HA',
    'rest_command': 'calls an arbitrary URL',
    'notify': 'sends messages outside the house',
    'update': 'installs firmware or software',
    'recorder': "deletes HA's own history",
    'pyscript': 'runs custom Python',
}
NEVER_SERVICES = {
    'disarm': 'disarms an alarm',
    'unlock': 'unlocks a lock',
    'open': 'opens a lock or a valve',
    'install': 'installs software or firmware',
    'purge': 'deletes stored data',
    'delete': 'deletes stored data',
    'reset': 'resets a device',
}

# One entry per device type. `actions` maps the HA service to the label the
# operator sees. `risk` is shown word for word; `default` is used only by the
# one-time seed.
CATALOGUE = [
    {'domain': 'light', 'label': 'Lights', 'default': True, 'risk': '',
     'actions': {'turn_on': 'Turn on', 'turn_off': 'Turn off',
                 'toggle': 'Toggle'}},
    {'domain': 'switch', 'label': 'Smart plugs and switches', 'default': True,
     'risk': '',
     'actions': {'turn_on': 'Turn on', 'turn_off': 'Turn off',
                 'toggle': 'Toggle'}},
    {'domain': 'fan', 'label': 'Fans', 'default': True, 'risk': '',
     'actions': {'turn_on': 'Turn on', 'turn_off': 'Turn off',
                 'toggle': 'Toggle'}},
    {'domain': 'input_boolean', 'label': 'HA helpers', 'default': True,
     'risk': '',
     'actions': {'turn_on': 'Turn on', 'turn_off': 'Turn off',
                 'toggle': 'Toggle'}},
    {'domain': 'media_player', 'label': 'Media players', 'default': True,
     'risk': '',
     'actions': {'turn_on': 'Turn on', 'turn_off': 'Turn off',
                 'media_play': 'Play', 'media_pause': 'Pause',
                 'media_stop': 'Stop'}},
    {'domain': 'cover', 'label': 'Covers: blinds, garage doors, gates',
     'default': False,
     'risk': ('Physical security and safety. Opening a garage door or gate '
              'is a physical access event; a closing blind or door can trap '
              'a person or an animal.'),
     'actions': {'open_cover': 'Open', 'close_cover': 'Close',
                 'stop_cover': 'Stop'}},
    {'domain': 'climate', 'label': 'Heating and climate', 'default': False,
     'risk': ('Cost and comfort. Heating or cooling runs for as long as the '
              'device decides; a stuck call costs money and can be unsafe.'),
     'actions': {'turn_on': 'Turn on', 'turn_off': 'Turn off'}},
]

# BV1/answer 1: halting a cover or a player is the safe direction. The
# exemption names the one *built-in* word; an operator-added `stop` still
# caps these rows.
_EXEMPT = {('cover', 'stop_cover'): ('stop',),
           ('media_player', 'media_stop'): ('stop',)}


def _rows():
    """(catalogue_id, domain, service, action_label, type_entry) once each."""
    for entry in CATALOGUE:
        for service, action_label in entry['actions'].items():
            yield (f"{entry['domain']}.{service}", entry['domain'], service,
                   action_label, entry)


def catalogue_row(integration, catalogue_id):
    if integration != INTEGRATION:
        return None
    for cid, domain, service, label, entry in _rows():
        if cid != catalogue_id:
            continue
        return {'id': cid, 'domain': domain, 'service': service,
                'action_label': label, 'type_label': entry['label'],
                'risk': entry['risk'], 'default': entry['default'],
                'exempt_words': list(_EXEMPT.get((domain, service), ()))}
    return None


def all_ids():
    return [row[0] for row in _rows()]


def is_risky(catalogue_id):
    row = catalogue_row(INTEGRATION, catalogue_id)
    return bool(row and row['risk'])


def _tokens(text):
    return [t for t in re.split(r'[^a-z0-9]+', (text or '').lower()) if t]


def operator_words():
    from db.core import db_conn
    try:
        with db_conn() as conn:
            rows = conn.execute(
                'SELECT word FROM allowance_never_words ORDER BY word').fetchall()
    except Exception:
        return []
    return [r['word'] for r in rows]


def destructive_words():
    return list(DESTRUCTIVE_WORDS) + operator_words()


def is_destructive(service, exempt=()):
    """BD1 matching: `_` separates words; one *built-in* word may be exempt.

    This is the same decision as the ceiling's deny rule; keep it in
    `_ceiling_denied` so there is exactly one copy of the loop.
    """
    return _ceiling_denied(service, exempt, operator_words())


def never_reason(domain, service):
    if domain in NEVER_DOMAINS:
        return NEVER_DOMAINS[domain]
    for word, reason in NEVER_SERVICES.items():
        if service == word or service.endswith('_' + word):
            return reason
    return ''


def ensure_initialised():
    """Create and seed the ceiling once. Idempotent; never re-seeds."""
    from db.core import db_conn
    from db.audit import record_audit_event
    from db.policies import get_meta, set_meta
    with db_conn() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS allowance_ceiling (
            integration TEXT NOT NULL,
            catalogue_id TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (integration, catalogue_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS allowance_never_words (
            word TEXT PRIMARY KEY,
            added_by TEXT NOT NULL DEFAULT '',
            added_at INTEGER NOT NULL DEFAULT 0)''')
        conn.commit()
    if get_meta(INIT_MARKER):
        return
    now = int(time.time())
    first_seed = not get_meta('allowance_ceiling_since')
    with db_conn() as conn:
        for cid, _domain, _service, _label, entry in _rows():
            conn.execute(
                'INSERT OR IGNORE INTO allowance_ceiling '
                '(integration, catalogue_id, enabled, updated_by, updated_at) '
                'VALUES (?, ?, ?, ?, ?)',
                (INTEGRATION, cid, 1 if entry['default'] else 0, '', now))
        conn.commit()
    if first_seed:
        set_meta('allowance_ceiling_since', str(now))
    set_meta(INIT_MARKER, str(now))
    record_audit_event(
        'allowance_ceiling_seeded',
        details='Allowance ceiling seeded: %d catalogue rows; covers and '
                'climate off.' % len(all_ids()))


def _enabled_map(integration=INTEGRATION):
    from db.core import db_conn
    with db_conn() as conn:
        rows = conn.execute(
            'SELECT catalogue_id, enabled, updated_at, updated_by '
            'FROM allowance_ceiling WHERE integration = ?', (integration,)
        ).fetchall()
    return {r['catalogue_id']: {'enabled': bool(r['enabled']),
                                'updated_at': r['updated_at'],
                                'updated_by': r['updated_by']} for r in rows}


def enabled_ids(integration=INTEGRATION):
    ensure_initialised()
    return sorted(cid for cid, row in _enabled_map(integration).items()
                  if row['enabled'] and catalogue_row(integration, cid))


def _enabled_hash(ids):
    h = hashlib.sha256()
    for cid in sorted(ids):
        h.update(cid.encode() + b'\0')
    return h.hexdigest()[:16]


def set_hash(integration=INTEGRATION):
    """A stable hash of the enabled set, for the stale-form check (BV2)."""
    return _enabled_hash(enabled_ids(integration))


def apply_ceiling(actor, integration, wanted, risk_ack=False,
                  expected_hash=''):
    """Replace the enabled set; validate, audit and notify a widening.

    BX6: `set_hash` is required, and the compare plus the write share one
    `BEGIN IMMEDIATE`, so two saves cannot both pass the compare.
    """
    from db.core import db_conn
    from db.audit import record_audit_event
    from core.notify import send_notify
    ensure_initialised()
    if integration != INTEGRATION:
        raise CeilingError(400, 'This integration has no ceiling yet.')
    if not expected_hash:
        raise CeilingError(
            400, 'set_hash is required: reload the ceiling before saving.',
            {'code': 'set_hash_required'})
    wanted = sorted(set(wanted or []))
    unknown = [cid for cid in wanted if catalogue_row(integration, cid) is None]
    if unknown:
        raise CeilingError(400, 'Unknown catalogue id: ' + ', '.join(unknown))
    for cid in wanted:
        row = catalogue_row(integration, cid)
        if never_reason(row['domain'], row['service']):
            raise CeilingError(400, 'Never allowable: ' + cid)
        if is_destructive(row['service'], row['exempt_words']):
            raise CeilingError(400, 'A destructive word caps: ' + cid)

    before = set()
    enabling = []
    disabling = []
    risky = []
    now = _now()
    with db_conn() as conn:
        try:
            conn.execute('BEGIN IMMEDIATE')
            rows = conn.execute(
                'SELECT catalogue_id FROM allowance_ceiling '
                'WHERE integration = ? AND enabled = 1', (integration,)
            ).fetchall()
            before = {r['catalogue_id'] for r in rows
                      if catalogue_row(integration, r['catalogue_id'])}
            current_hash = _enabled_hash(before)
            if expected_hash != current_hash:
                conn.rollback()
                raise CeilingError(
                    409, 'The ceiling changed since this form was loaded.',
                    {'code': 'ceiling_changed', 'set_hash': current_hash})
            after = set(wanted)
            enabling = sorted(after - before)
            disabling = sorted(before - after)
            risky = [cid for cid in enabling if is_risky(cid)]
            if risky and not risk_ack:
                conn.rollback()
                raise CeilingError(
                    400, 'This change needs a risk confirmation.',
                    {'code': 'risk_ack_required', 'risky': risky})
            for cid, _domain, _service, _label, _entry in _rows():
                conn.execute(
                    'UPDATE allowance_ceiling SET enabled = ?, updated_by = ?, '
                    'updated_at = ? WHERE integration = ? AND catalogue_id = ?',
                    (1 if cid in after else 0, actor, now, integration, cid))
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
    record_audit_event(
        'allowance_ceiling_changed',
        details='%s ceiling: enabled [%s] -> [%s]; set %s; risky %s'
                % (integration, ','.join(sorted(before)), ','.join(sorted(after)),
                   set_hash(integration), ','.join(risky) or 'none'))
    if enabling:
        # BT5: a widening goes out on the existing notify channel. No entity
        # lists, just the verdict.
        send_notify('ceiling', 'Allowance ceiling widened',
                    'Enabled: %s (by %s)' % (', '.join(enabling), actor))
    return {'before': sorted(before), 'after': sorted(after),
            'enabling': enabling, 'disabling': disabling,
            'set_hash': set_hash(integration), 'risky': risky}


def _ceiling_denied(service, exempt_words, operator):
    """The one destructive-word rule, shared by both call styles.

    Built-in words and operator words both cap an action, except for a
    catalogue row's explicit exemption (cover stop is the safe direction).
    An operator word always wins, even over an exemption.
    """
    words = set(DESTRUCTIVE_WORDS) | set(operator)
    allowed = set(exempt_words or ()) - set(operator)
    for token in _tokens(service):
        if token in words and token not in allowed:
            return True
    return False


def ceiling_allows(integration, domain, service, conn=None):
    """The one check used at save, apply and match.

    ``conn`` is optional. When supplied, the ceiling and operator-word rows
    are read on that transaction so an apply can re-check without a second
    connection; otherwise the normal cached/read helper is used. The deny
    rule itself lives in ``_ceiling_denied`` above, once.
    """
    ensure_initialised()
    if integration != INTEGRATION:
        return False
    catalogue_id = f'{domain}.{service}'
    row = catalogue_row(integration, catalogue_id)
    if row is None:
        return False
    if never_reason(domain, service):
        return False
    if conn is None:
        operator = set(operator_words())
        if _ceiling_denied(service, row.get('exempt_words'), operator):
            return False
        info = _enabled_map(integration).get(catalogue_id)
        return bool(info and info['enabled'])
    operator = {r['word'] for r in conn.execute(
        'SELECT word FROM allowance_never_words').fetchall()}
    if _ceiling_denied(service, row.get('exempt_words'), operator):
        return False
    info = conn.execute(
        'SELECT enabled FROM allowance_ceiling WHERE integration = ? '
        'AND catalogue_id = ?', (INTEGRATION, catalogue_id)).fetchone()
    return bool(info and info['enabled'])


def catalogue(integration=INTEGRATION):
    enabled = _enabled_map(integration)
    out = []
    for entry in CATALOGUE:
        actions = []
        for service, label in entry['actions'].items():
            cid = f"{entry['domain']}.{service}"
            info = enabled.get(cid, {})
            actions.append({
                'id': cid, 'service': service, 'label': label,
                'enabled': bool(info.get('enabled')),
                'exempt_words': list(_EXEMPT.get((entry['domain'], service), ())),
            })
        out.append({'domain': entry['domain'], 'label': entry['label'],
                    'risk': entry['risk'], 'default': entry['default'],
                    'actions': actions})
    return out


def state(integration=INTEGRATION):
    ensure_initialised()
    enabled = _enabled_map(integration)
    changed = [v['updated_at'] for v in enabled.values() if v['updated_at']]
    return {
        'integration': integration,
        'catalogue': catalogue(integration),
        'never': {
            'domains': [{'value': k, 'reason': v}
                        for k, v in NEVER_DOMAINS.items()],
            'services': [{'value': k, 'reason': v}
                         for k, v in NEVER_SERVICES.items()],
        },
        'words': {'builtin': list(DESTRUCTIVE_WORDS),
                  'operator': operator_words()},
        'enabled': enabled_ids(integration),
        'set_hash': set_hash(integration),
        'updated_at': max(changed) if changed else None,
    }


class CeilingError(ValueError):
    """A refused ceiling change; the route maps these to HTTP errors."""

    def __init__(self, status, detail, extra=None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.extra = extra or {}


_WORD_RE = re.compile(r'^[a-z0-9_]{3,32}$')


def _now():
    return int(time.time())


def add_word(actor, word):
    """Add an operator word; disable the rows it now caps (BT3)."""
    from db.core import db_conn
    from db.audit import record_audit_event
    ensure_initialised()
    w = (word or '').strip()
    if not _WORD_RE.fullmatch(w):
        raise CeilingError(400, 'A word is 3-32 lowercase letters, digits or _.')
    if w in DESTRUCTIVE_WORDS:
        raise CeilingError(400, 'That word is built in and read-only.')
    if w in operator_words():
        raise CeilingError(400, 'That word is already there.')
    now = _now()
    affected = []
    with db_conn() as conn:
        conn.execute('INSERT INTO allowance_never_words (word, added_by, added_at) '
                     'VALUES (?, ?, ?)', (w, actor, now))
        for cid in enabled_ids(INTEGRATION):
            row = catalogue_row(INTEGRATION, cid)
            # is_destructive() subtracts the operator words from the exemption,
            # so an operator word is never exempt: the same rule here.
            if w in _tokens(row['service']):
                conn.execute(
                    'UPDATE allowance_ceiling SET enabled = 0, updated_by = ?, '
                    'updated_at = ? WHERE integration = ? AND catalogue_id = ?',
                    (actor, now, INTEGRATION, cid))
                affected.append(cid)
        conn.commit()
    record_audit_event(
        'allowance_never_word_added',
        details='Word %r added by %s; disabled %s' %
                (w, actor, ','.join(affected) or 'nothing'))
    return {'word': w, 'disabled': affected, 'set_hash': set_hash(INTEGRATION)}


def remove_word(actor, word):
    """Remove an operator word. This is a widening (BV2), not a resume."""
    from db.core import db_conn
    from db.audit import record_audit_event
    from core.notify import send_notify
    ensure_initialised()
    w = (word or '').strip()
    if w in DESTRUCTIVE_WORDS:
        raise CeilingError(400, 'That word is built in and read-only.')
    if w not in operator_words():
        raise CeilingError(404, 'No such operator word.')
    with db_conn() as conn:
        conn.execute('DELETE FROM allowance_never_words WHERE word = ?', (w,))
        conn.commit()
    record_audit_event(
        'allowance_never_word_removed',
        details='Word %r removed by %s (widening; nothing resumed)' % (w, actor))
    send_notify('ceiling', 'Allowance ceiling widened',
                'Word removed: %s (by %s)' % (w, actor))
    return {'word': w, 'set_hash': set_hash(INTEGRATION)}


# ── F27: allowances (the engine) ───────────────────────────────────────────
#
# One allowance is bound to one agent token, one integration and one tool,
# with exact conditions and a stored entity list. Matching never calls HA:
# the entity ids, and (when available) the registry pins, were resolved when
# the allowance was saved. Every miss is an ordinary card, never a denial.

ALLOWANCE_TOOL = 'call_service'
_ENTITY_RE = re.compile(r'^[a-z0-9_]+\.[a-z0-9_]+$')
_SERVICE_RE = re.compile(r'^[a-z0-9_]+$')


def is_registry_write_path(value):
    """True for an HA entity-registry or config-entry write (BD5/BX2).

    Accepts a WS command (`config/entity_registry/update`), a REST path
    (`/api/config/entity_registry/...`), or a `/config/...` path.
    """
    text = str(value or '').strip().lower().replace('\\', '/').lstrip('/')
    if text.startswith('api/'):
        text = text[4:]
    if text.startswith('homeassistant/'):
        text = text[len('homeassistant/'):]
    return (text.startswith('config/entity_registry/')
            or text.startswith('config/config_entries/'))


class AllowanceError(ValueError):
    def __init__(self, status, detail, extra=None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.extra = extra or {}


# ── S3: protected devices (operator-held, no agent may be granted one) ──────

def _init_protected_table():
    _init_allowance_tables()


def protected_records(conn=None):
    """The protected rows: entity id plus the registry id captured at protect."""
    from db.core import db_conn
    _init_protected_table()
    sql = ('SELECT entity_id, registry_id, added_by, added_at '
           'FROM allowance_protected ORDER BY entity_id')
    if conn is None:
        with db_conn() as own:
            rows = own.execute(sql).fetchall()
    else:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def protected_entities():
    """The entity ids the operator has protected, lower-case."""
    return {str(r['entity_id']).lower() for r in protected_records()}


def protected_rows():
    return protected_records()


def _protected_hits(pins=None, entities=None, conn=None):
    """Protected rows matched by entity id or by the stored registry id.

    A permission's `pins` carry the registry id read when it was saved, so a
    match can refuse a renamed protected entity without reading HA.
    """
    rows = protected_records(conn=conn)
    by_entity = {str(r['entity_id']).lower(): r for r in rows}
    by_registry = {str(r.get('registry_id')): r for r in rows
                   if r.get('registry_id')}
    hits = []
    seen = set()
    for ent in entities or []:
        e = str(ent or '').lower()
        if e in by_entity and e not in seen:
            hits.append(by_entity[e])
            seen.add(e)
    for pin in pins or []:
        if not isinstance(pin, dict):
            continue
        e = str(pin.get('entity_id') or '').lower()
        rid = str(pin.get('registry_id') or '')
        row = by_entity.get(e) or (by_registry.get(rid) if rid else None)
        if row and row['entity_id'] not in seen:
            hits.append(row)
            seen.add(row['entity_id'])
    return hits


def set_protected_entities(actor, entities):
    """Replace the protected list; audit every add/remove.

    Adding a device also suspends every live permission that already holds it
    (reason `protected`) in the same transaction, because protection is a
    promise and an old grant must not come back when protection is removed.
    Removal never resumes anything; Resume is an explicit operator action.
    """
    from db.core import db_conn
    from db.audit import record_audit_event
    from core.notify import send_notify
    _init_protected_table()
    if not isinstance(entities, (list, tuple)):
        raise AllowanceError(400, 'Protected devices must be a list.')
    wanted = []
    for ent in entities:
        e = str(ent or '').strip().lower()
        if not _ENTITY_RE.fullmatch(e):
            raise AllowanceError(400, 'Not a plain entity id: %r' % ent)
        if e not in wanted:
            wanted.append(e)
    if len(wanted) > 256:
        raise AllowanceError(400, 'At most 256 protected devices.')
    before_rows = protected_records()
    before = {r['entity_id'] for r in before_rows}
    after = set(wanted)
    added = sorted(after - before)
    removed = sorted(before - after)
    added_registry = {}
    registry_unreachable = False
    if added:
        try:
            registry = _ha_registry()
        except AllowanceError as exc:
            # Security answer (a): protection is a tightening, so it must not
            # wait for HA. A 503 means the registry could not be read; accept
            # the entity id now and backfill the registry id on the next
            # successful refresh. A reachable HA that does not know the id is
            # still a 400 below.
            if getattr(exc, 'status', None) != 503:
                raise
            registry = None
            registry_unreachable = True
        if registry is not None:
            for e in added:
                reg = registry.get(e)
                if not reg:
                    raise AllowanceError(
                        400, 'HA does not know the protected entity %s.' % e)
                added_registry[e] = reg.get('id') or ''
        else:
            added_registry = {e: '' for e in added}
    now = _now()
    suspended = []
    if added or removed:
        # A cached device list would otherwise show a stale lock for 60 s.
        try:
            from core import ha_devices as _ha_devices
            _ha_devices.clear_cache()
        except Exception:
            pass
        import json as _json
        affected = set()
        new_registry = set(v for v in added_registry.values() if v)
        added_set = set(added)
        with db_conn() as conn:
            try:
                conn.execute('BEGIN IMMEDIATE')
                rows = conn.execute(
                    'SELECT id, agent_id, suspended_at, entities_json '
                    'FROM allowances WHERE revoked_at = 0').fetchall()
                for row in rows:
                    try:
                        pins = _json.loads(row['entities_json'] or '[]')
                    except (TypeError, ValueError):
                        pins = []
                    held = [p for p in pins
                            if isinstance(p, dict)
                            and (str(p.get('entity_id') or '').lower()
                                 in added_set
                                 or (p.get('registry_id')
                                     and p.get('registry_id') in new_registry))]
                    if not held:
                        continue
                    affected.add(int(row['agent_id']))
                    if not row['suspended_at']:
                        conn.execute(
                            'UPDATE allowances SET suspended_at = ?, '
                            'suspended_reason = ? WHERE id = ?',
                            (now, 'protected', row['id']))
                        suspended.append((row['id'], row['agent_id']))
                for e in removed:
                    conn.execute(
                        'DELETE FROM allowance_protected WHERE entity_id = ?', (e,))
                for e in added:
                    conn.execute(
                        'INSERT OR IGNORE INTO allowance_protected '
                        '(entity_id, registry_id, added_by, added_at) '
                        'VALUES (?, ?, ?, ?)',
                        (e, added_registry.get(e) or '', actor, now))
                for aid in sorted(affected):
                    _bump_config(conn, aid)
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
        for e in added:
            note = ('registry id pending: HA was unreachable'
                    if registry_unreachable
                    else 'registry %s' % (added_registry.get(e) or '-'))
            record_audit_event(
                'allowance_protected_added',
                details='%s protected by %s (%s)' % (e, actor, note))
        for e in removed:
            record_audit_event(
                'allowance_protected_removed',
                details='%s unprotected by %s' % (e, actor))
            try:
                send_notify('ceiling', 'Protected device removed',
                            '%s is no longer protected. Existing permissions '
                            'stay suspended; Resume each one explicitly.' % e)
            except Exception:
                pass
        for aid, agent_id in suspended:
            record_audit_event(
                'allowance_suspended',
                details='allowance #%d suspended by %s: protected '
                        '(agent #%d)' % (aid, actor, int(agent_id)))
    return {'entities': sorted(after), 'added': added, 'removed': removed,
            'suspended': [aid for aid, _agent in suspended]}



def is_protected(entity_id):
    return str(entity_id or '').strip().lower() in protected_entities()


# ── S4/CB1: the HA integration pin on each permission ──────────────────────

def _ha_base_fp(base_url):
    value = str(base_url or '').strip().rstrip('/')
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]


def _ha_pin():
    """(integration id, base-URL fingerprint) for the one enabled HA."""
    integration = _ha_integration()
    if not integration:
        return 0, ''
    return (int(integration.get('id') or 0),
            _ha_base_fp(integration.get('base_url')))


def _pin_mismatch(row, pin=None):
    """True only when a pinned permission no longer points at this HA.

    An unpinned legacy row is not a mismatch; `refresh_labels` backfills it.
    """
    iid, fp = pin if pin is not None else _ha_pin()
    if not iid:
        return False
    old_iid = int((row or {}).get('ha_integration_id') or 0)
    old_fp = (row or {}).get('ha_base_url_fp') or ''
    if not old_iid and not old_fp:
        return False
    return old_iid != iid or old_fp != fp


def _write_ha_pin(conn, aid, iid, fp):
    conn.execute(
        'UPDATE allowances SET ha_integration_id = ?, ha_base_url_fp = ? '
        'WHERE id = ?', (int(iid or 0), fp or '', int(aid)))


def _bump_config(conn, agent_id):
    """Move the agent's config version inside this write's transaction.

    Every allowance write changes what GET /api/agents/{id}/config returns,
    so a document loaded before it must be a 409. Keeping this in the core
    writer (not the HTTP endpoint) covers the approver path and background
    refreshes too.
    """
    from db.agent_policies import bump_agent_config_version
    return bump_agent_config_version(agent_id, conn)


_TABLES_READY = set()


def _init_allowance_tables():
    from db.core import DB_PATH, db_conn
    key = DB_PATH
    if key in _TABLES_READY:
        return
    with db_conn() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS allowances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL,
            integration TEXT NOT NULL,
            tool TEXT NOT NULL,
            name TEXT NOT NULL,
            conditions_json TEXT NOT NULL,
            entities_json TEXT NOT NULL DEFAULT '[]',
            source_label_id TEXT NOT NULL DEFAULT '',
            suspended_at INTEGER NOT NULL DEFAULT 0,
            suspended_reason TEXT NOT NULL DEFAULT '',
            rate_per_hour INTEGER NOT NULL DEFAULT 0,
            window TEXT NOT NULL DEFAULT '',
            expires_at INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1,
            created_by TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL DEFAULT 0,
            revoked_at INTEGER NOT NULL DEFAULT 0,
            profile_id INTEGER NOT NULL DEFAULT 0,
            profile_version INTEGER NOT NULL DEFAULT 0,
            pending_json TEXT NOT NULL DEFAULT '[]')''')
        conn.execute('''CREATE TABLE IF NOT EXISTS allowance_uses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            allowance_id INTEGER NOT NULL,
            used_at INTEGER NOT NULL,
            call_ref TEXT NOT NULL DEFAULT '')''')
        conn.execute('''CREATE TABLE IF NOT EXISTS allowance_protected (
            entity_id TEXT PRIMARY KEY,
            registry_id TEXT NOT NULL DEFAULT '',
            added_by TEXT NOT NULL DEFAULT '',
            added_at INTEGER NOT NULL DEFAULT 0)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS allowance_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            version INTEGER NOT NULL DEFAULT 1,
            created_by TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0,
            deleted_at INTEGER NOT NULL DEFAULT 0)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS allowance_profile_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL,
            profile_version INTEGER NOT NULL,
            integration TEXT NOT NULL,
            tool TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL,
            service TEXT NOT NULL,
            entities_json TEXT NOT NULL DEFAULT '[]',
            label_id TEXT NOT NULL DEFAULT '',
            rate_per_hour INTEGER NOT NULL DEFAULT 0,
            window TEXT NOT NULL DEFAULT '',
            expires_at INTEGER NOT NULL DEFAULT 0)''')
        try:
            conn.execute("ALTER TABLE allowances ADD COLUMN pending_json "
                         "TEXT NOT NULL DEFAULT '[]'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE allowance_protected ADD COLUMN "
                         "registry_id TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        # S4/CB1: pin the HA integration and its base URL on every permission.
        for col, decl in (('ha_integration_id', 'INTEGER NOT NULL DEFAULT 0'),
                          ('ha_base_url_fp', "TEXT NOT NULL DEFAULT ''")):
            try:
                conn.execute(f"ALTER TABLE allowances ADD COLUMN {col} {decl}")
            except Exception:
                pass
        conn.commit()
    _TABLES_READY.add(key)


def _now():
    return int(time.time())


def _tool_constraints():
    """v1: the only allowable tool, and its three required paths."""
    return {'constrainable': ['domain', 'service', 'data.entity_id'],
            'required': ['domain', 'service', 'data.entity_id']}


def _condition_in(value):
    return {'in': [value]}


def validate_entity_set(domain, entities):
    if not isinstance(entities, list) or not entities:
        raise AllowanceError(400, 'Pick at least one entity.')
    if len(entities) > 64:
        raise AllowanceError(400, 'At most 64 entities per allowance.')
    seen = []
    for ent in entities:
        e = str(ent or '').strip().lower()
        if not _ENTITY_RE.fullmatch(e):
            raise AllowanceError(400, 'Not a plain entity id: %r' % ent)
        if not e.startswith(domain + '.'):
            raise AllowanceError(400, 'Entity %s is not a %s.' % (e, domain))
        if e not in seen:
            seen.append(e)
    return seen


def _ha_integration():
    """The one enabled Home Assistant integration, found by its kind.

    Allowances are stored under the kind `ha`. The operator names the
    integration ("Home Assistant"), so a lookup by name never found it on a
    real install. With no HA integration, or more than one enabled, this is
    None: saves refuse and nothing matches, because an entity id means
    nothing without knowing which HA it belongs to.
    """
    from db.integrations import get_integration, get_integrations
    rows = [r for r in get_integrations()
            if (r.get('kind') or '').lower() == INTEGRATION and r.get('enabled')]
    if len(rows) != 1:
        return None
    return get_integration(rows[0]['name'])


def _is_the_ha_integration(integration):
    """True only for the single enabled HA integration (by id, not name)."""
    if not isinstance(integration, dict):
        return False
    if (integration.get('kind') or '').lower() != INTEGRATION:
        return False
    ha = _ha_integration()
    return bool(ha) and ha.get('id') == integration.get('id')


def _ha_registry():
    """Read the entity registry once (save/apply only; matching never does)."""
    from core.ha_ws import ha_ws_request
    integration = _ha_integration()
    if not integration:
        raise AllowanceError(503, 'Allowances need exactly one enabled Home '
                                  'Assistant integration.')
    try:
        rows = ha_ws_request(integration, 'config/entity_registry/list', {})
    except Exception as exc:
        raise AllowanceError(503, 'Could not read the HA entity registry: %s' % exc)
    return {r.get('entity_id'): r for r in (rows or []) if r.get('entity_id')}


def _risky_classes(entities):
    """Garage/gate/door/lock-like device classes, read at save/apply."""
    from core.ha_ws import ha_ws_request
    integration = _ha_integration()
    if not integration:
        return []
    try:
        states = ha_ws_request(integration, 'get_states', {})
    except Exception as exc:
        raise AllowanceError(503, 'Could not read HA states: %s' % exc)
    wanted = set(entities)
    classes = []
    for st in states or []:
        if st.get('entity_id') not in wanted:
            continue
        attrs = st.get('attributes') or {}
        dc = str(attrs.get('device_class') or '').lower()
        if dc in ('garage', 'garage_door', 'gate', 'door', 'lock'):
            classes.append({'entity_id': st.get('entity_id'), 'device_class': dc})
    return classes


def _risk_for(domain, entities):
    """Answer 6: a risky catalogue row OR a garage/gate/door/lock entity."""
    rows = [catalogue_row(INTEGRATION, f'{domain}.{service}')
            for service in CATALOGUE_BY_DOMAIN.get(domain, {}).get('actions', {})]
    risky_row = any(r and r['risk'] for r in rows)
    return risky_row, _risky_classes(entities)


CATALOGUE_BY_DOMAIN = {e['domain']: e for e in CATALOGUE}


def _validate_allowance(domain, service, entities, rate_per_hour=0, window='',
                        expires_at=0, risk_ack=False, registry=None,
                        return_risk=False, source_label=''):
    """Every save/apply check, in one place, without writing anything.

    Returns `(entities, pins)`, or `(entities, pins, risk)` when
    `return_risk` is set. Raises before the caller revokes or inserts, which
    is what makes apply atomic (BX4).
    """
    domain = (domain or '').strip().lower()
    service = (service or '').strip().lower()
    if not _SERVICE_RE.fullmatch(domain) or not _SERVICE_RE.fullmatch(service):
        raise AllowanceError(400, 'A domain and a service are required.')
    reason = never_reason(domain, service)
    if reason:
        raise AllowanceError(
            400, 'Never allowed: %s.%s — %s.' % (domain, service, reason),
            {'code': 'never_allowed'})
    row = catalogue_row(INTEGRATION, f'{domain}.{service}')
    if row is None:
        raise AllowanceError(400, 'That type and action are not in the catalogue.')
    if not ceiling_allows(INTEGRATION, domain, service):
        raise AllowanceError(400, 'The ceiling does not allow %s.%s.' %
                             (domain, service))
    entities = validate_entity_set(domain, entities)
    if window:
        _check_window(window)
    if rate_per_hour and not (0 <= int(rate_per_hour) <= 1000):
        raise AllowanceError(400, 'The rate cap is out of range.')
    _check_expiry(expires_at)
    # BD5: pin the registry entry now; a missing entity refuses the save.
    if registry is None:
        registry = _ha_registry()
    pins = []
    for ent in entities:
        reg = registry.get(ent)
        if not reg:
            raise AllowanceError(400, 'HA does not know the entity %s.' % ent)
        pins.append({'entity_id': ent, 'registry_id': reg.get('id') or ''})
    # S3/CB5: refuse on the entity id or the registry id captured at protect
    # time, so a renamed protected entity cannot be granted under its new name.
    hits = _protected_hits(pins=pins, entities=entities)
    if hits:
        first = hits[0]['entity_id']
        if source_label:
            message = ('Protected: %s is in label %s. Unprotect it, or pick '
                       'devices instead of the label.' % (first, source_label))
        else:
            message = ('Protected: no agent can be given %s. Unprotect it '
                       'first.' % first)
        raise AllowanceError(
            400, message,
            {'code': 'protected_device',
             'entities': [r['entity_id'] for r in hits]})
    risky_row, risky_entities = _risk_for(domain, entities)
    risk = {'risky_row': risky_row, 'risky_entities': risky_entities}
    if (risky_row or risky_entities) and not risk_ack:
        raise AllowanceError(
            400, 'This allowance needs a second risk confirmation.',
            {'code': 'risk_ack_required', 'risky_row': risky_row,
             'risky_entities': risky_entities})
    if return_risk:
        return entities, pins, risk
    return entities, pins


def _ceiling_allows_conn(conn, domain, service):
    """Compatibility wrapper: the same rule on a caller's transaction.

    Kept so tests and existing call sites can keep injecting this name; the
    decision itself is ``ceiling_allows(..., conn=conn)``.
    """
    return ceiling_allows(INTEGRATION, domain, service, conn=conn)


def _recheck_conn(conn, prepared):
    """Re-check the ceiling and protection inside an apply transaction.

    A ceiling or protection write that landed after validation must not let
    an already-validated row through.
    """
    for item in prepared:
        domain = item['domain']
        service = item['service']
        pins = item.get('pins') or []
        if not _ceiling_allows_conn(conn, domain, service):
            raise AllowanceError(
                400, 'The ceiling no longer allows %s.%s; reload and retry.'
                     % (domain, service), {'code': 'ceiling_changed'})
        hits = _protected_hits(pins=pins, conn=conn)
        if hits:
            first = hits[0]['entity_id']
            label = item.get('label_id') or item.get('source_label') or ''
            if label:
                message = ('Protected: %s is in label %s. Unprotect it, or '
                           'pick devices instead of the label.'
                           % (first, label))
            else:
                message = ('Protected: no agent can be given %s. Unprotect it '
                           'first.' % first)
            raise AllowanceError(
                400, message,
                {'code': 'protected_device',
                 'entities': [r['entity_id'] for r in hits]})


def _insert_allowance(conn, agent_id, name, domain, service, entities, pins,
                      rate_per_hour=0, window='', expires_at=0, version=1,
                      actor='', now=None, profile_id=0, profile_version=0,
                      source_label_id='', ha_integration_id=0,
                      ha_base_url_fp=''):
    """Insert one row on an existing connection (no commit, no audit)."""
    import json as _json
    conditions = {'domain': _condition_in(domain),
                  'service': _condition_in(service),
                  'data.entity_id': {'in': entities}}
    cur = conn.execute(
        'INSERT INTO allowances (agent_id, integration, tool, name, '
        'conditions_json, entities_json, source_label_id, rate_per_hour, '
        'window, expires_at, version, created_by, created_at, '
        'profile_id, profile_version, ha_integration_id, ha_base_url_fp) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (int(agent_id), INTEGRATION, ALLOWANCE_TOOL, (name or 'Allowance')[:80],
         _json.dumps(conditions, sort_keys=True),
         _json.dumps(pins), source_label_id or '',
         int(rate_per_hour or 0), window or '', int(expires_at or 0), int(version),
         actor, now if now is not None else _now(), int(profile_id or 0),
         int(profile_version or 0), int(ha_integration_id or 0),
         ha_base_url_fp or ''))
    return cur.lastrowid


def create_allowance(actor, agent_id, name, domain, service, entities,
                     rate_per_hour=0, window='', expires_at=0,
                     risk_ack=False, profile_id=0, profile_version=0,
                     source_label_id=''):
    """Validate and insert one allowance (save path)."""
    from db.core import db_conn
    from db.audit import record_audit_event
    _init_allowance_tables()
    ensure_initialised()
    domain = (domain or '').strip().lower()
    service = (service or '').strip().lower()
    entities, pins = _validate_allowance(
        domain, service, entities, rate_per_hour=rate_per_hour,
        window=window, expires_at=expires_at, risk_ack=risk_ack)
    ha_id, ha_fp = _ha_pin()
    now = _now()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        _recheck_conn(conn, [{
            'domain': domain, 'service': service, 'pins': pins,
            'label_id': source_label_id or '',
        }])
        aid = _insert_allowance(
            conn, agent_id, name, domain, service, entities, pins,
            rate_per_hour=rate_per_hour, window=window,
            expires_at=expires_at, actor=actor, now=now,
            profile_id=profile_id, profile_version=profile_version,
            source_label_id=source_label_id, ha_integration_id=ha_id,
            ha_base_url_fp=ha_fp)
        _bump_config(conn, agent_id)
        conn.commit()
    record_audit_event('allowance_created',
                       details='allowance #%d by %s: %s %s.%s on %d entities%s'
                               % (aid, actor, name, domain, service, len(entities),
                                  ' (risky confirmed)' if risk_ack else ''))
    return get_allowance(aid)


def _check_window(window):
    import re as _re
    m = _re.fullmatch(r'([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)', window or '')
    if not m or (m.group(1), m.group(2)) == (m.group(3), m.group(4)):
        raise AllowanceError(400, 'The window must be HH:MM-HH:MM and not empty.')


def _check_expiry(expires_at):
    """A Unix second, 0/None for never; never a negative or non-integer."""
    if expires_at in (None, ''):
        return 0
    try:
        value = int(expires_at)
    except (TypeError, ValueError):
        raise AllowanceError(400, 'expires_at must be a Unix timestamp or 0.')
    if value < 0:
        raise AllowanceError(400, 'expires_at must be a Unix timestamp or 0.')
    return value


def _in_window(window, now_ts=None):
    if not window:
        return True
    from datetime import datetime
    lt = datetime.fromtimestamp(now_ts or _now()).astimezone()
    minutes = lt.hour * 60 + lt.minute
    a, b = window.split('-')
    start = int(a[:2]) * 60 + int(a[3:])
    end = int(b[:2]) * 60 + int(b[3:])
    if start < end:
        return start <= minutes < end
    return minutes >= start or minutes < end


def get_allowance(aid):
    from db.core import db_conn
    with db_conn() as conn:
        row = conn.execute('SELECT * FROM allowances WHERE id = ?', (aid,)).fetchone()
    return dict(row) if row else None


def list_allowances(include_revoked=False):
    from db.core import db_conn
    _init_allowance_tables()
    with db_conn() as conn:
        sql = 'SELECT * FROM allowances'
        if not include_revoked:
            sql += ' WHERE revoked_at = 0'
        rows = conn.execute(sql + ' ORDER BY id DESC').fetchall()
    return [dict(r) for r in rows]


def _stored_entities(row):
    """The pins are the entity list matching reads (BX1).

    `entities_json` is the one source of truth. `conditions_json` carries the
    same list only so the page and older readers can show it; refresh and
    accept-join rewrite both in one UPDATE, and a row whose pins are empty
    matches nothing.
    """
    import json as _json
    try:
        pins = _json.loads(row['entities_json'] or '[]')
    except ValueError:
        return set()
    if not isinstance(pins, list):
        return set()
    return {p.get('entity_id') for p in pins
            if isinstance(p, dict) and p.get('entity_id')}


def _matches(row, domain, service, entities):
    import json as _json
    try:
        cond = _json.loads(row['conditions_json'] or '{}')
    except ValueError:
        return False
    if cond.get('domain', {}).get('in') != [domain]:
        return False
    if cond.get('service', {}).get('in') != [service]:
        return False
    allowed = _stored_entities(row)
    return bool(entities) and set(entities) <= allowed


def _parse_call(args):
    """Only plain entity ids; any expanding target gets a card."""
    domain = str((args or {}).get('domain') or '').strip().lower()
    service = str((args or {}).get('service') or '').strip().lower()
    if not _SERVICE_RE.fullmatch(domain) or not _SERVICE_RE.fullmatch(service):
        return None
    data = (args or {}).get('data')
    target = (args or {}).get('target')
    if target not in (None, {}, ''):
        return None
    if data in (None, ''):
        entities = []
    elif not isinstance(data, dict):
        return None
    else:
        for key in data:
            if key != 'entity_id':
                return None
        raw = data.get('entity_id')
        if isinstance(raw, str):
            entities = [raw.strip().lower()]
        elif isinstance(raw, list) and raw:
            entities = [str(x).strip().lower() for x in raw]
        else:
            return None
    if not entities or 'all' in entities or 'none' in entities:
        return None
    if not all(_ENTITY_RE.fullmatch(e) for e in entities):
        return None
    return domain, service, entities


def match_and_claim(agent, integration, tool, args, call_ref=''):
    """Return (id, name) when one live allowance matches; count the use.

    The rate check and the insert are one BEGIN IMMEDIATE transaction. Any
    failure (no match, ambiguous, over rate, window, expired, suspended) gives
    None, which the caller turns into the ordinary card.
    """
    from db.core import db_conn, get_db
    from db.audit import record_audit_event
    # `integration` is the integration row the call runs against. Only the
    # one enabled HA integration can match; its name is irrelevant.
    if not _is_the_ha_integration(integration) or tool != ALLOWANCE_TOOL:
        return None
    agent_id = (agent or {}).get('id')
    if not agent_id:
        return None
    parsed = _parse_call(args)
    if not parsed:
        return None
    domain, service, entities = parsed
    if not ceiling_allows(INTEGRATION, domain, service):
        return None
    now = _now()
    rows = [r for r in list_allowances() if r['agent_id'] == int(agent_id)
            and r['integration'] == INTEGRATION and r['tool'] == ALLOWANCE_TOOL]
    hits = [r for r in rows if _matches(r, domain, service, entities)]
    if len(hits) > 1:
        record_audit_event('allowance_ambiguous',
                           details='%d allowances match %s.%s; card shown'
                                   % (len(hits), domain, service))
        return None
    if not hits:
        return None
    row = hits[0]
    # S4: a permission pinned to another HA (or another base URL) never runs,
    # even before the periodic refresh has suspended it.
    if _pin_mismatch(row):
        record_audit_event(
            'allowance_ha_changed',
            details='allowance #%d not run: the HA integration or base URL '
                    'changed' % row['id'])
        return None
    if row['suspended_at']:
        return None
    if row['expires_at'] and row['expires_at'] <= now:
        return None
    if not _in_window(row['window'], now):
        return None
    # S3/CB5: refuse on the registry ids already stored in the permission's
    # pins, so a renamed protected entity needs no HA read. Only audit when a
    # hit would otherwise have run; a stray protected id in a non-matching
    # call is not an event.
    import json as _json
    try:
        hit_pins = _json.loads(row['entities_json'] or '[]')
    except (TypeError, ValueError):
        hit_pins = []
    protected = _protected_hits(pins=hit_pins)
    if protected:
        record_audit_event(
            'allowance_protected_refused',
            details='allowance #%d refused: protected %s'
                    % (row['id'], ','.join(r['entity_id'] for r in protected)))
        return None
    conn = get_db()
    try:
        conn.execute('BEGIN IMMEDIATE')
        live = conn.execute('SELECT * FROM allowances WHERE id = ? AND revoked_at = 0 '
                            'AND suspended_at = 0', (row['id'],)).fetchone()
        if not live:
            conn.rollback()
            return None
        if live['rate_per_hour']:
            used = conn.execute('SELECT COUNT(*) AS n FROM allowance_uses '
                                'WHERE allowance_id = ? AND used_at >= ?',
                                (row['id'], now - 3600)).fetchone()['n']
            if used >= live['rate_per_hour']:
                conn.rollback()
                return None
        conn.execute('INSERT INTO allowance_uses (allowance_id, used_at, call_ref) '
                     'VALUES (?, ?, ?)', (row['id'], now, call_ref or ''))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        conn.close()
    record_audit_event('allowance_used',
                       details='allowance #%d ran %s.%s on %d entities'
                               % (row['id'], domain, service, len(entities)))
    return row['id'], row['name']


def revoke_allowance(actor, aid):
    from db.core import db_conn
    from db.audit import record_audit_event
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT agent_id, revoked_at FROM allowances '
                           'WHERE id = ?', (aid,)).fetchone()
        if row and not row['revoked_at']:
            conn.execute('UPDATE allowances SET revoked_at = ? WHERE id = ? '
                         'AND revoked_at = 0', (_now(), aid))
            _bump_config(conn, row['agent_id'])
        conn.commit()
    record_audit_event('allowance_revoked', details='allowance #%d revoked by %s' % (aid, actor))


def suspend_allowance(actor, aid, reason):
    from db.core import db_conn
    from db.audit import record_audit_event
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT agent_id, suspended_at FROM allowances '
                           'WHERE id = ?', (aid,)).fetchone()
        if row and not row['suspended_at']:
            conn.execute('UPDATE allowances SET suspended_at = ?, '
                         'suspended_reason = ? WHERE id = ?',
                         (_now(), reason, aid))
            _bump_config(conn, row['agent_id'])
        conn.commit()
    record_audit_event('allowance_suspended',
                       details='allowance #%d suspended by %s: %s' % (aid, actor, reason))


def resume_allowance(actor, aid):
    from db.core import db_conn
    from db.audit import record_audit_event
    row = get_allowance(aid)
    if not row:
        raise AllowanceError(404, 'No such allowance.')
    import json as _json
    cond = _json.loads(row['conditions_json'] or '{}')
    domain = (cond.get('domain', {}).get('in') or [''])[0]
    service = (cond.get('service', {}).get('in') or [''])[0]
    if not ceiling_allows(INTEGRATION, domain, service):
        raise AllowanceError(400, 'The ceiling no longer allows this allowance.')
    import json as _json2
    pins = _json2.loads(row['entities_json'] or '[]')
    registry = _ha_registry()
    for pin in pins:
        reg = registry.get(pin.get('entity_id'))
        if not reg or (pin.get('registry_id') and reg.get('id') and
                       reg.get('id') != pin.get('registry_id')):
            raise AllowanceError(400, 'The entity %s changed; re-confirm the '
                                 'allowance explicitly.' % pin.get('entity_id'))
    protected = _protected_hits(pins=pins)
    if protected:
        first = protected[0]['entity_id']
        raise AllowanceError(
            400, 'Protected: %s is still protected. Unprotect it before '
                 'resuming this permission.' % first,
            {'code': 'protected_device',
             'entities': [r['entity_id'] for r in protected]})
    ha_id, ha_fp = _ha_pin()
    with db_conn() as conn:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute('SELECT agent_id, suspended_at FROM allowances '
                               'WHERE id = ?', (aid,)).fetchone()
        if not current:
            conn.rollback()
            raise AllowanceError(404, 'No such allowance.')
        if not _ceiling_allows_conn(conn, domain, service):
            raise AllowanceError(
                400, 'The ceiling no longer allows this allowance.',
                {'code': 'ceiling_changed'})
        hits = _protected_hits(pins=pins, conn=conn)
        if hits:
            raise AllowanceError(
                400, 'Protected: %s is still protected. Unprotect it before '
                     'resuming this permission.' % hits[0]['entity_id'],
                {'code': 'protected_device',
                 'entities': [r['entity_id'] for r in hits]})
        conn.execute('UPDATE allowances SET suspended_at = 0, suspended_reason = \'\' '
                     'WHERE id = ?', (aid,))
        # S4: an explicit Resume is the operator re-confirming the current HA.
        if ha_id:
            _write_ha_pin(conn, aid, ha_id, ha_fp)
        if current['suspended_at']:
            _bump_config(conn, current['agent_id'])
        conn.commit()
    record_audit_event('allowance_resumed', details='allowance #%d resumed by %s' % (aid, actor))


# ── Profiles (a named set of allowance templates) ──────────────────────────

def _resolve_label(label_id, domain, registry=None):
    """Resolve a label to entity ids at apply time (never at match)."""
    registry = registry if registry is not None else _ha_registry()
    out = []
    for ent, reg in registry.items():
        labels = reg.get('labels') or []
        if label_id in labels and ent.startswith(domain + '.'):
            out.append(ent)
    return sorted(out)


def create_profile(actor, name, description='', templates=None,
                   version=None, profile_id=None):
    from db.core import db_conn
    from db.audit import record_audit_event
    import json as _json
    _init_allowance_tables()
    templates = templates or []
    if not templates:
        raise AllowanceError(400, 'A profile needs at least one template.')
    name = (name or '').strip()
    if not name:
        raise AllowanceError(400, 'A profile needs a name.')
    now = _now()
    with db_conn() as conn:
        if profile_id:
            row = conn.execute('SELECT * FROM allowance_profiles WHERE id = ?',
                               (profile_id,)).fetchone()
            if not row:
                raise AllowanceError(404, 'No such profile.')
            ver = int(row['version']) + 1
            conn.execute('UPDATE allowance_profiles SET name = ?, description = ?, '
                         'version = ?, updated_at = ? WHERE id = ?',
                         (name, description or '', ver, now, profile_id))
            pid = profile_id
        else:
            ver = 1
            cur = conn.execute('INSERT INTO allowance_profiles (name, description, '
                               'version, created_by, created_at, updated_at) '
                               'VALUES (?,?,?,?,?,?)',
                               (name, description or '', ver, actor, now, now))
            pid = cur.lastrowid
        for t in templates:
            _domain = (t.get('domain') or '').strip().lower()
            _service = (t.get('service') or '').strip().lower()
            if catalogue_row(INTEGRATION, f'{_domain}.{_service}') is None:
                raise AllowanceError(400, 'Not in the catalogue: %s.%s' %
                                     (_domain, _service))
            if not ceiling_allows(INTEGRATION, _domain, _service):
                raise AllowanceError(400, 'The ceiling does not allow %s.%s.' %
                                     (_domain, _service))
            if not t.get('label_id'):
                validate_entity_set(_domain, t.get('entities') or [])
            conn.execute('INSERT INTO allowance_profile_templates '
                         '(profile_id, profile_version, integration, tool, name, '
                         'domain, service, entities_json, label_id, rate_per_hour, '
                         'window, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                         (pid, ver, INTEGRATION, ALLOWANCE_TOOL, t.get('name', ''),
                          t.get('domain', ''), t.get('service', ''),
                          _json.dumps(t.get('entities') or []),
                          t.get('label_id', ''), int(t.get('rate_per_hour') or 0),
                          t.get('window', ''), int(t.get('expires_at') or 0)))
        conn.commit()
    record_audit_event('allowance_profile_saved',
                       details='profile #%d %r version %d by %s' % (pid, name, ver, actor))
    return get_profile(pid)


def get_profile(pid):
    from db.core import db_conn
    import json as _json
    with db_conn() as conn:
        p = conn.execute('SELECT * FROM allowance_profiles WHERE id = ?', (pid,)).fetchone()
        if not p:
            return None
        tpls = conn.execute('SELECT * FROM allowance_profile_templates '
                            'WHERE profile_id = ? AND profile_version = ? ORDER BY id',
                            (pid, p['version'])).fetchall()
    out = dict(p)
    out['templates'] = []
    for t in tpls:
        d = dict(t)
        d['entities'] = _json.loads(d.pop('entities_json') or '[]')
        out['templates'].append(d)
    return out


def list_profiles():
    from db.core import db_conn
    _init_allowance_tables()
    with db_conn() as conn:
        rows = conn.execute('SELECT id FROM allowance_profiles WHERE deleted_at = 0 '
                            'ORDER BY id DESC').fetchall()
    return [get_profile(r['id']) for r in rows]


def apply_profile(actor, pid, agent_id, risk_ack=False, profile_version=0):
    """Validate every template, then revoke and insert in one transaction.

    A stale `profile_version` is a 409 before any HA read. The transaction
    re-checks the version, so two applies cannot both pass the compare. If a
    template fails any check, nothing is revoked (BX4).
    """
    from db.core import db_conn
    from db.audit import record_audit_event
    import json as _json
    _init_allowance_tables()
    ensure_initialised()
    profile = get_profile(pid)
    if not profile:
        raise AllowanceError(404, 'No such profile.')
    try:
        wanted_version = int(profile_version or 0)
    except (TypeError, ValueError):
        raise AllowanceError(400, 'profile_version must be an integer.')
    if wanted_version <= 0:
        raise AllowanceError(
            400, 'profile_version is required: reload the preview.',
            {'code': 'profile_version_required'})
    if wanted_version != int(profile['version']):
        raise AllowanceError(
            409, 'The profile changed since the preview; reload it.',
            {'code': 'profile_changed',
             'profile_version': int(profile['version'])})
    # One registry read for every template. A failed read refuses the apply
    # before anything is revoked.
    registry = _ha_registry()
    ha_id, ha_fp = _ha_pin()
    prepared = []
    for t in profile['templates']:
        domain = (t.get('domain') or '').strip().lower()
        service = (t.get('service') or '').strip().lower()
        entities = list(t.get('entities') or [])
        if t.get('label_id'):
            entities = _resolve_label(t['label_id'], domain, registry)
            if not entities:
                raise AllowanceError(400, 'Label %r has no %s entities.' %
                                     (t['label_id'], domain))
        entities, pins = _validate_allowance(
            domain, service, entities,
            rate_per_hour=t.get('rate_per_hour') or 0,
            window=t.get('window') or '',
            expires_at=t.get('expires_at') or 0, risk_ack=risk_ack,
            registry=registry, source_label=t.get('label_id') or '')
        prepared.append((t, domain, service, entities, pins))
    # "Update agents": a re-apply replaces this agent's allowances from the
    # same profile. The revoke and every insert share one BEGIN IMMEDIATE, so
    # a failure leaves the agent's old rows exactly as they were.
    now = _now()
    created_ids = []
    revoked_ids = []
    with db_conn() as conn:
        try:
            conn.execute('BEGIN IMMEDIATE')
            current = conn.execute(
                'SELECT version FROM allowance_profiles WHERE id = ?',
                (pid,)).fetchone()
            if not current or int(current['version']) != wanted_version:
                conn.rollback()
                live = get_profile(pid)
                raise AllowanceError(
                    409, 'The profile changed since the preview; reload it.',
                    {'code': 'profile_changed',
                     'profile_version': int((live or profile)['version'])})
            _recheck_conn(conn, [{
                'domain': domain, 'service': service, 'pins': pins,
                'label_id': t.get('label_id') or '',
            } for t, domain, service, _entities, pins in prepared])
            old_rows = conn.execute(
                'SELECT id FROM allowances WHERE agent_id = ? AND profile_id = ? '
                'AND revoked_at = 0', (int(agent_id), pid)).fetchall()
            for old_row in old_rows:
                conn.execute(
                    'UPDATE allowances SET revoked_at = ? WHERE id = ? '
                    'AND revoked_at = 0', (now, old_row['id']))
                revoked_ids.append(old_row['id'])
            for t, domain, service, entities, pins in prepared:
                created_ids.append(_insert_allowance(
                    conn, agent_id, t.get('name') or profile['name'], domain,
                    service, entities, pins,
                    rate_per_hour=t.get('rate_per_hour') or 0,
                    window=t.get('window') or '',
                    expires_at=t.get('expires_at') or 0, actor=actor,
                    now=now, profile_id=pid, profile_version=wanted_version,
                    source_label_id=t.get('label_id') or '',
                    ha_integration_id=ha_id, ha_base_url_fp=ha_fp))
            _bump_config(conn, agent_id)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
    for old_id in revoked_ids:
        record_audit_event(
            'allowance_revoked',
            details='allowance #%d revoked by %s (profile update)' %
                    (old_id, actor))
    created = []
    for i, aid in enumerate(created_ids):
        _t, domain, service, entities, _pins = prepared[i]
        created.append(get_allowance(aid))
        record_audit_event(
            'allowance_created',
            details='allowance #%d by %s: %s %s.%s on %d entities '
                    '(profile #%d v%d)%s'
                    % (aid, actor, prepared[i][0].get('name') or profile['name'],
                       domain, service, len(entities), pid, wanted_version,
                       ' (risky confirmed)' if risk_ack else ''))
    record_audit_event(
        'allowance_profile_applied',
        details='profile #%d %r version %d applied to agent #%s by %s: '
                'revoked %s, created %s'
                % (pid, profile['name'], wanted_version, agent_id, actor,
                   ','.join(map(str, revoked_ids)) or 'none',
                   ','.join(map(str, created_ids)) or 'none'))
    return {'profile_id': pid, 'version': wanted_version, 'created': created}


# ── AZ2: an approved config write suspends what it can redefine ────────────

_ENTITY_SCAN = re.compile(r'\b([a-z_]+)\.([a-z0-9_]+)\b')
# BX3: the modern `template:` form puts the domain key behind a list dash and
# derives the entity id from `name`/`unique_id`, so it never appears as a
# plain `domain.entity` token. Both `template:` and `- switch:` are unmapped.
_YAML_DOMAIN_BLOCK = re.compile(
    r'^\s*(?:-\s*)?(light|switch|cover|climate|fan|media_player|'
    r'input_boolean|template)\s*:',
    re.MULTILINE)


def _live_ha_allowances():
    return [r for r in list_allowances() if r['integration'] == INTEGRATION]


def _suspend_all(actor, reason, detail):
    from db.audit import record_audit_event
    count = 0
    for row in _live_ha_allowances():
        if not row['suspended_at']:
            suspend_allowance(actor, row['id'], reason)
            count += 1
    if count:
        record_audit_event('allowance_config_write_suspended',
                           details='%s: %d allowance(s)' % (detail, count))
    return count


def on_config_write(actor, path, content):
    """AZ2: suspend allowances an approved HA config write could redefine.

    A file that includes others, or defines entities without a plain entity
    id, cannot be mapped: suspend every HA allowance (the conservative branch
    security accepted). Otherwise suspend the allowances whose stored entity
    ids appear in the text. Suspension, never deletion.
    """
    from db.audit import record_audit_event
    text = content or ''
    if ('!include' in text or 'packages:' in text
            or _YAML_DOMAIN_BLOCK.search(text)):
        n = _suspend_all(actor, 'config_write_unmapped',
                         'config write %s' % path)
        record_audit_event('allowance_config_write_scanned',
                           details='%s: mapping incomplete, suspended all (%d)' %
                                   (path, n))
        return {'suspended': n, 'reason': 'config_write_unmapped'}
    found = set(m.group(0) for m in _ENTITY_SCAN.finditer(text))
    count = 0
    for row in _live_ha_allowances():
        if row['suspended_at']:
            continue
        import json as _json
        pins = _json.loads(row['entities_json'] or '[]')
        ids = {p.get('entity_id') for p in pins}
        if ids & found:
            suspend_allowance(actor, row['id'], 'config_write')
            count += 1
    record_audit_event('allowance_config_write_scanned',
                       details='%s: %d entity id(s), suspended %d allowance(s)'
                               % (path, len(found), count))
    return {'suspended': count, 'entities': sorted(found)}


def on_config_restore(actor, path):
    """AZ2: a restore replaces an unseen file, so treat it as unmapped.

    The handler cannot see the restored content, so every live HA allowance is
    suspended with `config_write_unmapped` and the operator re-confirms.
    """
    from db.audit import record_audit_event
    n = _suspend_all(actor, 'config_write_unmapped',
                     'config restore %s' % path)
    record_audit_event(
        'allowance_config_write_scanned',
        details='%s: restore content unmapped, suspended all (%d)' % (path, n))
    return {'suspended': n, 'reason': 'config_write_unmapped'}


def on_config_write_failed(actor, path, exc):
    """AZ2 fail-closed path: a scan that raised suspends every HA allowance.

    If the suspension itself fails, one `allowance_config_write_failed` row is
    written instead (and never a traceback in the tool result).
    """
    from db.audit import record_audit_event
    why = str(exc)[:200] or exc.__class__.__name__
    try:
        n = _suspend_all(actor, 'config_write_unmapped',
                         'config write %s (scan failed: %s)' % (path, why))
        record_audit_event(
            'allowance_config_write_scanned',
            details='%s: scan failed (%s), suspended all (%d)' % (path, why, n))
        return {'suspended': n, 'reason': 'config_write_unmapped',
                'error': why}
    except Exception:
        try:
            record_audit_event(
                'allowance_config_write_failed',
                details='%s: scan failed (%s) and the fail-closed suspension '
                        'also failed' % (path, why))
        except Exception:
            pass
        return {'suspended': 0, 'reason': 'config_write_unmapped',
                'error': why}


# ── The label and registry refresh (accept-to-join) ───────────────────────

def _resolve_label_pins(label_id, domain, registry=None):
    registry = registry if registry is not None else _ha_registry()
    out = []
    for ent, reg in registry.items():
        if label_id in (reg.get('labels') or []) and ent.startswith(domain + '.'):
            out.append({'entity_id': ent, 'registry_id': reg.get('id') or ''})
    return sorted(out, key=lambda p: p['entity_id'])


def _pin_changed(registry, pin):
    """A pin is bad when its entity is gone or its registry id moved."""
    ent = (pin or {}).get('entity_id')
    reg = registry.get(ent)
    if not reg:
        return True
    old = (pin or {}).get('registry_id')
    new = reg.get('id')
    return bool(old and new and old != new)


def _write_entity_list(conn, row, pins, pending):
    """Rewrite the pins and the duplicate conditions list in one UPDATE.

    Matching reads the pins; the conditions copy keeps the page and any older
    reader consistent. BX1: both columns move together or neither does.
    """
    import json as _json
    cond = _json.loads(row['conditions_json'] or '{}')
    cond = dict(cond)
    cond['data.entity_id'] = {'in': [p['entity_id'] for p in pins]}
    conn.execute(
        'UPDATE allowances SET conditions_json = ?, entities_json = ?, '
        'pending_json = ? WHERE id = ?',
        (_json.dumps(cond, sort_keys=True), _json.dumps(pins),
         _json.dumps(pending), row['id']))


def _backfill_protected_registry(registry):
    """Fill registry ids captured while HA was unreachable, and audit each.

    Only an empty id is filled, and only when HA now knows the entity. In the
    same transaction as the fill, every live permission that holds that
    registry id under a different entity id is suspended as `protected`:
    the rename may have happened during the outage, and the new name must
    not inherit the old grant.
    """
    from db.core import db_conn
    from db.audit import record_audit_event
    import json as _json
    filled = []
    suspended = []
    for row in protected_records():
        if row.get('registry_id'):
            continue
        entity_id = row['entity_id']
        reg = registry.get(entity_id)
        rid = (reg or {}).get('id') or ''
        if not rid:
            continue
        with db_conn() as conn:
            try:
                conn.execute('BEGIN IMMEDIATE')
                conn.execute(
                    'UPDATE allowance_protected SET registry_id = ? '
                    'WHERE entity_id = ? AND registry_id = \'\'',
                    (rid, entity_id))
                for live in conn.execute(
                        'SELECT id, agent_id, entities_json FROM allowances '
                        'WHERE revoked_at = 0 AND suspended_at = 0').fetchall():
                    try:
                        pins = _json.loads(live['entities_json'] or '[]')
                    except (TypeError, ValueError):
                        pins = []
                    if not any(isinstance(p, dict)
                               and p.get('registry_id') == rid
                               and str(p.get('entity_id') or '').lower()
                               != entity_id
                               for p in pins):
                        continue
                    conn.execute(
                        'UPDATE allowances SET suspended_at = ?, '
                        'suspended_reason = ? WHERE id = ?',
                        (_now(), 'protected', live['id']))
                    _bump_config(conn, live['agent_id'])
                    suspended.append((live['id'], live['agent_id'],
                                      entity_id))
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
        filled.append((entity_id, rid))
    for entity_id, rid in filled:
        record_audit_event(
            'allowance_protected_registry_filled',
            details='%s registry id filled as %s after a successful refresh'
                    % (entity_id, rid))
    for aid, agent_id, entity_id in suspended:
        record_audit_event(
            'allowance_suspended',
            details='allowance #%d suspended by refresh: protected '
                    '(registry id filled for %s; agent #%d)'
                    % (aid, entity_id, int(agent_id)))
    return filled


def refresh_pins(actor='config-write'):
    """Re-read every live HA allowance's registry pins (BD5/BX2).

    Runs on the periodic refresh and straight after an approved registry or
    config-entry write. A missing or changed registry id suspends the
    allowance with `registry_changed`; it never deletes it. A changed HA
    integration or base URL suspends with `ha_changed` (S4).
    """
    from db.audit import record_audit_event
    import json as _json
    try:
        registry = _ha_registry()
    except AllowanceError as exc:
        return {'checked': 0, 'error': str(exc)}
    _backfill_protected_registry(registry)
    pin = _ha_pin()
    suspended = []
    for row in _live_ha_allowances():
        if row['suspended_at']:
            continue
        if _pin_mismatch(row, pin):
            suspend_allowance(actor, row['id'], 'ha_changed')
            suspended.append(row['id'])
            continue
        pins = _json.loads(row['entities_json'] or '[]')
        if any(_pin_changed(registry, p) for p in pins):
            suspend_allowance(actor, row['id'], 'registry_changed')
            suspended.append(row['id'])
    if suspended:
        record_audit_event(
            'allowance_registry_refresh',
            details='registry or HA pin changed: suspended %s'
                    % ','.join(map(str, suspended)))
    return {'checked': len(suspended), 'suspended': suspended, 'error': ''}


def refresh_labels(actor='refresh'):
    """Re-resolve every live HA allowance's pins and labels (BD5/BX2).

    * every row, label-sourced or hand-made, has its HA integration pin
      checked; a changed integration or base URL suspends it (`ha_changed`);
    * every row's registry pins are re-read; a missing or changed id suspends
      it (`registry_changed`);
    * a label entity that **left** drops out of the pins at once;
    * a label entity that **joined** waits in `pending_json` until accepted;
    * a protected entity never enters, including through a label (S3).
    A failed registry read changes nothing.
    """
    from db.core import db_conn
    from db.audit import record_audit_event
    import json as _json
    try:
        registry = _ha_registry()
    except AllowanceError as exc:
        try:
            from db.policies import set_meta
            set_meta('allowance_refresh_error', str(exc))
        except Exception:
            pass
        return {'checked': 0, 'error': str(exc)}
    _backfill_protected_registry(registry)
    pin = _ha_pin()
    protected = protected_records()
    prot_entity = {r['entity_id']: r for r in protected}
    prot_reg = {r['registry_id']: r for r in protected if r.get('registry_id')}
    changed = 0
    for row in _live_ha_allowances():
        if row['suspended_at']:
            continue
        if _pin_mismatch(row, pin):
            suspend_allowance(actor, row['id'], 'ha_changed')
            record_audit_event(
                'allowance_ha_changed',
                details='allowance #%d suspended: the HA integration or base '
                        'URL changed' % row['id'])
            changed += 1
            continue
        label = row.get('source_label_id') or ''
        cond = _json.loads(row['conditions_json'] or '{}')
        domain = (cond.get('domain', {}).get('in') or [''])[0]
        stored_list = _json.loads(row['entities_json'] or '[]')
        stored = {p['entity_id']: p for p in stored_list}
        pin_needed = bool(pin[0] and not row.get('ha_integration_id')
                          and not row.get('ha_base_url_fp'))
        if not label:
            # BX2: a hand-made allowance is pinned too. Entity-id reuse is the
            # exact case: delete a plug, rename a new one to the same id.
            if any(_pin_changed(registry, p) for p in stored.values()):
                suspend_allowance(actor, row['id'], 'registry_changed')
                changed += 1
                continue
            # Security answer (4): pin only after the registry check passed.
            if pin_needed:
                with db_conn() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    _write_ha_pin(conn, row['id'], pin[0], pin[1])
                    _bump_config(conn, row['agent_id'])
                    conn.commit()
                changed += 1
            continue
        current_all = {p['entity_id']: p
                       for p in _resolve_label_pins(label, domain, registry)}
        # S3/CB5: protected devices are dropped before they can join, by
        # entity id or by the registry id captured when protection was set.
        def _protected_pin(p):
            return bool(p.get('entity_id') in prot_entity
                        or (p.get('registry_id') and p.get('registry_id') in prot_reg))
        skipped = sorted(e for e, p in current_all.items()
                         if _protected_pin(p) and e not in stored)
        current = {e: p for e, p in current_all.items()
                   if not _protected_pin(p)}
        left = set(stored) - set(current)
        joined = set(current) - set(stored)
        bad = [e for e, p in stored.items()
               if e in current and _pin_changed(registry, p)]
        if bad:
            suspend_allowance(actor, row['id'], 'registry_changed')
            changed += 1
            continue
        if skipped:
            record_audit_event(
                'allowance_protected_label_skipped',
                details='allowance #%d: label %s offered protected %s; dropped'
                        % (row['id'], label, ','.join(skipped)))
        if not left and not joined and not pin_needed:
            continue
        pins = [stored[e] for e in stored if e not in left]
        pending = [p for p in _json.loads(row.get('pending_json') or '[]')
                   if p.get('entity_id') not in left]
        have = {p['entity_id'] for p in pending}
        for e in sorted(joined):
            if e not in have:
                pending.append(current[e])
        with db_conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if pin_needed:
                _write_ha_pin(conn, row['id'], pin[0], pin[1])
            if left or joined:
                _write_entity_list(conn, row, pins, pending)
            _bump_config(conn, row['agent_id'])
            conn.commit()
        if left or joined:
            record_audit_event(
                'allowance_label_refresh',
                details='allowance #%d: dropped %s; held %s; protected-skipped %s'
                        % (row['id'], ','.join(sorted(left)) or 'none',
                           ','.join(sorted(joined)) or 'none',
                           ','.join(skipped) or 'none'))
        changed += 1
    return {'checked': changed, 'error': ''}


def accept_join(actor, aid, risk_ack=False):
    """Accept the entities a label gained, after the save path's checks.

    BX5: joiners go through the ceiling, the 64-entity cap and the same
    second risk confirmation as a new allowance, before anything is pinned.
    """
    from db.core import db_conn
    from db.audit import record_audit_event
    import json as _json
    row = get_allowance(aid)
    if not row or row.get('revoked_at'):
        raise AllowanceError(404, 'No such allowance.')
    pending = _json.loads(row.get('pending_json') or '[]')
    if not pending:
        raise AllowanceError(400, 'Nothing to accept.')
    cond = _json.loads(row['conditions_json'] or '{}')
    domain = (cond.get('domain', {}).get('in') or [''])[0]
    service = (cond.get('service', {}).get('in') or [''])[0]
    if not ceiling_allows(INTEGRATION, domain, service):
        raise AllowanceError(400, 'The ceiling no longer allows %s.%s.' %
                             (domain, service))
    registry = _ha_registry()
    now_ok = []
    for p in pending:
        reg = registry.get(p.get('entity_id'))
        if reg and (not p.get('registry_id') or reg.get('id') == p.get('registry_id')):
            now_ok.append({'entity_id': p['entity_id'],
                           'registry_id': reg.get('id') or ''})
    if not now_ok:
        raise AllowanceError(400, 'The entities changed again; refresh first.')
    protected = _protected_hits(pins=now_ok)
    if protected:
        first = protected[0]['entity_id']
        raise AllowanceError(
            400, 'Protected: %s is in this label. Unprotect it, or pick '
                 'devices instead of the label.' % first,
            {'code': 'protected_device',
             'entities': [r['entity_id'] for r in protected]})
    pins = _json.loads(row['entities_json'] or '[]') + now_ok
    if len({p['entity_id'] for p in pins}) > 64:
        raise AllowanceError(400, 'At most 64 entities per allowance.')
    accepted = [p['entity_id'] for p in now_ok]
    risky_row, risky_entities = _risk_for(domain, accepted)
    if (risky_row or risky_entities) and not risk_ack:
        raise AllowanceError(
            400, 'Accepting these entities needs a second risk confirmation.',
            {'code': 'risk_ack_required', 'risky_row': risky_row,
             'risky_entities': risky_entities})
    rest = [p for p in pending if p.get('entity_id') not in set(accepted)]
    with db_conn() as conn:
        try:
            conn.execute('BEGIN IMMEDIATE')
            live = conn.execute(
                'SELECT * FROM allowances WHERE id = ? AND revoked_at = 0',
                (aid,)).fetchone()
            if not live:
                conn.rollback()
                raise AllowanceError(404, 'No such allowance.')
            if not _ceiling_allows_conn(conn, domain, service):
                raise AllowanceError(
                    400, 'The ceiling no longer allows %s.%s; reload and retry.'
                         % (domain, service), {'code': 'ceiling_changed'})
            hits = _protected_hits(pins=pins, conn=conn)
            if hits:
                raise AllowanceError(
                    400, 'Protected: %s is in this label. Unprotect it, or '
                         'pick devices instead of the label.'
                         % hits[0]['entity_id'],
                    {'code': 'protected_device',
                     'entities': [r['entity_id'] for r in hits]})
            _write_entity_list(conn, dict(live), pins, rest)
            _bump_config(conn, live['agent_id'])
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
    record_audit_event('allowance_joined_accepted',
                       details='allowance #%d accepted %s' %
                               (aid, ','.join(accepted)))
    return {'accepted': accepted}


def profile_diff(pid, agent_id):
    """What a profile re-apply would revoke and add, without changing anything."""
    profile = get_profile(pid)
    if not profile:
        raise AllowanceError(404, 'No such profile.')
    existing = [r for r in list_allowances()
                if r['agent_id'] == int(agent_id) and r['profile_id'] == pid]
    add = []
    for t in profile['templates']:
        domain = (t.get('domain') or '').strip().lower()
        service = (t.get('service') or '').strip().lower()
        entities = list(t.get('entities') or [])
        if t.get('label_id'):
            try:
                entities = _resolve_label(t['label_id'], domain)
            except AllowanceError:
                raise
        add.append({'name': t.get('name') or profile['name'],
                    'domain': domain, 'service': service,
                    'entities': entities, 'label_id': t.get('label_id') or ''})
    up_to_date = bool(existing) and all(
        r['version'] == profile['version'] for r in existing) and \
        len(existing) == len(add)
    return {'profile_id': pid, 'profile_version': profile['version'],
            'agent_id': int(agent_id),
            'revoke': [{'id': r['id'], 'name': r['name'],
                        'domain': (__import__('json').loads(r['conditions_json'])['domain']['in'][0]),
                        'service': (__import__('json').loads(r['conditions_json'])['service']['in'][0]),
                        'version': r['version']} for r in existing],
            'add': add, 'up_to_date': up_to_date}


def agent_allowance_line(agent_id):
    """Answer 8: one generic line, never the operator's ceiling list."""
    if not agent_id:
        return ''
    live = [r for r in list_allowances() if r['agent_id'] == int(agent_id)]
    if not live:
        return ''
    return ('Calls matching one of your allowances run without approval. '
            'The window, rate and expiry still apply.')

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

    An operator word is never exempt, even when it spells the same word: the
    catalogue exemption names a built-in (`stop`), not a boolean.
    """
    words = set(destructive_words())
    operator = set(operator_words())
    allowed = set(exempt or ()) - operator
    for token in _tokens(service):
        if token in words and token not in allowed:
            return True
    return False


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


def set_hash(integration=INTEGRATION):
    """A stable hash of the enabled set, for the stale-form check (BV2)."""
    h = hashlib.sha256()
    for cid in enabled_ids(integration):
        h.update(cid.encode() + b'\0')
    return h.hexdigest()[:16]


def ceiling_allows(integration, domain, service):
    """The one check used at save, apply and match."""
    ensure_initialised()
    if integration != INTEGRATION:
        return False
    catalogue_id = f'{domain}.{service}'
    row = catalogue_row(integration, catalogue_id)
    if row is None:
        return False
    if never_reason(domain, service):
        return False
    if is_destructive(service, row['exempt_words']):
        return False
    info = _enabled_map(integration).get(catalogue_id)
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


def apply_ceiling(actor, integration, wanted, risk_ack=False,
                  expected_hash=''):
    """Replace the enabled set; validate, audit and notify a widening."""
    from db.core import db_conn
    from db.audit import record_audit_event
    from core.notify import send_notify
    ensure_initialised()
    if integration != INTEGRATION:
        raise CeilingError(400, 'This integration has no ceiling yet.')
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

    before = set(enabled_ids(integration))
    if expected_hash and expected_hash != set_hash(integration):
        raise CeilingError(409, 'The ceiling changed since this form was loaded.',
                           {'code': 'ceiling_changed',
                            'set_hash': set_hash(integration)})
    after = set(wanted)
    enabling = sorted(after - before)
    disabling = sorted(before - after)
    risky = [cid for cid in enabling if is_risky(cid)]
    if risky and not risk_ack:
        raise CeilingError(400, 'This change needs a risk confirmation.',
                           {'code': 'risk_ack_required', 'risky': risky})

    now = _now()
    with db_conn() as conn:
        for cid, _domain, _service, _label, _entry in _rows():
            conn.execute(
                'UPDATE allowance_ceiling SET enabled = ?, updated_by = ?, '
                'updated_at = ? WHERE integration = ? AND catalogue_id = ?',
                (1 if cid in after else 0, actor, now, integration, cid))
        conn.commit()
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
            if w in _tokens(row['service']) and w not in row['exempt_words']:
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

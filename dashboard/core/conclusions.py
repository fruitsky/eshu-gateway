"""F13: the MCP-facing half of session conclusions and runbooks.

`session_conclude` validates and scrubs the agent's account of a session and
stores it as a **draft**; the operator confirms, edits or rejects it in the
session modal. Only a confirmed conclusion — and a runbook promoted from one —
is ever returned by `find_runbooks`, `similar_incidents` or `get_runbook`.

Agent text is untrusted: it is scrubbed on write and again on read, never
executed, and escaped by the UI. A reference in `worked`/`did_not_work`
(`req:<id>` / `call:<id>`) must belong to the same session, because a
conclusion is only allowed to cite its own evidence.
"""
import json
import re

from core.cmd_parse import breakdown
from core.secret_scrub import scrub_string
from db.conclusions import (
    ConclusionError, create_runbook, get_conclusion, get_for_agent, get_runbook,
    search_confirmed, search_runbooks, upsert_draft,
)
from db.core import db_conn
from db.requests import get_request_row
from db.sessions import get_session

OUTCOMES = ('solved', 'partial', 'failed', 'abandoned')
MAX_TEXT = 4000
MAX_ITEMS = 50
MAX_TAGS = 20
MAX_TAG_CHARS = 40
MAX_FOLLOW_UPS = 20
MAX_STEPS = 20
MAX_INCIDENT_CHARS = 6000          # ~1,500 tokens
REF_RE = re.compile(r'^(req|call):([0-9]{1,12})$')


def _scrub(value) -> str:
    return scrub_string(str(value or ''))


def _require_text(name, value, limit=MAX_TEXT):
    text = _scrub(value)
    if len(text) > limit:
        raise ValueError('%s_too_long' % name)
    return text


def _normalise_items(session_id, raw, name):
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError('%s_not_a_list' % name)
    if len(raw) > MAX_ITEMS:
        raise ValueError('%s_too_many' % name)
    out = []
    for entry in raw:
        if isinstance(entry, str):
            out.append({'note': _require_text('note', entry, 1000)})
            continue
        if not isinstance(entry, dict):
            raise ValueError('%s_bad_item' % name)
        note = _require_text('note', entry.get('note'), 1000)
        ref = str(entry.get('ref') or '').strip()
        if ref:
            if not _ref_in_session(session_id, ref):
                raise ValueError('ref_not_in_session')
            out.append({'ref': ref, 'note': note})
        elif note:
            out.append({'note': note})
    return out


def _ref_in_session(session_id, ref) -> bool:
    match = REF_RE.match(ref)
    if not match:
        return False
    kind, number = match.group(1), int(match.group(2))
    if kind == 'req':
        row = get_request_row(number)
        return bool(row) and row.get('session_id') == session_id
    with db_conn() as conn:
        row = conn.execute(
            'SELECT session_id FROM integration_calls WHERE id = ?',
            (number,)).fetchone()
    return bool(row) and row['session_id'] == session_id


def _call_row(call_id):
    with db_conn() as conn:
        row = conn.execute(
            'SELECT * FROM integration_calls WHERE id = ?', (int(call_id),)
        ).fetchone()
    return dict(row) if row else None


def validate_payload(session_id, agent, outcome, summary, worked=None,
                     did_not_work=None, root_cause='', fix='',
                     follow_ups=None, tags=None, runbook_candidate=False):
    """Return the scrubbed payload, or raise ValueError(code)."""
    session = get_session(session_id)
    if not session:
        raise ValueError('no_session')
    owner = (session.get('agent') or '').strip()
    if owner and (agent or '') != owner:
        raise ValueError('session_not_yours')
    if (outcome or '') not in OUTCOMES:
        raise ValueError('bad_outcome')
    summary_text = _require_text('summary', summary)
    if not summary_text.strip():
        raise ValueError('summary_required')
    tag_list = []
    for tag in (tags or []):
        tag = _scrub(tag).strip()[:MAX_TAG_CHARS]
        if tag and tag not in tag_list:
            tag_list.append(tag)
    if len(tag_list) > MAX_TAGS:
        raise ValueError('too_many_tags')
    follow = [_require_text('follow_up', item, 1000) for item in (follow_ups or [])]
    if len(follow) > MAX_FOLLOW_UPS:
        raise ValueError('too_many_follow_ups')
    return {
        'outcome': outcome,
        'summary': summary_text,
        'worked': _normalise_items(session_id, worked, 'worked'),
        'did_not_work': _normalise_items(session_id, did_not_work, 'did_not_work'),
        'root_cause': _require_text('root_cause', root_cause),
        'fix': _require_text('fix', fix),
        'follow_ups': follow,
        'tags': tag_list,
        'runbook_candidate': bool(runbook_candidate),
    }


def save_conclusion(session_id, agent, **fields):
    """Validate, scrub and store the agent's draft. Returns the stored row."""
    session = get_session(session_id)
    payload = validate_payload(session_id, agent, **fields)
    return upsert_draft(session_id, agent, (session or {}).get('goal', ''),
                        payload)


def conclusion_state(session_id, agent):
    item = get_for_agent(session_id, agent)
    if not item:
        return None
    return {'id': item['id'], 'status': item['status'],
            'outcome': item['outcome'], 'runbook_candidate':
                item['runbook_candidate']}


def build_steps(session_id, conclusion):
    """Copy the cited evidence verbatim, with F1's breakdown, in order."""
    steps = []
    for item in (conclusion.get('worked') or [])[:MAX_STEPS]:
        ref = item.get('ref') or ''
        note = _scrub(item.get('note'))
        if ref.startswith('req:'):
            row = get_request_row(int(ref.split(':', 1)[1]))
            if not row:
                continue
            steps.append({
                'kind': 'command',
                'ref': ref,
                'host': row.get('hostname') or row.get('target_ip') or '',
                'command': row.get('command') or '',
                'breakdown': breakdown(row.get('command') or ''),
                'note': note,
            })
        elif ref.startswith('call:'):
            row = _call_row(int(ref.split(':', 1)[1]))
            if not row:
                continue
            steps.append({
                'kind': 'tool',
                'ref': ref,
                'integration': row.get('integration') or '',
                'tool': row.get('tool') or '',
                'method': row.get('method') or '',
                'path': row.get('path') or '',
                'note': note,
            })
        else:
            steps.append({'kind': 'instruction', 'text': note})
    dead_ends = []
    for item in (conclusion.get('did_not_work') or [])[:MAX_STEPS]:
        dead_ends.append({'ref': item.get('ref') or '',
                          'note': _scrub(item.get('note'))})
    return steps, dead_ends


def promote_conclusion(conclusion_id, title='', when_to_use='', by='',
                       operator_tags=None):
    """Turn a confirmed, runbook-candidate conclusion into a runbook."""
    item = get_conclusion(conclusion_id)
    if not item:
        raise ConclusionError('not_found', 'No conclusion with that id.')
    steps, dead_ends = build_steps(item['session_id'], item)
    title = (title or item['summary'] or '').strip()[:200]
    if not title:
        title = 'Session %s' % (item['session_id'][:12] or item['id'])
    when = (when_to_use or '').strip()[:2000]
    if not when:
        bits = [item['outcome']]
        if item['tags']:
            bits.append(', '.join(item['tags']))
        when = ('When a session ends %s.' % bits[0]) + (
            ' Tags: %s.' % bits[1] if len(bits) > 1 else '')
    tags = list(dict.fromkeys((operator_tags or []) + item['tags']))
    runbook_id = create_runbook(conclusion_id, title, when, steps, dead_ends,
                                tags=tags, by=by)
    return get_runbook(runbook_id)


def _scrub_payload(payload):
    """Second pass on read: the stored text is scrubbed again as JSON."""
    text = json.dumps(payload, separators=(',', ':'))
    return json.loads(scrub_string(text))


def _incident(row):
    payload = {
        'id': row['id'],
        'session_id': row['session_id'],
        'outcome': row['outcome'],
        'summary': _scrub(row['summary']),
        'root_cause': _scrub(row['root_cause']),
        'fix': _scrub(row['fix']),
        'tags': row['tags'],
        'confirmed_at': row.get('confirmed_at'),
        'truncated': False,
    }
    for field in ('fix', 'root_cause', 'summary', 'tags'):
        if len(json.dumps(payload, separators=(',', ':'))) <= MAX_INCIDENT_CHARS:
            break
        payload[field] = [] if field == 'tags' else ''
        payload['truncated'] = True
    return _scrub_payload(payload)


def similar_incidents(query):
    rows = search_confirmed(_scrub(query), limit=6)
    items = [_incident(row) for row in rows[:5]]
    return {'incidents': items, 'count': len(items), 'more': len(rows) > 5}


def find_runbooks(query, tags=None):
    rows = search_runbooks(_scrub(query), tags, limit=6)
    items = []
    for row in rows[:5]:
        items.append(_scrub_payload({
            'id': row['id'],
            'session_id': row['session_id'],
            'conclusion_id': row['conclusion_id'],
            'title': _scrub(row['title']),
            'when_to_use': _scrub(row['when_to_use']),
            'tags': row['tags'],
            'step_count': len(row['steps']),
        }))
    return {'runbooks': items, 'count': len(items), 'more': len(rows) > 5}


def get_runbook_payload(runbook_id):
    row = get_runbook(runbook_id)
    if not row:
        return None
    return _scrub_payload({
        'id': row['id'],
        'session_id': row['session_id'],
        'conclusion_id': row['conclusion_id'],
        'title': _scrub(row['title']),
        'when_to_use': _scrub(row['when_to_use']),
        'tags': row['tags'],
        'steps': row['steps'],
        'dead_ends': row['dead_ends'],
        'created_at': row['created_at'],
    })


def scrub_read(rows):
    """Scrub stored conclusion rows again on read (defence in depth)."""
    out = []
    for row in rows or []:
        item = dict(row)
        for field in ('summary', 'root_cause', 'fix', 'goal', 'operator_note'):
            item[field] = _scrub(item.get(field))
        for field in ('worked', 'did_not_work'):
            cleaned = []
            for entry in item.get(field) or []:
                entry = dict(entry) if isinstance(entry, dict) else {'note': entry}
                entry['note'] = _scrub(entry.get('note'))
                cleaned.append(entry)
            item[field] = cleaned
        item['follow_ups'] = [_scrub(v) for v in item.get('follow_ups') or []]
        item['tags'] = [_scrub(v) for v in item.get('tags') or []]
        out.append(item)
    return out


def as_json(payload):
    return json.dumps(payload, separators=(',', ':'))

"""Curated multi-step tool handlers.

Most integration tools are declarative: one upstream call plus optional
response shaping. A few need to orchestrate several calls and reason about the
result — Lovelace dashboard create/update/save/delete are the motivating cases
(HA's ``lovelace/config/save`` is a blind full replace with no locking, so the
safe path has to be built server-side).

A handler is registered by name and selected per tool via the
``integration_tools.handler`` column. ``tool_runner.run_tool`` dispatches to it
for both read and mutating tools, and the approval executor
(``main.approve_integration_call``) dispatches to the SAME handler after
approval, so a gated tool behaves identically whether it ran immediately or via
the operator queue.

Handlers return a JSON string (the model-facing tool result). They run WS
commands through ``ha_ws_exec`` (raw result + per-call audit) so a
read→write→read-back round trip never reintroduces a scrubbed placeholder.
"""
import copy
import hashlib
import json

from core.ha_ws import ha_ws_exec
from core.integration_proxy import ProxyError

LOVELACE_DASHBOARDS = 'lovelace/dashboards'
LOVELACE_CONFIG = 'lovelace/config'


def _err(code: str, message: str, status_code: int = 502) -> str:
    return json.dumps({'error': code, 'message': message, 'status_code': status_code})


def _canon(value) -> str:
    """Canonical JSON for hashing / equality. Key order and whitespace never
    differ between two logically-equal configs."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _hash(value) -> str:
    return hashlib.sha256(_canon(value).encode('utf-8')).hexdigest()


def _translate(msg: str) -> str:
    """Map the raw HA WS error string to a stable tool error code."""
    m = str(msg or '')
    low = m.lower()
    if 'unknown config specified: lovelace' in low:
        return 'ha_default_dashboard_untargetable'
    if 'unknown config specified' in low:
        return 'ha_config_not_found'
    if 'no config found' in low:
        return 'ha_no_config'
    if 'unable to find dashboard' in low:
        return 'ha_dashboard_not_found'
    if 'hyphen' in low:
        return 'ha_dashboard_url_invalid'
    if 'url_already_exists' in low or 'already exists' in low:
        return 'ha_dashboard_url_exists'
    return 'ha_ws_error'


# ── Lovelace helpers ────────────────────────────────────────────────────

def _list(integration, tool_name):
    rows = ha_ws_exec(integration, f'{LOVELACE_DASHBOARDS}/list', {}, tool_name=tool_name)
    return rows if isinstance(rows, list) else []


def _find(rows, dashboard_id=None, url_path=None):
    for d in rows or []:
        if not isinstance(d, dict):
            continue
        if dashboard_id is not None and d.get('id') == dashboard_id:
            return d
        if url_path is not None and d.get('url_path') == url_path:
            return d
    return None


def _compact(d):
    if not isinstance(d, dict):
        return {}
    item = {}
    for src, dst in (('id', 'dashboard_id'), ('url_path', 'url_path'),
                     ('title', 'title'), ('icon', 'icon'), ('mode', 'mode'),
                     ('show_in_sidebar', 'show_in_sidebar'),
                     ('require_admin', 'require_admin')):
        if d.get(src) is not None:
            item[dst] = d[src]
    if d.get('mode') == 'yaml' and d.get('filename'):
        item['filename'] = d['filename']
    return item


def _collect_targets(card):
    out = []

    def add(v):
        if isinstance(v, str) and v and v not in out:
            out.append(v)

    add(card.get('entity'))
    ents = card.get('entities')
    if isinstance(ents, list):
        for e in ents:
            if isinstance(e, str):
                add(e)
            elif isinstance(e, dict):
                add(e.get('entity'))
    elif isinstance(ents, dict):
        add(ents.get('entity'))
    add(card.get('camera_image'))
    for action in ('tap_action', 'hold_action', 'double_tap_action'):
        a = card.get(action)
        if isinstance(a, dict):
            add(a.get('navigation_path'))
    return out


def _view_cards_count(v):
    if not isinstance(v, dict):
        return 0
    if isinstance(v.get('cards'), list):
        return len(v['cards'])
    n = 0
    for s in (v.get('sections') or []):
        if isinstance(s, dict) and isinstance(s.get('cards'), list):
            n += len(s['cards'])
    return n


def _view_cards(v):
    """The card list a card-level op should operate on. Supports both the flat
    ``views[].cards[]`` layout and the newer ``views[].sections[].cards[]``
    layout (appends to the last section)."""
    if isinstance(v.get('cards'), list):
        return v['cards']
    if isinstance(v.get('sections'), list):
        if not v['sections']:
            v['sections'].append({'cards': []})
        sec = v['sections'][-1]
        if not isinstance(sec, dict):
            raise ValueError('last section is not an object')
        if not isinstance(sec.get('cards'), list):
            sec['cards'] = []
        return sec['cards']
    v['cards'] = []
    return v['cards']


def _view_index(views, sel):
    """Resolve a view selector (index, numeric string, path, title, or 'new') to
    an index. Returns None for an append point ('new' or index == len)."""
    if sel is None:
        raise ValueError('view is required')
    if isinstance(sel, bool):
        raise ValueError('view must be an index, path, title, or "new"')
    if isinstance(sel, int):
        if 0 <= sel < len(views):
            return sel
        if sel == len(views):
            return None
        raise ValueError('view index %d out of range' % sel)
    if isinstance(sel, str):
        if sel == 'new':
            return None
        if sel.isdigit():
            return _view_index(views, int(sel))
        for i, v in enumerate(views):
            if isinstance(v, dict) and (v.get('path') == sel or v.get('title') == sel):
                return i
        raise ValueError('no view with path/title %r' % sel)
    raise ValueError('view must be an index, path, title, or "new"')


def _op_view(views, sel):
    idx = _view_index(views, sel)
    if idx is None or idx >= len(views):
        raise ValueError('view %r does not exist' % sel)
    v = views[idx]
    if not isinstance(v, dict):
        raise ValueError('view %r is not an object' % sel)
    return v


def _apply_ops(cfg, ops):
    if not isinstance(ops, list) or not ops:
        raise ValueError('ops must be a non-empty array')
    new = copy.deepcopy(cfg)
    views = new.setdefault('views', [])
    if not isinstance(views, list):
        raise ValueError('config views is not a list')
    for op in ops:
        if not isinstance(op, dict):
            raise ValueError('each op must be an object')
        kind = op.get('op')
        if kind == 'append_card':
            v = _op_view(views, op.get('view'))
            card = op.get('card')
            if not isinstance(card, dict):
                raise ValueError('append_card requires a card object')
            _view_cards(v).append(copy.deepcopy(card))
        elif kind == 'set_card':
            v = _op_view(views, op.get('view'))
            idx = op.get('card_index')
            cards = _view_cards(v)
            if not isinstance(idx, int) or isinstance(idx, bool) or not (0 <= idx < len(cards)):
                raise ValueError('set_card card_index out of range')
            card = op.get('card')
            if not isinstance(card, dict):
                raise ValueError('set_card requires a card object')
            cards[idx] = copy.deepcopy(card)
        elif kind == 'delete_card':
            v = _op_view(views, op.get('view'))
            idx = op.get('card_index')
            cards = _view_cards(v)
            if not isinstance(idx, int) or isinstance(idx, bool) or not (0 <= idx < len(cards)):
                raise ValueError('delete_card card_index out of range')
            del cards[idx]
        elif kind == 'set_view':
            idx = _view_index(views, op.get('view'))
            vc = op.get('view_config')
            if not isinstance(vc, dict):
                raise ValueError('set_view requires a view_config object')
            if idx is None or idx >= len(views):
                views.append(copy.deepcopy(vc))
            else:
                views[idx] = copy.deepcopy(vc)
        elif kind == 'delete_view':
            idx = _view_index(views, op.get('view'))
            if idx is None or idx >= len(views):
                raise ValueError('delete_view could not resolve view %r' % op.get('view'))
            del views[idx]
        else:
            raise ValueError('unknown op %r' % kind)
    return new


def _diff(old, new):
    old_views = old.get('views') if isinstance(old.get('views'), list) else []
    new_views = new.get('views') if isinstance(new.get('views'), list) else []
    result = {
        'views_added': max(0, len(new_views) - len(old_views)),
        'views_removed': max(0, len(old_views) - len(new_views)),
        'cards_added': 0,
        'cards_removed': 0,
        'cards_changed': 0,
        'views_changed': [],
        'changed': False,
    }
    for i in range(min(len(old_views), len(new_views))):
        oc = _view_cards_count(old_views[i])
        nc = _view_cards_count(new_views[i])
        result['cards_added'] += max(0, nc - oc)
        result['cards_removed'] += max(0, oc - nc)
        if _canon(old_views[i]) != _canon(new_views[i]):
            result['cards_changed'] += 1
            result['views_changed'].append(i)
    result['changed'] = bool(
        result['views_added'] or result['views_removed']
        or result['cards_added'] or result['cards_removed']
        or result['cards_changed'] or _canon(old) != _canon(new))
    return result


def _summarize_view(i, v):
    cards = v.get('cards')
    if not isinstance(cards, list):
        cards = []
        for s in (v.get('sections') or []):
            if isinstance(s, dict) and isinstance(s.get('cards'), list):
                cards.extend(s['cards'])
    out = {'index': i, 'card_count': len(cards)}
    for k in ('path', 'title', 'icon'):
        if v.get(k) is not None:
            out[k] = v[k]
    if v.get('panel') is not None:
        out['panel'] = bool(v.get('panel'))
    strat = v.get('strategy')
    if isinstance(strat, dict):
        out['strategy'] = strat.get('type')
    elif strat is not None:
        out['strategy'] = strat
    summary_cards = []
    for ci, c in enumerate(cards):
        if not isinstance(c, dict):
            continue
        entry = {'index': ci, 'type': c.get('type')}
        if c.get('title'):
            entry['title'] = c['title']
        targets = _collect_targets(c)
        if targets:
            entry['targets'] = targets
        summary_cards.append(entry)
    out['cards'] = summary_cards
    return out


def _view_matches(i, v, sel):
    if sel is None:
        return True
    if isinstance(sel, bool):
        return False
    if isinstance(sel, int):
        return i == sel
    if isinstance(sel, str):
        if sel.isdigit():
            return i == int(sel)
        return v.get('path') == sel or v.get('title') == sel
    return False


# ── Handlers ────────────────────────────────────────────────────────────

def _h_dashboards(integration, tool, args):
    name = tool.get('name', '')
    rows = _list(integration, name)
    if args.get('full'):
        return json.dumps(rows)
    return json.dumps([_compact(d) for d in rows])


def _h_dashboard(integration, tool, args):
    name = tool.get('name', '')
    url_path = args.get('url_path')
    view_sel = args.get('view')
    full = bool(args.get('full'))
    max_bytes = args.get('max_bytes') or 20000
    payload = {'url_path': url_path} if url_path else {}
    try:
        cfg = ha_ws_exec(integration, LOVELACE_CONFIG, payload, tool_name=name)
    except ProxyError as e:
        return _err(_translate(e.message), e.message, e.status_code)
    if not isinstance(cfg, dict):
        cfg = {}
    dashboard_id = None
    mode = None
    try:
        dash = _find(_list(integration, name), url_path=url_path) if url_path else None
        if dash:
            dashboard_id = dash.get('id')
            mode = dash.get('mode')
    except ProxyError:
        pass
    if full:
        raw = _canon(cfg)
        size = len(raw.encode('utf-8'))
        if size > max_bytes:
            return json.dumps({
                'url_path': url_path or 'default', 'size_bytes': size,
                'config_hash': _hash(cfg),
                'truncated': True, 'config': None,
                'message': 'Config is %d bytes (max_bytes=%d); raise max_bytes to fetch it.'
                           % (size, max_bytes)})
        return json.dumps({'url_path': url_path or 'default', 'size_bytes': size,
                           'config_hash': _hash(cfg),
                           'truncated': False, 'config': cfg})
    views = cfg.get('views') if isinstance(cfg.get('views'), list) else []
    summary = {'url_path': url_path or 'default', 'dashboard_id': dashboard_id,
               'mode': mode, 'view_count': len(views),
               'config_hash': _hash(cfg),
               'top_level_keys': sorted(cfg.keys()), 'views': []}
    for i, v in enumerate(views):
        if isinstance(v, dict) and _view_matches(i, v, view_sel):
            summary['views'].append(_summarize_view(i, v))
    return json.dumps(summary)


def _h_create_dashboard(integration, tool, args):
    name = tool.get('name', '')
    title = args.get('title')
    url_path = args.get('url_path')
    allow_single_word = bool(args.get('allow_single_word'))
    if not title or not url_path:
        return _err('invalid_request', 'title and url_path are required', 400)
    if not allow_single_word and '-' not in url_path:
        return _err('ha_dashboard_url_invalid',
                    'url_path must contain a hyphen (-) (or pass allow_single_word: true)', 400)
    try:
        rows = _list(integration, name)
    except ProxyError as e:
        return _err(_translate(e.message), e.message, e.status_code)
    existing = _find(rows, url_path=url_path)
    if existing:
        return _err('ha_dashboard_url_exists',
                    'A dashboard with url_path %r already exists (dashboard_id=%s)'
                    % (url_path, existing.get('id')), 409)
    payload = {'title': title, 'url_path': url_path,
               'show_in_sidebar': bool(args.get('show_in_sidebar', True)),
               'require_admin': bool(args.get('require_admin', False))}
    if args.get('icon'):
        payload['icon'] = args['icon']
    if allow_single_word:
        payload['allow_single_word'] = True
    try:
        created = ha_ws_exec(integration, f'{LOVELACE_DASHBOARDS}/create', payload, tool_name=name)
    except ProxyError as e:
        return _err(_translate(e.message), e.message, e.status_code)
    item = _compact(created)
    if isinstance(created, dict):
        item['dashboard_id'] = created.get('id')
    verified = False
    try:
        verified = _find(_list(integration, name), url_path=url_path) is not None
    except ProxyError:
        pass
    item['verified'] = verified
    return json.dumps(item)


def _h_update_dashboard(integration, tool, args):
    name = tool.get('name', '')
    dashboard_id = args.get('dashboard_id')
    url_path = args.get('url_path')
    if not dashboard_id and not url_path:
        return _err('invalid_request', 'pass dashboard_id or url_path', 400)
    try:
        rows = _list(integration, name)
    except ProxyError as e:
        return _err(_translate(e.message), e.message, e.status_code)
    dash = _find(rows, dashboard_id=dashboard_id, url_path=url_path)
    if not dash:
        return _err('ha_dashboard_not_found',
                    'No dashboard matches %s; list with lovelace_dashboards'
                    % (dashboard_id or url_path), 404)
    if dash.get('mode') == 'yaml':
        return _err('ha_dashboard_yaml_readonly',
                    'Dashboard %r is YAML-mode and cannot be edited' % dash.get('url_path'), 400)
    payload = {'dashboard_id': dash.get('id')}
    for k in ('title', 'icon', 'show_in_sidebar', 'require_admin'):
        if args.get(k) is not None:
            payload[k] = args[k]
    if len(payload) == 1:
        return _err('invalid_request',
                    'nothing to update: pass title, icon, show_in_sidebar or require_admin', 400)
    try:
        updated = ha_ws_exec(integration, f'{LOVELACE_DASHBOARDS}/update', payload, tool_name=name)
    except ProxyError as e:
        return _err(_translate(e.message), e.message, e.status_code)
    if isinstance(updated, dict) and updated.get('id'):
        item = _compact(updated)
        item['dashboard_id'] = updated.get('id')
    else:
        item = _compact(dash)
    return json.dumps(item)


def _h_save_config(integration, tool, args):
    name = tool.get('name', '')
    url_path = args.get('url_path')
    ops = args.get('ops')
    raw_new = args.get('config')
    confirm = args.get('confirm')
    expect_hash = args.get('expect_hash')
    dry_run = bool(args.get('dry_run'))
    payload = {'url_path': url_path} if url_path else {}
    try:
        cfg = ha_ws_exec(integration, LOVELACE_CONFIG, payload, tool_name=name)
    except ProxyError as e:
        return _err(_translate(e.message), e.message, e.status_code)
    if not isinstance(cfg, dict):
        cfg = {}
    try:
        dash = _find(_list(integration, name), url_path=url_path) if url_path else None
    except ProxyError:
        dash = None
    if dash and dash.get('mode') == 'yaml':
        return _err('ha_dashboard_yaml_readonly',
                    'Dashboard %r is YAML-mode; edit its file instead' % url_path, 400)
    if expect_hash and expect_hash != _hash(cfg):
        return _err('ha_config_changed',
                    'Live config hash does not match expect_hash; re-read before writing', 409)
    if ops:
        try:
            new_cfg = _apply_ops(cfg, ops)
        except ValueError as e:
            return _err('invalid_request', str(e), 400)
    elif raw_new is not None:
        if confirm != 'replace':
            return _err('confirm_required',
                        'Full replace requires confirm: "replace" (or use ops)', 400)
        if isinstance(raw_new, str):
            try:
                new_cfg = json.loads(raw_new)
            except (ValueError, TypeError):
                return _err('invalid_request', 'config string is not valid JSON', 400)
        elif isinstance(raw_new, dict):
            new_cfg = raw_new
        else:
            return _err('invalid_request', 'config must be an object or JSON string', 400)
    else:
        return _err('invalid_request',
                    'pass ops, or config with confirm: "replace"', 400)
    diff = _diff(cfg, new_cfg)
    if dry_run:
        return json.dumps({'changed': bool(diff.get('changed')), 'dry_run': True,
                           'url_path': url_path or 'default', 'diff': diff})
    save_payload = {'config': new_cfg}
    if url_path:
        save_payload['url_path'] = url_path
    try:
        ha_ws_exec(integration, f'{LOVELACE_CONFIG}/save', save_payload, tool_name=name)
    except ProxyError as e:
        return _err(_translate(e.message), e.message, e.status_code)
    verified = False
    try:
        readback = ha_ws_exec(integration, LOVELACE_CONFIG, payload, tool_name=name)
        verified = _canon(readback) == _canon(new_cfg)
    except ProxyError:
        pass
    return json.dumps({'changed': bool(diff.get('changed')),
                       'url_path': url_path or 'default', 'diff': diff,
                       'verified': verified})


def _h_delete_dashboard(integration, tool, args):
    name = tool.get('name', '')
    dashboard_id = args.get('dashboard_id')
    url_path = args.get('url_path')
    if not dashboard_id and not url_path:
        return _err('invalid_request', 'pass dashboard_id or url_path', 400)
    try:
        rows = _list(integration, name)
    except ProxyError as e:
        return _err(_translate(e.message), e.message, e.status_code)
    dash = _find(rows, dashboard_id=dashboard_id, url_path=url_path)
    if not dash:
        return _err('ha_dashboard_not_found',
                    'No dashboard matches %s' % (dashboard_id or url_path), 404)
    if dash.get('mode') == 'yaml':
        return _err('ha_dashboard_yaml_readonly',
                    'Dashboard %r is YAML-mode (file-backed); remove it from configuration.yaml'
                    % dash.get('url_path'), 400)
    if not dash.get('id'):
        return _err('ha_dashboard_not_found',
                    'Dashboard %r has no storage id' % dash.get('url_path'), 404)
    try:
        ha_ws_exec(integration, f'{LOVELACE_DASHBOARDS}/delete',
                   {'dashboard_id': dash.get('id')}, tool_name=name)
    except ProxyError as e:
        return _err(_translate(e.message), e.message, e.status_code)
    # Deleting a storage dashboard also removes its stored config (HA's
    # collection change handler calls StorageLovelaceConfig.async_delete ->
    # store.async_remove), so no separate lovelace/config/delete is needed.
    verified = False
    try:
        verified = _find(_list(integration, name), dashboard_id=dash.get('id')) is None
    except ProxyError:
        pass
    return json.dumps({'deleted': True, 'dashboard_id': dash.get('id'),
                       'url_path': dash.get('url_path'), 'verified': verified})


HANDLERS = {
    'ha_lovelace_dashboards': _h_dashboards,
    'ha_lovelace_dashboard': _h_dashboard,
    'ha_lovelace_create_dashboard': _h_create_dashboard,
    'ha_lovelace_update_dashboard': _h_update_dashboard,
    'ha_lovelace_save_config': _h_save_config,
    'ha_lovelace_delete_dashboard': _h_delete_dashboard,
}


def run_handler(name: str, integration: dict, tool: dict, args: dict) -> str:
    """Run a registered handler and return its JSON string. ProxyError is
    translated to a stable tool error code; anything else surfaces as
    handler_failed."""
    fn = HANDLERS.get(name)
    if not fn:
        return json.dumps({'error': 'handler_not_found',
                           'message': "No handler registered for '%s'" % name,
                           'status_code': 500})
    try:
        return fn(integration, tool, args or {})
    except ProxyError as e:
        return _err(_translate(e.message), e.message, e.status_code)
    except Exception as e:  # noqa: BLE001 - never leak a traceback to the model
        return _err('handler_failed', '%s: %s' % (type(e).__name__, e), 500)

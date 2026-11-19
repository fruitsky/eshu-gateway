"""F11: the tool handlers and the write/restore preflights.

Handlers are registered by name in `core/tool_handlers.py`; the gated write
and restore dispatch to the same handler after approval, so an approved call
re-validates everything (hash, freeze, unresolved state) before it writes.
"""
import json

from core import ha_config_ssh

STATUS_BAD_REQUEST = 400


def _err(code, message, status=STATUS_BAD_REQUEST):
    return json.dumps({'error': code, 'message': message,
                       'status_code': status})


def _h_ha_config_list(integration, tool, args, ctx=None):
    try:
        files = ha_config_ssh.list_configs(integration, ctx=ctx)
    except ha_config_ssh.HaConfigError as exc:
        return _err(exc.code, exc.message)
    out = {'files': files, 'count': len(files)}
    # F11 follow-up: the first call establishes the pin; report it so the
    # agent can show the fingerprint without a second call.
    fingerprint = ha_config_ssh.pinned_fingerprint(integration)
    if fingerprint:
        out['host_fingerprint'] = fingerprint
    return json.dumps(out)


def _h_ha_config_read(integration, tool, args, ctx=None):
    try:
        out = ha_config_ssh.read_config(integration, (args or {}).get('path'),
                                        ctx=ctx)
    except ha_config_ssh.HaConfigError as exc:
        return _err(exc.code, exc.message)
    return json.dumps(out)


def _h_ha_config_write(integration, tool, args, ctx=None):
    args = args or {}
    try:
        out = ha_config_ssh.write_config(
            integration, args.get('path'), args.get('content'),
            args.get('expect_hash'), ctx=ctx, dry_run=bool(args.get('dry_run')))
    except ha_config_ssh.HaConfigError as exc:
        return _err(exc.code, exc.message)
    if not args.get('dry_run'):
        # F27 AZ2: an approved write can turn an allowed entity into a
        # template/shell command. Suspend what the file can redefine. BX3:
        # a scan that raises fails closed (suspend every HA allowance), and
        # only a second failure writes one `allowance_config_write_failed`
        # row instead of surfacing a traceback.
        try:
            from core import allowances
            allowances.on_config_write('config-write', args.get('path'),
                                       args.get('content'))
            if allowances.is_registry_write_path(args.get('path')):
                allowances.refresh_pins('config-write')
        except Exception as exc:
            try:
                from core import allowances
                allowances.on_config_write_failed(
                    'config-write', args.get('path'), exc)
            except Exception:
                pass
    return json.dumps(out)


def _h_ha_config_restore(integration, tool, args, ctx=None):
    args = args or {}
    path = args.get('path')
    backup = args.get('backup')
    try:
        root = ha_config_ssh.check_write_path(path)
        if str(backup or '').startswith('/config/'):
            backup = ha_config_ssh.to_root_path(backup)
        out = ha_config_ssh.restore_config(integration, path, backup, ctx=ctx)
    except ha_config_ssh.HaConfigError as exc:
        return _err(exc.code, exc.message)
    # F27 AZ2/BX3: a restore replaces a file the handler never sees, so treat
    # every restore as unmapped and suspend every HA allowance.
    try:
        from core import allowances
        allowances.on_config_restore('config-write', path)
    except Exception as exc:
        try:
            from core import allowances
            allowances.on_config_write_failed('config-write', path, exc)
        except Exception:
            pass
    return json.dumps(out)


def _h_ha_config_check(integration, tool, args, ctx=None):
    try:
        out = ha_config_ssh.check_config(integration, ctx=ctx)
    except ha_config_ssh.HaConfigError as exc:
        return _err(exc.code, exc.message)
    return json.dumps(out)


def _preflight_ha_config_write(integration, tool, args, ctx=None):
    """The card's summary: path, hashes, risky keys and the diff.

    Computed by the preflight (a dry-run read) so a doomed write — a refused
    path, a stale hash, a frozen fleet, an unresolved previous write — is
    never queued.
    """
    args = args or {}
    try:
        out = ha_config_ssh.write_config(
            integration, args.get('path'), args.get('content'),
            args.get('expect_hash'), ctx=ctx, dry_run=True)
    except ha_config_ssh.HaConfigError as exc:
        return _err(exc.code, exc.message)
    risky = ', '.join(out.get('risky') or []) or 'none'
    outside = ', '.join(g.get('target') or ''
                        for g in (out.get('grandfathered') or []))
    diff = (out.get('diff') or '')[:4000]
    summary = (
        'Write %s (%s -> %s)\nrisky keys: %s\nbackup: %s, then ha core '
        'check; a failed check restores the backup.\n%s'
        % (out['path'], (out.get('expect_hash') or '')[:12],
           (out.get('sha256') or '')[:12], risky, out.get('backup') or '',
           diff))
    if outside:
        summary += ('\nunchanged includes outside packages/: ' + outside)
    return {'_summary': summary}


def _preflight_ha_config_restore(integration, tool, args, ctx=None):
    args = args or {}
    path = args.get('path')
    backup = args.get('backup')
    try:
        root = ha_config_ssh.check_write_path(path)
        if str(backup or '').startswith('/config/'):
            backup = ha_config_ssh.to_root_path(backup)
        if not str(backup or '').startswith(root + '.eshu-'):
            raise ha_config_ssh.HaConfigError(
                'config_backup_refused',
                'the backup must belong to the same path')
    except ha_config_ssh.HaConfigError as exc:
        return _err(exc.code, exc.message)
    return {'_summary': ('Restore %s from %s, then ha core check; a failed '
                         'check puts the previous content back.'
                         % (ha_config_ssh._display_path(root), backup))}


HANDLERS = {
    'ha_config_list': _h_ha_config_list,
    'ha_config_read': _h_ha_config_read,
    'ha_config_write': _h_ha_config_write,
    'ha_config_restore': _h_ha_config_restore,
    'ha_config_check': _h_ha_config_check,
}

PREFLIGHTS = {
    'ha_config_write': _preflight_ha_config_write,
    'ha_config_restore': _preflight_ha_config_restore,
}

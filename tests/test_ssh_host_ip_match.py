"""BF2 — an agent's SSH host entry is matched by the gateway's IP only.

The gateway's hostname is self-reported at registration, so a compromised
gateway B can re-register under gateway A's name and satisfy an entry written
for A; the agent's commands then go to B. Hostnames stay stored (and can be
converted from the policy modal) but never match.
"""
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
APP_JS = os.path.join(ROOT, "dashboard", "static", "app.js")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _new_agent(name, hosts, mode='normal'):
    from db.agent_tokens import create_agent_token, get_agent_by_name
    from db.agent_policies import set_agent_policy
    _raw, agent_id = create_agent_token(name)
    set_agent_policy(agent_id, ssh_hosts_allow=hosts, ssh_mode=mode)
    return get_agent_by_name(name)


def _gateway(ip, hostname):
    from db.gateways import register_gateway
    register_gateway(ip, hostname)


def test_host_allowed_matches_the_ip_and_star_only():
    from core.ssh_exec import host_allowed
    gw = {'ip': '192.0.2.10', 'hostname': 'gw-a'}
    assert host_allowed(['192.0.2.10'], gw)
    assert host_allowed(['*'], gw)
    assert not host_allowed([], gw)
    # The hostname, and the host the agent typed, no longer count.
    assert not host_allowed(['gw-a'], gw)
    assert not host_allowed(['gw-a'], gw, 'gw-a')
    assert not host_allowed(['192.0.2.11'], gw)


def test_a_gateway_reregistering_under_anothers_name_does_not_match():
    """The attack: B takes A's hostname. An entry for A's IP must not match B."""
    _gateway('192.0.2.10', 'gw-a')
    _gateway('192.0.2.99', 'gw-b')
    agent = _new_agent('bf2-rereg', ['192.0.2.10'])
    # B re-registers under A's hostname. (Also what a first registration
    # under the stolen name looks like.)
    _gateway('192.0.2.99', 'gw-a')
    from core.ssh_exec import ssh_exec
    out = ssh_exec('192.0.2.99', 'uptime', agent=agent)
    assert out['status'] == 'refused', out
    assert out['reason'] == 'host_not_allowed', out
    assert '192.0.2.99' in out['message']
    # A is still reachable for the host check; the refusal past it is about
    # capability, not the host allowlist.
    out_a = ssh_exec('192.0.2.10', 'uptime', agent=agent)
    assert out_a.get('reason') not in ('host_not_allowed', None), out_a


def test_a_hostname_entry_is_refused_with_the_conversion_and_audited():
    _gateway('192.0.2.10', 'gw-a')
    agent = _new_agent('bf2-hostname', ['gw-a'])
    from core.ssh_exec import ssh_exec
    out = ssh_exec('192.0.2.10', 'uptime', agent=agent)
    assert out['status'] == 'refused', out
    assert out['reason'] == 'host_not_allowed'
    assert 'hostname' in out['message'].lower()
    assert '192.0.2.10' in out['message']
    from db.audit import get_audit_log
    rows = [r for r in get_audit_log(limit=20) if r['event_type'] == 'ssh_exec']
    assert any('status=hostname_entry_ignored' in (r['details'] or '') for r in rows), rows


def test_the_modal_ticks_and_counts_ips_only_and_offers_the_conversion():
    js = _read(APP_JS)
    assert 'const on = _agentSshHas(g.ip);' in js
    assert '_agentSshHas(g.ip) || _agentSshHas(g.hostname)' not in js
    assert 'function convertAgentSshHostnames()' in js
    assert 'Convert hostnames to IPs' in js
    assert 'Hostname entries are not matched' in js
    # The tab count says how many entries are ignored instead of counting them
    # as gateways.
    assert "' \\u00b7 ' + ignored + ' ignored'" in js


def test_bf2_the_kept_entries_are_escaped():
    """Security nit: the list is innerHTML, so escape it even though the
    entries are charset-checked on write."""
    js = _read(APP_JS)
    assert "extra.map(esc).join(', ')" in js
    assert "'Also kept (not a current gateway IP): ' + extra.join" not in js

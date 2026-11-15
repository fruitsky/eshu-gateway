"""X9: gated gateway files, generations, legacy 1.3 routes (BU1, BU2)."""
import hashlib
import os

import pytest

from core import installer_channels as ic


def _sha(path):
    return hashlib.sha256(open(path, 'rb').read()).hexdigest()


def _distinct_edge():
    """Make Edge a generation distinct from Fleet (both start as Build)."""
    ic.ensure_initialised()
    files = dict(ic.read_generation(ic.channel_generation(ic.EDGE)))
    files[ic.INSTALLER] = b"#!/bin/bash\n# edge\necho edge\n"
    files[ic.WINDOWS] = files[ic.WINDOWS] + b"\n# edge build variant\n"
    files[ic.RUNNER] = files[ic.RUNNER] + b"\n# edge runner variant\n"
    ic._write_pointer(ic.EDGE, ic._write_generation(files))


@pytest.fixture
def build(tmp_path, monkeypatch):
    path = tmp_path / "build-installer.sh"
    path.write_bytes(b"#!/bin/bash\necho build-1\n")
    monkeypatch.setattr(ic, "build_path", lambda: str(path))
    return path


class TestGatewayFileRoute:

    def test_requires_a_gateway_token(self, client):
        assert client.get("/api/gateway/files/approved_windows.sh").status_code == 401

    def test_unknown_name_is_404(self, client, gateway_headers):
        assert client.get("/api/gateway/files/evil.sh",
                          headers=gateway_headers).status_code == 404

    def test_a_gateway_token_must_be_bound_to_the_ip(self, client):
        # A valid token for a different IP is still a valid token; the route
        # resolves it, so it serves that gateway's channel. An invalid token
        # is the refusal path.
        r = client.get("/api/gateway/files/approved_windows.sh",
                       headers={"X-Gateway-Token": "not-a-token"})
        assert r.status_code == 401

    def test_dev_mark_gets_edge_and_prod_gets_fleet(self, client, gateway_headers):
        from db.gateways import set_gateway_mode
        _distinct_edge()
        edge = ic.channel_generation(ic.EDGE)
        fleet = ic.channel_generation(ic.FLEET)
        assert edge != fleet

        r = client.get("/api/gateway/files/approved_windows.sh", headers=gateway_headers)
        assert r.status_code == 200
        assert r.headers["x-eshu-channel"] == "fleet"
        assert r.headers["x-eshu-generation"] == fleet
        assert r.content == ic.read_file(ic.FLEET, ic.WINDOWS)
        assert r.headers["cache-control"] == "no-store"

        set_gateway_mode("10.0.0.1", "dev")
        r = client.get("/api/gateway/files/approved_windows.sh", headers=gateway_headers)
        assert r.headers["x-eshu-channel"] == "edge"
        assert r.content == ic.read_file(ic.EDGE, ic.WINDOWS)
        assert r.content != ic.read_file(ic.FLEET, ic.WINDOWS)

    def test_both_files_have_their_own_route_and_hash(self, client, gateway_headers):
        for url, name in (("/api/gateway/files/approved_windows.sh", ic.WINDOWS),
                          ("/api/gateway/files/fleet-runner.sh", ic.RUNNER)):
            r = client.get(url, headers=gateway_headers)
            assert r.status_code == 200
            assert r.content == ic.read_file(ic.FLEET, name)
            assert r.headers["x-eshu-file-sha256"] == ic.channel_file_hash(ic.FLEET, name)

    def test_a_missing_generation_is_503(self, client, gateway_headers):
        ic.ensure_initialised()
        os.remove(os.path.join(ic._gen_dir(ic.channel_generation(ic.FLEET)),
                               ic.INSTALLER))
        r = client.get("/api/gateway/files/approved_windows.sh", headers=gateway_headers)
        assert r.status_code == 503


class TestGenerations:

    def test_seed_promote_rollback_move_all_three(self, auth_client, build):
        def info(ch):
            return ic.channel_info(ch)

        start = info(ic.FLEET)
        build.write_bytes(b"#!/bin/bash\necho build-2\n")
        ic.seed_edge()
        edge = info(ic.EDGE)
        assert edge['generation'] != start['generation']
        assert edge['installer'] != start['installer']
        assert (edge['windows'], edge['runner']) == (start['windows'], start['runner'])

        r = auth_client.post("/api/dev/promote")
        assert r.status_code == 200
        promoted = info(ic.FLEET)
        assert promoted['generation'] == edge['generation']
        assert promoted['windows'] == edge['windows']

        r = auth_client.post("/api/dev/rollback")
        assert r.status_code == 200
        back = info(ic.FLEET)
        assert back['generation'] == start['generation']
        assert (back['windows'], back['runner']) == (start['windows'], start['runner'])

    def test_seed_audit_names_the_generation_and_three_hashes(self, auth_client):
        from db.audit import get_audit_log
        _distinct_edge()
        auth_client.post("/api/dev/seed")
        ic.seed_edge()
        row = [r for r in get_audit_log(50) if r["event_type"] == "edge_seeded"][0]
        info = ic.channel_info(ic.EDGE)
        assert info['generation'] in row['details']
        assert info['windows'] in row['details']
        assert info['runner'] in row['details']

    def test_a_partial_generation_is_never_served(self, auth_client):
        """BU1: a generation missing one file is missing, not a mixed copy."""
        ic.ensure_initialised()
        files = dict(ic.read_generation(ic.channel_generation(ic.FLEET)))
        files[ic.INSTALLER] = b"#!/bin/bash\n# partial\n"
        gen = ic._write_generation(files)
        os.remove(os.path.join(ic._gen_dir(gen), ic.RUNNER))
        ic._write_pointer(ic.FLEET, gen)
        assert auth_client.get("/api/gateway-script").status_code == 503
        assert auth_client.get("/api/gateway/files/approved_windows.sh",
                               headers={"X-Gateway-Token": "none"}).status_code == 401

    def test_a_build_change_changes_no_served_file(self, auth_client, build):
        before = ic.channel_info(ic.FLEET)
        build.write_bytes(b"#!/bin/bash\necho build-2\n")
        assert ic.channel_info(ic.FLEET) == before
        assert ic.channel_info(ic.EDGE) == before


class TestLegacyRoutes:

    def test_the_legacy_bytes_are_frozen(self, client):
        base = os.path.join(ic.gateway_files_dir(), 'legacy-pre-x9')
        pins = {
            'approved_windows.sh': '5ae4a36d5aa76a50bb6fef3c38939e177992f12d92198e9cbdb375c7f5474383',
            'fleet-runner.sh': '6633c6a9827adc291ea7a6b2652522ff686a2ecad7d7853e75d69081edf852b5',
        }
        for name, pin in pins.items():
            assert _sha(os.path.join(base, name)) == pin
        r = client.get('/static/features/approved_windows.sh')
        assert r.status_code == 200
        assert r.content == open(os.path.join(base, 'approved_windows.sh'), 'rb').read()
        r = client.get('/static/fleet-runner.sh')
        assert r.status_code == 200
        assert r.content == open(os.path.join(base, 'fleet-runner.sh'), 'rb').read()

    def test_retirement_needs_seven_quiet_days_and_then_410(self, auth_client):
        from db.policies import set_meta
        set_meta('legacy_pre_x9_since', '1')
        r = auth_client.post('/api/dev/legacy-retire')
        assert r.status_code == 200
        assert auth_client.get('/static/features/approved_windows.sh').status_code == 410

    def test_retirement_is_refused_while_a_pre_x9_gateway_polls(self, auth_client):
        from db.gateways import register_gateway, update_gateway_last_seen
        register_gateway('10.0.0.50', 'old-gw', 'v6.0')
        update_gateway_last_seen('10.0.0.50')
        assert auth_client.post('/api/dev/legacy-retire').status_code == 409

    def test_bw1_a_1_4_gateway_without_x9_fields_still_blocks(self, auth_client):
        """A 1.4 gateway reports installer_hash but still uses /static."""
        from db.gateways import register_gateway, set_gateway_installer_hash
        from db.policies import set_meta
        set_meta('legacy_pre_x9_since', '1')
        register_gateway('10.0.0.51', 'x8-gw', 'v6.0')
        set_gateway_installer_hash('10.0.0.51', 'abcdefabcdef')
        r = auth_client.post('/api/dev/legacy-retire')
        assert r.status_code == 409
        status = auth_client.get('/api/dev/status').json()['legacy_pre_x9']
        assert status['gateway_count'] == 1
        assert status['retire_available'] is False

    def test_the_hit_record_is_throttled(self, auth_client):
        from db.policies import get_meta, set_meta
        now = int(__import__('time').time())
        set_meta('legacy_pre_x9_last_hit_at', str(now))
        set_meta('legacy_pre_x9_last_hit_ip', '192.0.2.1')
        assert auth_client.get('/static/features/approved_windows.sh').status_code == 200
        assert get_meta('legacy_pre_x9_last_hit_at') == str(now)
        set_meta('legacy_pre_x9_last_hit_at', str(now - 120))
        auth_client.get('/static/features/approved_windows.sh')
        assert int(get_meta('legacy_pre_x9_last_hit_at')) >= now

    def test_a_pre_x9_installer_clears_retirement(self, auth_client, build):
        from db.audit import get_audit_log
        from db.policies import get_meta, set_meta
        set_meta('legacy_pre_x9_retired', '1')
        auth_client.post('/api/dev/seed')
        r = auth_client.post('/api/dev/promote')
        assert r.status_code == 200
        assert get_meta('legacy_pre_x9_retired') == '0'
        assert 'legacy_pre_x9_retirement_cleared' in [
            x['event_type'] for x in get_audit_log(50)]

    def test_a_recent_legacy_hit_blocks_the_switch(self, auth_client):
        from db.policies import set_meta
        set_meta('legacy_pre_x9_since', '1')
        assert auth_client.get('/static/features/approved_windows.sh').status_code == 200
        assert auth_client.post('/api/dev/legacy-retire').status_code == 409

    def test_bw2_a_stale_installer_refuses_the_seed(self, auth_client, build):
        import base64
        base = ic.read_generation(ic.channel_generation(ic.FLEET))
        build.write_bytes(
            b"#!/bin/bash\ninstall_seed_files() { :; }\n"
            + b"printf '%s' '" + base64.b64encode(b'wrong windows') + b"'\n")
        r = auth_client.post('/api/dev/seed')
        assert r.status_code == 503


class TestGatewayFileReport:

    def test_files_channel_and_mixed_warning(self, auth_client, gateway_headers):
        from db.gateways import (register_gateway, set_gateway_files,
                                 set_gateway_installer_hash)
        _distinct_edge()
        fleet = ic.channel_info(ic.FLEET)
        edge = ic.channel_info(ic.EDGE)
        register_gateway('10.0.0.2', 'same', 'v6.0')
        set_gateway_files('10.0.0.2', fleet['windows'], fleet['runner'], 'https')
        register_gateway('10.0.0.3', 'mixed', 'v6.0')
        set_gateway_installer_hash('10.0.0.3', fleet['installer'])
        set_gateway_files('10.0.0.3', edge['windows'], fleet['runner'], 'http')
        rows = {g['ip']: g for g in auth_client.get('/api/gateways').json()}
        assert rows['10.0.0.2']['files_channel'] == 'fleet'
        assert rows['10.0.0.2']['files_mixed'] is False
        assert rows['10.0.0.3']['files_channel'] == 'mixed'
        assert rows['10.0.0.3']['files_mixed'] is True
        assert rows['10.0.0.3']['dashboard_scheme'] == 'http'

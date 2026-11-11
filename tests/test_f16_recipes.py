"""Chunk F16 — recipes.

Synthetic data only: TEST-NET addresses, `web01`, `*.example.test`. Tool calls
and SSH runs are stubbed at the engine's own seams; the policy/validation
layers run for real.
"""
import asyncio
import json
import time

import pytest

from core.recipe_params import (
    RecipeParamError, normalise_param, resolve_values, substitute_args,
    substitute_command, validate_slot_step, validate_template,
)
from core.recipe_tools import (
    FORBIDDEN_RECIPE_TOOLS, RECIPE_TOOLS, RECIPE_UNDO, classify,
)
from core.recipes import (
    RecipeError, approve, execute_rollback, execute_run, propose, rollback,
    start_run, validate_document,
)
from db.agent_policies import set_agent_policy
from db.agent_tokens import (
    create_agent_token, get_agent_by_name, revoke_agent_token,
)
from db.core import db_conn
from db.gateways import (
    register_gateway, set_gateway_run_limits, set_gateway_ssh_capabilities,
    set_gateway_ssh_host_key, set_gateway_token,
)
from db.integrations import create_integration, create_tool, get_tool
from db.recipes import get_recipe, get_run, get_version_by_number, list_runs
from db.requests import get_request_row
from db.session_plans import (
    PlanError, approve_plan, claim_mcp_plan_item, claim_plan_item,
    claim_recipe_item, create_plan, create_recipe_plan, get_plan,
)
from db.sessions import upsert_session
from core.npm_seed import seed_npm_tools
from core.pihole_seed import seed_pihole_tools

HOST = '192.0.2.20'
GW = 'web01.example.test'


# ── fixtures and helpers ───────────────────────────────────────────────

@pytest.fixture
def env(monkeypatch, temp_db):
    npm = create_integration('Nginx Proxy Manager', 'https://192.0.2.60/api',
                             'header', 'npm-token',
                             auth_header_name='Authorization', kind='npm')
    seed_npm_tools(npm)
    pihole = create_integration('Pi-hole', 'https://192.0.2.61/api', 'header',
                                'pi-token', auth_header_name='Authorization',
                                kind='pihole')
    seed_pihole_tools(pihole)
    register_gateway(HOST, GW, 'v15.3')
    set_gateway_run_limits(HOST, 'v1')
    # AW1/AW2: a run started by an agent has a real agent row, and the engine
    # re-reads it before every step.
    create_agent_token('hermes')
    hermes = get_agent_by_name('hermes')
    set_agent_policy(hermes['id'], ssh_hosts_allow=[GW], ssh_mode='approval')
    import core.recipes as recipes
    monkeypatch.setattr(recipes, 'verify_access_list',
                        lambda integration, access: access)
    return {'npm': npm, 'pihole': pihole}


def doc(name='add_internal_fqdn', **overrides):
    base = {
        'name': name,
        'description': 'Point a name at a host through NPM and verify it.',
        'parameters': [
            {'name': 'fqdn', 'type': 'fqdn', 'example': 'app.example.test'},
            {'name': 'target_ip', 'type': 'ipv4', 'example': '192.0.2.10'},
            {'name': 'port', 'type': 'port', 'example': 443},
        ],
        'allowed_agents': ['hermes'],
        'preconditions': [
            {'kind': 'tool',
             'call': {'integration': 'Pi-hole', 'tool': 'local_dns_records',
                      'args': {}},
             'pass': {'field': 'hosts', 'contains': {
                 'where': {'name': {'param': 'fqdn'},
                           'ips': {'param': 'target_ip'}}}}},
        ],
        'steps': [
            {'id': '1', 'kind': 'tool', 'title': 'NPM proxy host',
             'call': {'integration': 'Nginx Proxy Manager',
                      'tool': 'create_proxy_host',
                      'args': {'body': {
                          'domain_names': [{'param': 'fqdn'}],
                          'forward_scheme': 'http',
                          'forward_host': {'param': 'target_ip'},
                          'forward_port': {'param': 'port'},
                          'access_list': {'id': 7, 'name': 'Internal only'}}}},
             'precheck': {'kind': 'tool',
                          'call': {'integration': 'Nginx Proxy Manager',
                                   'tool': 'proxy_hosts',
                                   'args': {'full': True}},
                          'pass': {'field': 'items', 'contains': {
                              'where': {'domain_names': {'param': 'fqdn'}}}}},
             'check': {'kind': 'tool',
                       'call': {'integration': 'Nginx Proxy Manager',
                                'tool': 'proxy_hosts',
                                'args': {'full': True}},
                       'pass': {'field': 'items', 'contains': {
                           'where': {'domain_names': {'param': 'fqdn'},
                                     'forward_host': {'param': 'target_ip'},
                                     'forward_port': {'param': 'port'},
                                     'access_list_id': 7}}}},
             'undo': {'kind': 'tool',
                      'call': {'integration': 'Nginx Proxy Manager',
                               'tool': 'delete_proxy_host',
                               'args': {'id': {'result': 'id'}}}}},
            {'id': '2', 'kind': 'ssh', 'title': 'The name resolves',
             'ssh': {'host': {'enum': [GW]},
                     'command': 'getent hosts {fqdn}'},
             'check': 'self', 'undo': None},
        ],
    }
    base.update(overrides)
    return base


def approved(name='add_internal_fqdn', **overrides):
    propose(doc(name, **overrides), owner='operator')
    recipe = get_recipe(name=name)
    approve(recipe['id'], 1, actor='operator')
    return recipe['id']


def approved_tool_only(name='npm_only'):
    """A recipe with one tool step: what an operator-started run may be."""
    d = doc(name)
    d['steps'] = d['steps'][:1]
    propose(d, owner='operator')
    recipe = get_recipe(name=name)
    approve(recipe['id'], 1, actor='operator')
    return recipe['id']


def make_host_result(**kwargs):
    row = {'id': 7, 'domain_names': ['app.example.test'],
           'forward_host': '192.0.2.10', 'forward_port': 443,
           'access_list_id': 7}
    row.update(kwargs)
    return {'items': [row]}


class Calls:
    def __init__(self):
        self.tool_calls = []
        self.ssh_calls = []

    def tool(self, run, integration, tool, args, step_id):
        self.tool_calls.append((integration, tool, args, step_id))
        if tool == 'local_dns_records':
            return ({'hosts': [{'name': 'app.example.test',
                                'ips': ['192.0.2.10']}]}, 200, {})
        if tool == 'create_proxy_host':
            return ({'id': 7, 'domain_names': ['app.example.test']}, 201, {})
        if tool == 'proxy_hosts':
            if step_id.endswith(':pre'):
                return ({'items': []}, 200, {})
            return (make_host_result(), 200, {})
        if tool == 'proxy_host':
            return (make_host_result()['items'][0], 200, {})
        if tool == 'delete_proxy_host':
            return ({'ok': True}, 200, {})
        raise AssertionError(tool)

    def ssh(self, command, host, run, step_id, had_slot=False):
        self.ssh_calls.append((command, host, step_id))
        return 0, 'app.example.test has address 192.0.2.10'


@pytest.fixture
def run_env(env, monkeypatch):
    import core.recipes as recipes
    calls = Calls()
    monkeypatch.setattr(recipes, '_run_tool_call', calls.tool)
    monkeypatch.setattr(recipes, '_run_ssh', calls.ssh)
    return calls


def _approve_plan(plan_id):
    approve_plan(plan_id, decided_by='operator')


# ── parameters ─────────────────────────────────────────────────────────

class TestParams:
    @pytest.mark.parametrize('kind,good,bad', [
        ('hostname', 'web01', '-web01'),
        ('hostname', 'a-b', 'a-'),
        ('fqdn', 'app.example.test', '-app.example.test'),
        ('fqdn', 'a.b.c', 'app..test'),
        ('slug', 'core_mosquitto', 'Core'),
        ('port', '443', '99999'),
        ('int', '-3', 'x'),
    ])
    def test_types(self, kind, good, bad):
        spec = {'name': 'v', 'type': kind, 'min': -10, 'max': 10}
        assert normalise_param(spec, good) is not None
        with pytest.raises(RecipeParamError):
            normalise_param(spec, bad)

    def test_ipv4_normalises(self):
        assert normalise_param({'name': 'ip', 'type': 'ipv4'}, ' 192.0.2.10 ') \
            == '192.0.2.10'

    def test_fqdn_is_lowercased_and_stripped(self):
        assert normalise_param({'name': 'f', 'type': 'fqdn'}, 'APP.Example.Test.') \
            == 'app.example.test'

    def test_port_is_an_int(self):
        assert normalise_param({'name': 'p', 'type': 'port'}, '443') == 443

    def test_enum_is_exact(self):
        spec = {'name': 'e', 'type': 'enum', 'values': ['a', 'b']}
        assert normalise_param(spec, 'b') == 'b'
        with pytest.raises(RecipeParamError):
            normalise_param(spec, 'A')

    def test_unknown_and_missing(self):
        recipe = {'parameters': [{'name': 'a', 'type': 'slug'}]}
        assert resolve_values(recipe, {'a': 'x'}) == {'a': 'x'}
        with pytest.raises(RecipeParamError) as exc:
            resolve_values(recipe, {'a': 'x', 'b': 'y'})
        assert exc.value.code == 'unknown_param'
        with pytest.raises(RecipeParamError) as exc:
            resolve_values(recipe, {})
        assert exc.value.code == 'missing_param'


# ── templates and the slot-verb allowlist ──────────────────────────────

class TestTemplates:
    declared = {'fqdn', 'ip', 'x'}

    def test_a_slot_is_one_quoted_word(self):
        out = substitute_command('getent hosts {fqdn}', {'fqdn': 'app.example.test'},
                                 self.declared)
        assert out == 'getent hosts app.example.test'
        quoted = substitute_command('getent hosts {x}', {'x': 'a; rm -rf /'},
                                    self.declared)
        assert quoted == "getent hosts 'a; rm -rf /'"

    def test_template_spellings_are_refused(self):
        for template in ('echo "{fqdn}"', 'echo {X}', 'echo { x }',
                         'echo --host={fqdn}', 'echo x{fqdn}', 'echo \\{fqdn}',
                         'echo $HOME', 'echo `id`'):
            with pytest.raises(RecipeParamError):
                validate_template(template, self.declared)

    def test_whole_word_slots_are_accepted(self):
        assert validate_template('echo {fqdn}', self.declared)
        assert validate_template('echo {fqdn} {ip}', self.declared)

    def test_v1_refuses_executors(self):
        for template in ('{x} --help', 'sudo {x}', 'sh -c {x}', 'xargs {x}',
                         'find . -exec {x} ;'):
            with pytest.raises(RecipeParamError):
                validate_template(template, self.declared)

    @pytest.mark.parametrize('template', [
        'getent hosts {fqdn} | sh', 'getent hosts {fqdn}; reboot',
        'getent hosts {fqdn} && reboot', 'getent hosts {fqdn} > /etc/hosts',
        'getent hosts {fqdn} < /etc/passwd', '/usr/bin/getent hosts {fqdn}',
        'sudo getent hosts {fqdn}', 'env X=1 getent hosts {fqdn}',
        'timeout 5 getent hosts {fqdn}', 'awk {x}', 'sed {x}',
    ])
    def test_y1_refuses_anything_but_a_lookup(self, template):
        with pytest.raises(RecipeParamError):
            validate_slot_step(template, self.declared)

    @pytest.mark.parametrize('template', [
        'getent hosts {fqdn}', 'dig +short {fqdn}', 'host {fqdn}',
        'nslookup {fqdn}', 'ping -c 3 -W 2 {fqdn}',
    ])
    def test_y1_allows_the_five_lookups(self, template):
        assert validate_slot_step(template, self.declared) is True

    def test_a_slot_free_command_is_left_alone(self):
        assert validate_slot_step('uptime', self.declared) is None

    def test_aa2_bounds_ping(self):
        for template in ('ping {fqdn}', 'ping -c 3 {fqdn}',
                         'ping -c 9 -W 1 {fqdn}', 'ping -c 3 -W 0 {fqdn}'):
            with pytest.raises(RecipeParamError):
                validate_slot_step(template, self.declared)

    def test_v2_refuses_a_leading_dash(self):
        with pytest.raises(RecipeParamError):
            substitute_command('getent hosts {x}', {'x': '-o.example'},
                               self.declared)
        with pytest.raises(RecipeParamError):
            normalise_param({'name': 'f', 'type': 'fqdn'}, '-o.example.test')

    def test_a_tool_slot_stays_a_json_value(self):
        args = substitute_args({'forward_port': {'param': 'port'},
                                'domain_names': [{'param': 'fqdn'}]},
                               {'port': 443, 'fqdn': 'app.example.test'})
        assert args == {'forward_port': 443,
                        'domain_names': ['app.example.test']}

    def test_a_result_slot_is_kept_for_the_undo(self):
        args = substitute_args({'id': {'result': 'id'}}, {}, keep_results=True)
        assert args == {'id': {'result': 'id'}}
        with pytest.raises(RecipeParamError):
            substitute_args({'id': {'result': 'id'}}, {})


# ── the document ───────────────────────────────────────────────────────

class TestDocument:
    def test_the_example_validates(self, env):
        out = validate_document(doc())
        assert out['name'] == 'add_internal_fqdn'
        assert len(out['steps']) == 2

    def test_too_many_steps(self, env):
        steps = [{'id': 's%d' % i, 'kind': 'ssh',
                  'ssh': {'host': {'enum': [GW]}, 'command': 'uptime'},
                  'check': None, 'undo': None} for i in range(11)]
        with pytest.raises(RecipeError) as exc:
            validate_document(doc(steps=steps))
        assert exc.value.code == 'too_many_steps'

    def test_too_many_ssh_steps(self, env):
        steps = [{'id': 's%d' % i, 'kind': 'ssh',
                  'ssh': {'host': {'enum': [GW]}, 'command': 'uptime'},
                  'check': None, 'undo': None} for i in range(4)]
        with pytest.raises(RecipeError) as exc:
            validate_document(doc(steps=steps))
        assert exc.value.code == 'too_many_ssh_steps'

    def test_a_host_parameter_is_not_possible(self, env):
        steps = [{'id': '1', 'kind': 'ssh',
                  'ssh': {'host': {'param': 'fqdn'}, 'command': 'uptime'},
                  'check': None, 'undo': None}]
        with pytest.raises(RecipeError):
            validate_document(doc(steps=steps))

    def test_a_forbidden_tool_is_refused(self, env):
        steps = [{'id': '1', 'kind': 'tool', 'title': 'no',
                  'call': {'integration': 'Nginx Proxy Manager',
                           'tool': 'write', 'args': {}},
                  'check': None, 'undo': None}]
        with pytest.raises(RecipeError) as exc:
            validate_document(doc(steps=steps))
        assert exc.value.code == 'recipe_tool_forbidden'

    def test_an_undo_only_tool_cannot_be_a_step(self, env):
        steps = [{'id': '1', 'kind': 'tool', 'title': 'no',
                  'call': {'integration': 'Nginx Proxy Manager',
                           'tool': 'delete_proxy_host', 'args': {'id': 7}},
                  'check': None, 'undo': None}]
        with pytest.raises(RecipeError) as exc:
            validate_document(doc(steps=steps))
        assert exc.value.code == 'recipe_tool_forbidden'

    def test_v6_a_same_named_tool_on_another_kind_is_refused(self, env):
        assert classify('pihole', 'create_proxy_host') == 'forbidden'

    def test_an_unknown_argument_is_refused(self, env):
        steps = [{'id': '1', 'kind': 'tool', 'title': 'x',
                  'call': {'integration': 'Nginx Proxy Manager',
                           'tool': 'create_proxy_host',
                           'args': {'body': {
                               'domain_names': [{'param': 'fqdn'}],
                               'forward_scheme': 'http',
                               'forward_host': {'param': 'target_ip'},
                               'forward_port': {'param': 'port'},
                               'access_list': {'public': True},
                               'certificate_id': 2}}},
                  'check': None, 'undo': None}]
        with pytest.raises(RecipeError) as exc:
            validate_document(doc(steps=steps))
        assert exc.value.code == 'recipe_arg_forbidden'

    def test_advanced_config_is_refused(self, env):
        d = doc()
        d['steps'][0]['call']['args']['body']['advanced_config'] = 'x'
        with pytest.raises(RecipeError) as exc:
            validate_document(d)
        assert exc.value.code == 'recipe_arg_forbidden'

    def test_an_enum_parameter_caps_at_16(self, env):
        d = doc(parameters=[{'name': 'e', 'type': 'enum',
                             'values': ['v%d' % i for i in range(17)]}])
        with pytest.raises(RecipeError):
            validate_document(d)

    def test_int_needs_min_and_max(self, env):
        d = doc(parameters=[{'name': 'n', 'type': 'int'}])
        with pytest.raises(RecipeError):
            validate_document(d)

    def test_the_registry_walk_classifies_every_tool(self, env):
        from db.integrations import get_integrations, get_tools
        seen = set()
        for integration in get_integrations():
            kind = (integration.get('kind') or '').lower()
            for tool in get_tools(integration['id']):
                verdict = classify(kind, tool['name'])
                assert verdict in ('call', 'undo', 'forbidden'), (kind, tool['name'])
                seen.add((kind, tool['name']))
        assert ('npm', 'create_proxy_host') in seen
        # The allowlist and the deny list never overlap.
        assert not (set(RECIPE_TOOLS) & set(RECIPE_UNDO))
        assert not ({t for _, t in RECIPE_TOOLS} & FORBIDDEN_RECIPE_TOOLS)


# ── the access list (W2) ───────────────────────────────────────────────

class TestAccessList:
    def _with(self, value):
        d = doc()
        body = d['steps'][0]['call']['args']['body']
        body.pop('access_list', None)
        if value is not _MISSING:
            body['access_list'] = value
        return d

    def test_required(self, env):
        with pytest.raises(RecipeError) as exc:
            validate_document(self._with(_MISSING))
        assert exc.value.code == 'recipe_access_list_required'

    def test_public_is_explicit_and_sends_zero(self, env):
        out = validate_document(self._with({'public': True}))
        assert out['steps'][0]['call']['args']['body']['access_list'] == {'public': True}

    @pytest.mark.parametrize('value', [
        {'param': 'fqdn'}, 7, {'id': 0, 'name': 'x'}, {'public': False},
        {'id': 7, 'name': 'x', 'extra': 1}, {'id': 7},
    ])
    def test_invalid_shapes(self, env, value):
        with pytest.raises(RecipeError):
            validate_document(self._with(value))

    def test_unknown_id_is_refused(self, env, monkeypatch):
        import core.recipes as recipes
        monkeypatch.setattr(recipes, 'verify_access_list',
                            lambda integration, access: (_ for _ in ()).throw(
                                RecipeError('recipe_access_list_unknown', 'no')))
        with pytest.raises(RecipeError) as exc:
            validate_document(self._with({'id': 9, 'name': 'Nope'}))
        assert exc.value.code == 'recipe_access_list_unknown'


_MISSING = object()


# ── approval, plans and the engine claim (V5) ─────────────────────────

class TestApproval:
    def test_a_draft_cannot_run(self, env):
        propose(doc(), owner='operator')
        with pytest.raises(RecipeError) as exc:
            start_run('add_internal_fqdn',
                      {'fqdn': 'app.example.test', 'target_ip': '192.0.2.10',
                       'port': 443}, agent='hermes', session_id='s1')
        assert exc.value.code == 'recipe_not_approved'

    def test_an_edit_creates_a_new_draft(self, env):
        recipe_id = approved()
        from core.recipes import propose as propose_again
        d = doc()
        d['description'] = 'changed'
        propose_again(d, owner='operator')
        recipe = get_recipe(recipe_id=recipe_id)
        assert recipe['status'] == 'draft'
        v2 = get_version_by_number(recipe_id, 2)
        assert v2['approved_at'] == 0
        assert v2['content_hash'] != get_version_by_number(recipe_id, 1)['content_hash']

    def test_an_agent_may_only_run_a_recipe_that_names_it(self, env, run_env):
        recipe_id = approved()
        with pytest.raises(RecipeError) as exc:
            start_run(recipe_id, {'fqdn': 'app.example.test',
                                  'target_ip': '192.0.2.10', 'port': 443},
                      agent='someone-else', session_id='s1')
        assert exc.value.code == 'recipe_agent_not_allowed'

    def test_the_operator_may_always_run(self, env, run_env):
        # AW1: an operator-started run has no agent identity, so it may run a
        # tool-only recipe and never an SSH step.
        d = doc()
        d['steps'] = d['steps'][:1]
        propose(d, owner='operator')
        recipe = get_recipe(name='add_internal_fqdn')
        approve(recipe['id'], 1, actor='operator')
        out = start_run(recipe['id'], {'fqdn': 'app.example.test',
                                       'target_ip': '192.0.2.10', 'port': 443},
                        agent='', session_id='s1')
        assert out['status'] == 'pending'
        assert out['plan_id']

    def test_an_operator_run_with_an_ssh_step_is_refused(self, env, run_env):
        recipe_id = approved()
        with pytest.raises(RecipeError) as exc:
            start_run(recipe_id, {'fqdn': 'app.example.test',
                                  'target_ip': '192.0.2.10', 'port': 443},
                      agent='', session_id='s1')
        assert exc.value.code == 'recipe_operator_ssh'

    def test_the_plan_items_are_recipe_sourced(self, env, run_env):
        recipe_id = approved()
        out = start_run(recipe_id, {'fqdn': 'app.example.test',
                                    'target_ip': '192.0.2.10', 'port': 443},
                        agent='hermes', session_id='s1')
        plan = get_plan(out['plan_id'])
        assert plan['origin'] == 'recipe'
        for item in plan['items']:
            assert item['source'] == 'recipe'
            assert item['recipe_run_id'] == out['run_id']
        kinds = [item['kind'] for item in plan['items']]
        assert 'mcp' in kinds and 'ssh' in kinds

    def test_ordinary_claims_never_match_a_recipe_item(self, env, run_env):
        recipe_id = approved()
        out = start_run(recipe_id, {'fqdn': 'app.example.test',
                                    'target_ip': '192.0.2.10', 'port': 443},
                        agent='hermes', session_id='s1')
        plan = get_plan(out['plan_id'])
        _approve_plan(plan['id'])
        ssh_item = [i for i in plan['items'] if i['kind'] == 'ssh'][0]
        assert claim_plan_item('s1', HOST, ssh_item['command']) is None
        mcp_item = [i for i in plan['items'] if i['kind'] == 'mcp'][0]
        assert claim_mcp_plan_item('s1', mcp_item['integration'], mcp_item['tool'],
                                   mcp_item['args_hash'], owner='hermes') is None

    def test_the_engine_claims_by_run_and_step(self, env, run_env):
        recipe_id = approved()
        out = start_run(recipe_id, {'fqdn': 'app.example.test',
                                    'target_ip': '192.0.2.10', 'port': 443},
                        agent='hermes', session_id='s1')
        _approve_plan(out['plan_id'])
        assert claim_recipe_item(out['run_id'], '1')
        assert claim_recipe_item(out['run_id'], '1') is None
        assert claim_recipe_item(out['run_id'] + 1, '1') is None

    def test_a_second_run_in_one_session_waits(self, env, run_env):
        recipe_id = approved()
        params = {'fqdn': 'app.example.test', 'target_ip': '192.0.2.10',
                  'port': 443}
        first = start_run(recipe_id, params, agent='hermes', session_id='s1')
        with pytest.raises(RecipeError) as exc:
            start_run(recipe_id, params, agent='hermes', session_id='s1')
        assert exc.value.code == 'plan_waiting'
        assert get_plan(first['plan_id']) is not None

    def test_auto_run_is_locked(self, env):
        from db.recipes import set_auto_run
        with pytest.raises(Exception) as exc:
            set_auto_run(1, True)
        assert getattr(exc.value, 'code', '') == 'auto_run_locked'
        with pytest.raises(RecipeError):
            validate_document(doc(auto_run=True))


# ── execution ──────────────────────────────────────────────────────────

class TestExecution:
    def _run(self, env, run_env, **kwargs):
        recipe_id = approved()
        out = start_run(recipe_id, {'fqdn': 'app.example.test',
                                    'target_ip': '192.0.2.10', 'port': 443},
                        agent='hermes', session_id='s1')
        _approve_plan(out['plan_id'])
        execute_run(out['run_id'])
        return get_run(out['run_id']), out, recipe_id

    def test_a_successful_run(self, env, run_env):
        run, out, _ = self._run(env, run_env)
        assert run['status'] == 'ok'
        states = {step['step_id']: step['status'] for step in run['steps']}
        assert states['1'] == 'ok'
        assert states['2'] == 'ok'
        # The create call carried access_list_id 7, never the label object.
        create = [c for c in run_env.tool_calls if c[1] == 'create_proxy_host'][0]
        assert create[2]['body']['access_list_id'] == 7
        assert 'access_list' not in create[2]['body']
        assert run_env.ssh_calls and run_env.ssh_calls[0][0] == 'getent hosts app.example.test'

    def test_a_failed_check_stops_the_run(self, env, run_env, monkeypatch):
        def bad_tool(run, integration, tool, args, step_id):
            if tool == 'proxy_hosts':
                if step_id.endswith(':pre'):
                    return ({'items': []}, 200, {})
                return (make_host_result(forward_host='192.0.2.99'), 200, {})
            return run_env.tool(run, integration, tool, args, step_id)
        monkeypatch.setattr('core.recipes._run_tool_call', bad_tool)
        run, out, _ = self._run(env, run_env)
        assert run['status'] == 'failed'
        assert run['failed_step'] == '1'
        states = {step['step_id']: step['status'] for step in run['steps']}
        assert states['1'] == 'ok' and states['2'] == 'skipped'

    def test_a_precheck_that_passes_skips_the_create(self, env, run_env, monkeypatch):
        def existing(run, integration, tool, args, step_id):
            if tool == 'proxy_hosts':
                return (make_host_result(), 200, {})
            return run_env.tool(run, integration, tool, args, step_id)
        monkeypatch.setattr('core.recipes._run_tool_call', existing)
        run, out, _ = self._run(env, run_env)
        step = [s for s in run['steps'] if s['step_id'] == '1'][0]
        assert step['status'] == 'already-present' and step['created'] == 0
        assert not [c for c in run_env.tool_calls if c[1] == 'create_proxy_host']

    def test_the_public_access_list_reaches_the_run_as_zero(self, env, run_env):
        d = doc()
        d['steps'][0]['call']['args']['body']['access_list'] = {'public': True}
        propose(d, owner='operator')
        recipe = get_recipe(name='add_internal_fqdn')
        approve(recipe['id'], 1)
        out = start_run(recipe['id'], {'fqdn': 'app.example.test',
                                       'target_ip': '192.0.2.10', 'port': 443},
                        agent='hermes', session_id='s1')
        _approve_plan(out['plan_id'])
        execute_run(out['run_id'])
        create = [c for c in run_env.tool_calls if c[1] == 'create_proxy_host'][0]
        assert create[2]['body']['access_list_id'] == 0

    def test_a_public_host_fails_the_access_list_check(self, env, run_env,
                                                      monkeypatch):
        def public(run, integration, tool, args, step_id):
            if tool == 'proxy_hosts' and step_id.endswith(':check'):
                return (make_host_result(access_list_id=0), 200, {})
            return run_env.tool(run, integration, tool, args, step_id)
        monkeypatch.setattr('core.recipes._run_tool_call', public)
        run, out, _ = self._run(env, run_env)
        assert run['status'] == 'failed'
        assert run['failed_step'] == '1'

    def test_the_run_twice_does_not_execute_twice(self, env, run_env):
        recipe_id = approved()
        out = start_run(recipe_id, {'fqdn': 'app.example.test',
                                    'target_ip': '192.0.2.10', 'port': 443},
                        agent='hermes', session_id='s1')
        _approve_plan(out['plan_id'])
        execute_run(out['run_id'])
        before = len(run_env.tool_calls)
        execute_run(out['run_id'])
        assert len(run_env.tool_calls) == before


# ── rollback ───────────────────────────────────────────────────────────

class TestRollback:
    def _finished(self, env, run_env):
        recipe_id = approved()
        out = start_run(recipe_id, {'fqdn': 'app.example.test',
                                    'target_ip': '192.0.2.10', 'port': 443},
                        agent='hermes', session_id='s1')
        _approve_plan(out['plan_id'])
        execute_run(out['run_id'])
        return out['run_id']

    def test_only_created_steps_are_undone(self, env, run_env):
        run_id = self._finished(env, run_env)
        result = rollback(run_id, agent='hermes', session_id='s1')
        assert result['status'] == 'pending'
        _approve_plan(result['plan_id'])
        execute_rollback(result['rollback_run_id'])
        rb = get_run(result['rollback_run_id'])
        assert rb['status'] == 'rolled_back'
        deletes = [c for c in run_env.tool_calls if c[1] == 'delete_proxy_host']
        assert deletes and deletes[0][2] == {'id': 7}
        # V4 re-read happened before the delete.
        assert [c for c in run_env.tool_calls if c[1] == 'proxy_host']

    def test_a_changed_target_stops_the_rollback(self, env, run_env, monkeypatch):
        run_id = self._finished(env, run_env)
        result = rollback(run_id, agent='hermes', session_id='s1')
        _approve_plan(result['plan_id'])

        def changed(run, integration, tool, args, step_id):
            if tool == 'proxy_host':
                return (make_host_result(domain_names=['other.example.test']),
                        200, {})
            return run_env.tool(run, integration, tool, args, step_id)
        monkeypatch.setattr('core.recipes._run_tool_call', changed)
        execute_rollback(result['rollback_run_id'])
        rb = get_run(result['rollback_run_id'])
        assert rb['status'] != 'rolled_back'
        assert not [c for c in run_env.tool_calls if c[1] == 'delete_proxy_host']

    def test_nothing_to_roll_back(self, env, run_env):
        recipe_id = approved()
        out = start_run(recipe_id, {'fqdn': 'app.example.test',
                                    'target_ip': '192.0.2.10', 'port': 443},
                        agent='hermes', session_id='s1')
        _approve_plan(out['plan_id'])
        execute_run(out['run_id'])
        # Mark every step as pre-existing, then try a rollback.
        with db_conn() as conn:
            conn.execute("UPDATE recipe_run_steps SET created = 0")
            conn.commit()
        with pytest.raises(RecipeError) as exc:
            rollback(out['run_id'])
        assert exc.value.code == 'rollback_nothing'


# ── the F10 leftover nit ───────────────────────────────────────────────

class TestMcpDisplayNit:
    def test_an_empty_display_with_args_is_refused(self, env, monkeypatch):
        import db.session_plans as plans
        create_integration('ha', 'https://192.0.2.30/api', 'header',
                           'tok', auth_header_name='Authorization', kind='ha')
        create_tool('ha', 'ha_addon_action',
                    'Restart an add-on.', 'POST',
                    '/api/hassio/addons/{slug}/{action}',
                    [{'name': 'slug'}, {'name': 'action'}],
                    '{"ok": true}', always_gate=True, read_only=False)
        monkeypatch.setattr(plans, '_mcp_display', lambda *a: ('', ''))
        with pytest.raises(PlanError) as exc:
            create_plan('s1', [{'kind': 'mcp', 'integration': 'ha',
                                'tool': 'ha_addon_action',
                                'args': {'slug': 'core_x', 'action': 'restart'}}])
        assert exc.value.code == 'invalid_args'


# ── API and MCP tools ─────────────────────────────────────────────────

class TestApi:
    def test_the_list_and_detail_need_a_session(self, client, env):
        assert client.get('/api/recipes').status_code == 401
        assert client.get('/api/recipe-runs').status_code == 401

    def test_list_detail_and_approve(self, auth_client, env):
        propose(doc(), owner='operator')
        listing = auth_client.get('/api/recipes').json()
        assert listing and listing[0]['status'] == 'draft'
        rid = listing[0]['id']
        detail = auth_client.get('/api/recipes/%d' % rid).json()
        assert detail['document']['name'] == 'add_internal_fqdn'
        ok = auth_client.post('/api/recipes/%d/versions/1/approve' % rid)
        assert ok.status_code == 200
        assert auth_client.get('/api/recipes/%d' % rid).json()['status'] == 'approved'

    def test_preview_and_run(self, auth_client, env, run_env):
        rid = approved()
        preview = auth_client.post('/api/recipes/%d/preview' % rid, json={
            'parameters': {'fqdn': 'app.example.test', 'target_ip': '192.0.2.10',
                           'port': 443}})
        assert preview.status_code == 200, preview.text
        steps = preview.json()['steps']
        assert steps[1]['command'] == 'getent hosts app.example.test'
        assert steps[0]['args']['body']['access_list_id'] == 7
        # AW1: the dashboard run route starts as the operator, so it may run a
        # tool-only recipe; the SSH-bearing one is refused with a clear reason.
        refused = auth_client.post('/api/recipes/%d/run' % rid, json={
            'parameters': {'fqdn': 'app.example.test', 'target_ip': '192.0.2.10',
                           'port': 443}})
        assert refused.status_code == 400
        assert 'operator-started run cannot use SSH steps' in refused.text
        rid = approved_tool_only()
        run = auth_client.post('/api/recipes/%d/run' % rid, json={
            'parameters': {'fqdn': 'app.example.test', 'target_ip': '192.0.2.10',
                           'port': 443}})
        assert run.status_code == 200, run.text
        assert run.json()['plan_id']
        runs = auth_client.get('/api/recipe-runs?recipe_id=%d' % rid).json()
        assert runs and runs[0]['status'] == 'pending'

    def test_rollback_route(self, auth_client, env, run_env):
        rid = approved_tool_only()
        run = auth_client.post('/api/recipes/%d/run' % rid, json={
            'parameters': {'fqdn': 'app.example.test', 'target_ip': '192.0.2.10',
                           'port': 443}}).json()
        approve_plan(run['plan_id'])
        execute_run(run['run_id'])
        body = auth_client.post('/api/recipe-runs/%d/rollback' % run['run_id']).json()
        assert body['plan_id']


class TestMcp:
    def test_the_recipe_tools_are_registered(self, env):
        from core.mcp_server import mcp
        names = {tool.name for tool in asyncio.run(mcp.list_tools())}
        for name in ('recipe_list', 'recipe_get', 'recipe_propose',
                     'recipe_run', 'recipe_run_status', 'recipe_rollback'):
            assert name in names, name

    def test_propose_then_run(self, env, run_env):
        from core.agent_context import reset_current_agent, set_current_agent
        from core.mcp_server import recipe_propose_fn, recipe_run_fn
        token = set_current_agent({'name': 'hermes'})
        try:
            out = json.loads(recipe_propose_fn()(doc()))
            assert out['status'] == 'draft'
            refused = json.loads(recipe_run_fn()('add_internal_fqdn', {
                'fqdn': 'app.example.test', 'target_ip': '192.0.2.10',
                'port': 443}, 's1'))
            assert refused['error'] == 'recipe_not_approved'
            recipe = get_recipe(name='add_internal_fqdn')
            approve(recipe['id'], 1)
            out = json.loads(recipe_run_fn()('add_internal_fqdn', {
                'fqdn': 'app.example.test', 'target_ip': '192.0.2.10',
                'port': 443}, 's1'))
            assert out['plan_id']
        finally:
            reset_current_agent(token)


# ── the reserved SSH row (V5's delivery side) ─────────────────────────

class TestReservation:
    def test_the_row_is_handed_out_once(self, env):
        from db.requests import create_recipe_request, take_recipe_reservation
        rid = create_recipe_request(HOST, 'getent hosts app.example.test', 5, '2',
                                    session_id='s1', agent='hermes',
                                    run_limit='{"seconds": 60, "bytes": 1024}')
        assert take_recipe_reservation(HOST, 'getent hosts app.example.test',
                                       's1', 'hermes') == rid
        assert take_recipe_reservation(HOST, 'getent hosts app.example.test',
                                       's1', 'hermes') is None

    def test_a_direct_call_never_gets_the_row(self, env):
        from db.requests import create_recipe_request, take_recipe_reservation
        create_recipe_request(HOST, 'getent hosts app.example.test', 5, '2',
                              session_id='s1', agent='hermes')
        assert take_recipe_reservation(HOST, 'getent hosts app.example.test',
                                       's1', 'other-agent') is None
        assert take_recipe_reservation(HOST, 'getent hosts other.example.test',
                                       's1', 'hermes') is None

    def _post(self, client, headers, command, run_limits='v1'):
        import base64
        return client.post('/api/request', headers=headers, json={
            'target_ip': HOST,
            'encoded_command': base64.b64encode(command.encode()).decode(),
            'session_id': 's1', 'agent': 'hermes', 'force_jit': True,
            'run_limits': run_limits})

    def _headers(self):
        from db.gateways import set_gateway_token
        set_gateway_token(HOST, 'tok-20')
        return {'X-Gateway-Token': 'tok-20'}

    def test_receive_request_hands_the_row_to_the_gateway(self, client, env):
        from db.requests import create_recipe_request
        rid = create_recipe_request(HOST, 'getent hosts app.example.test', 5, '2',
                                    session_id='s1', agent='hermes',
                                    run_limit='{"seconds": 60, "bytes": 1024}')
        body = self._post(client, self._headers(),
                          'getent hosts app.example.test').json()
        assert body.get('approved') is True, body
        assert body.get('request_id') == rid
        assert body.get('run_limit') == {'seconds': 60, 'bytes': 1024}
        # A second identical call is an ordinary card, not the row again.
        again = self._post(client, self._headers(),
                           'getent hosts app.example.test').json()
        assert not again.get('approved')

    def test_without_the_limits_capability_nothing_is_handed_out(self, client, env):
        from db.requests import create_recipe_request, take_recipe_reservation
        create_recipe_request(HOST, 'getent hosts app.example.test', 5, '2',
                              session_id='s1', agent='hermes')
        body = self._post(client, self._headers(),
                          'getent hosts app.example.test', run_limits='').json()
        assert not body.get('approved')
        # The reservation is still there for a capable gateway.
        assert take_recipe_reservation(HOST, 'getent hosts app.example.test',
                                       's1', 'hermes')


class TestPlanGate:
    def test_a_run_waits_for_its_plan(self, env, run_env):
        recipe_id = approved()
        out = start_run(recipe_id, {'fqdn': 'app.example.test',
                                    'target_ip': '192.0.2.10', 'port': 443},
                        agent='hermes', session_id='s1')
        execute_run(out['run_id'])
        assert get_run(out['run_id'])['status'] == 'pending'
        assert not [c for c in run_env.tool_calls if c[3] != 'precondition']


# ── code round 1: AW1–AW4 ──────────────────────────────────────────────

import base64

from core import ssh_exec as sx


HOST_KEY = ('ssh-ed25519 ' + base64.b64encode(b'0' * 32).decode()
            + ' root@web01')


def ssh_doc(command='getent hosts {fqdn}', name='resolve_it'):
    """A recipe whose only step is an SSH command."""
    d = doc(name)
    d['steps'] = [d['steps'][1]]
    d['steps'][0]['ssh']['command'] = command
    d['preconditions'] = []
    return d


def _ready_gateway():
    from db.gateways import (
        set_gateway_ssh_capabilities, set_gateway_ssh_host_key,
        set_gateway_token,
    )
    set_gateway_token(HOST, 'eshu-test-token')
    set_gateway_ssh_host_key(HOST, HOST_KEY)
    set_gateway_ssh_capabilities(HOST, sx.AGENT_FORMAT, sx.TICKET_FORMAT)


def _stub_runner(monkeypatch, status='ran', exit_code=0):
    """Stub only the SSH runner, so `ssh_exec` runs for real."""
    calls = []

    def fake(gateway, command_line, known_hosts, key_path, timeout):
        calls.append({'gateway': gateway, 'command_line': command_line})
        envelope = {'status': status, 'request_id': 70,
                    'exit_code': exit_code,
                    'output_b64': base64.b64encode(b'192.0.2.10\n').decode(),
                    'truncated': False, 'messages': []}
        return {'rc': 0,
                'stdout': sx.ENVELOPE_PREFIX + json.dumps(envelope),
                'stderr': '', 'timed_out': False}

    monkeypatch.setattr(sx, 'run_ssh', fake)
    monkeypatch.setattr(sx.ssh_keys, 'write_known_hosts',
                        lambda ip, key: '/tmp/kh.' + ip)
    monkeypatch.setattr(sx.ssh_keys, 'ensure_key_pair', lambda: '/tmp/key')
    return calls


def _run_ssh_recipe(monkeypatch, command='getent hosts {fqdn}',
                    tool_stub=None):
    upsert_session('s1', agent='hermes', goal='resolve the name')
    d = ssh_doc(command)
    propose(d, owner='operator')
    recipe = get_recipe(name=d['name'])
    approve(recipe['id'], 1, actor='operator')
    out = start_run(recipe['id'], {'fqdn': 'app.example.test',
                                   'target_ip': '192.0.2.10', 'port': 443},
                    agent='hermes', session_id='s1')
    approve_plan(out['plan_id'])
    execute_run(out['run_id'])
    return get_run(out['run_id']), out


def _hermes():
    return get_agent_by_name('hermes')


class TestAgentIdentity:
    """AW1: the SSH step runs as the real agent, through the real ssh_exec."""

    def test_an_allowed_host_reaches_the_gateway(self, env, monkeypatch):
        _ready_gateway()
        calls = _stub_runner(monkeypatch)
        run, out = _run_ssh_recipe(monkeypatch)
        assert run['status'] == 'ok', (run['failed_step'],
                                       [st['error'] for st in run['steps']])
        assert len(calls) == 1
        assert 'getent hosts app.example.test' in calls[0]['command_line']
        assert 'hermes' in calls[0]['command_line']
        step = [s for s in run['steps'] if s['step_id'] == '2'][0]
        assert step['status'] == 'ok'
        assert step['request_id']
        # The reservation was handed to the gateway in the stub's envelope.
        row = get_request_row(step['request_id'])
        assert row['status'] == 'approved'
        assert row['recipe_run_id'] == out['run_id']

    def test_a_host_outside_the_agents_allowlist_is_refused(self, env,
                                                            monkeypatch):
        _ready_gateway()
        calls = _stub_runner(monkeypatch)
        set_agent_policy(_hermes()['id'],
                         ssh_hosts_allow=['other.example.test'])
        run, out = _run_ssh_recipe(monkeypatch)
        assert run['status'] == 'failed'
        assert run['failed_step'] == '2'
        assert calls == []
        step = [s for s in run['steps'] if s['step_id'] == '2'][0]
        assert 'no SSH access' in step['error']
        # AW3: the reservation the engine made is expired, not claimable.
        row = get_request_row(step['request_id'])
        assert row['status'] == 'expired'
        assert row['recipe_handed'] == 0

    def test_a_read_only_agent_may_not_run_a_write_step(self, env, monkeypatch):
        _ready_gateway()
        calls = _stub_runner(monkeypatch)
        set_agent_policy(_hermes()['id'], ssh_hosts_allow=[GW],
                         ssh_mode='read_only')
        run, out = _run_ssh_recipe(monkeypatch,
                                   command='systemctl restart nginx')
        assert run['status'] == 'failed'
        assert calls == []
        step = [s for s in run['steps'] if s['step_id'] == '2'][0]
        assert 'read-only' in step['error'].lower()
        assert get_request_row(step['request_id'])['status'] == 'expired'

    def test_an_operator_run_cannot_use_ssh(self, env, monkeypatch):
        with pytest.raises(RecipeError) as exc:
            start_run(approved(), {'fqdn': 'app.example.test',
                                   'target_ip': '192.0.2.10', 'port': 443},
                      agent='', session_id='s1')
        assert exc.value.code == 'recipe_operator_ssh'


class TestAgentStanding:
    """AW2: the agent's standing is re-read before every step."""

    def test_a_revoked_agent_stops_the_second_step(self, env, run_env):
        recipe_id = approved()
        real = run_env.tool

        def first_then_revoke(run, integration, tool, args, step_id):
            value = real(run, integration, tool, args, step_id)
            if tool == 'create_proxy_host':
                revoke_agent_token(_hermes()['id'])
            return value

        import core.recipes as recipes
        original = recipes._run_tool_call
        recipes._run_tool_call = first_then_revoke
        try:
            out = start_run(recipe_id, {'fqdn': 'app.example.test',
                                        'target_ip': '192.0.2.10',
                                        'port': 443}, agent='hermes',
                            session_id='s1')
            approve_plan(out['plan_id'])
            execute_run(out['run_id'])
        finally:
            recipes._run_tool_call = original
        run = get_run(out['run_id'])
        assert run['status'] == 'stopped'
        assert run['failed_step'] == '2'
        assert run_env.ssh_calls == []
        step = [s for s in run['steps'] if s['step_id'] == '2'][0]
        assert step['status'] == 'failed'
        assert 'revoked' in step['error']

    def test_a_tightened_tool_allowlist_stops_the_next_step(self, env,
                                                            run_env):
        d = doc('two_tools')
        d['preconditions'] = []
        d['steps'] = [
            d['steps'][0],
            {'id': '2', 'kind': 'tool', 'title': 'DNS records',
             'call': {'integration': 'Pi-hole', 'tool': 'local_dns_records',
                      'args': {}},
             'check': None, 'undo': None},
        ]
        propose(d, owner='operator')
        recipe = get_recipe(name='two_tools')
        approve(recipe['id'], 1, actor='operator')
        set_agent_policy(_hermes()['id'], tools_mode='allowlist',
                         tools=['nginx_proxy_manager_create_proxy_host'])
        out = start_run(recipe['id'], {'fqdn': 'app.example.test',
                                       'target_ip': '192.0.2.10',
                                       'port': 443}, agent='hermes',
                        session_id='s1')
        approve_plan(out['plan_id'])
        execute_run(out['run_id'])
        run = get_run(out['run_id'])
        assert run['status'] == 'stopped'
        assert run['failed_step'] == '2'
        step = [s for s in run['steps'] if s['step_id'] == '2'][0]
        assert 'allowlist' in step['error']

    def test_the_standing_check_precedes_the_claim(self, env, run_env):
        """A revoked agent must not spend the step's approval."""
        recipe_id = approved()
        revoke_agent_token(_hermes()['id'])
        out = start_run(recipe_id, {'fqdn': 'app.example.test',
                                    'target_ip': '192.0.2.10', 'port': 443},
                        agent='hermes', session_id='s1')
        approve_plan(out['plan_id'])
        execute_run(out['run_id'])
        run = get_run(out['run_id'])
        assert run['status'] == 'stopped'
        plan = get_plan(out['plan_id'])
        assert all(item['uses'] == 0 for item in plan['items'])


class TestReservationAndPlanItems:
    """AW3/AW4."""

    def test_an_ssh_plan_item_must_name_its_host(self, env):
        with pytest.raises(PlanError) as exc:
            create_recipe_plan('s1', 1, [
                {'kind': 'ssh', 'command': 'uptime', 'step_id': '1'}])
        assert exc.value.code == 'recipe_item_host'
        with pytest.raises(PlanError) as exc:
            create_recipe_plan('s1', 1, [
                {'kind': 'ssh', 'command': 'uptime', 'host': '*',
                 'step_id': '1'}])
        assert exc.value.code == 'recipe_item_host'
        plan = create_recipe_plan('s1', 1, [
            {'kind': 'ssh', 'command': 'uptime', 'host': GW, 'step_id': '1'}])
        assert plan['items'] == 1

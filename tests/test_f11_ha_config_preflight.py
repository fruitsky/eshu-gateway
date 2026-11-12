"""F11 preflight: the parsed-YAML refusals (AL4, AQ2, AT1, BC1) and H4.

Synthetic data only. No SSH: these are the dashboard-side rules that run
before anything is queued.
"""
import pytest

from core.ha_config_preflight import (
    ConfigError, MAX_CONTENT, check_write_path, to_root_path, unified_diff,
    validate_write,
)


def _code(fn, *args):
    with pytest.raises(ConfigError) as exc:
        fn(*args)
    return exc.value.code


# ── the target path ────────────────────────────────────────────────────

class TestWritePath:
    @pytest.mark.parametrize('path', [
        '/config/configuration.yaml',
        '/homeassistant/configuration.yaml',
        '/config/packages/house/a.yaml',
        '/homeassistant/packages/a.yml',
    ])
    def test_accepted(self, path):
        assert check_write_path(path).startswith('/homeassistant/')

    @pytest.mark.parametrize('path', [
        '/config/.storage/core.config_entries',
        '/config/../etc/passwd',
        '/config/a/../b.yaml',
        '/etc/eshu-key.yaml',
        '/config/esphome/x.yaml',
        '/config/custom_components/x.yaml',
        '/config/x.txt',
        '/config/packages',
        '/config//x.yaml',
        'config/x.yaml',
        '',
    ])
    def test_refused(self, path):
        assert _code(check_write_path, path) == 'config_path_refused'

    def test_secrets_is_never_written(self):
        assert _code(check_write_path, '/config/secrets.yaml') == \
            'config_secret_refused'
        assert _code(check_write_path, '/homeassistant/secrets.yaml') == \
            'config_secret_refused'

    def test_config_maps_to_the_addon_view(self):
        assert to_root_path('/config/x.yaml') == '/homeassistant/x.yaml'


# ── includes: AT1 and BC1 ─────────────────────────────────────────────

class TestIncludes:
    def _check(self, body, path='/config/configuration.yaml'):
        return validate_write(path, body)

    def test_a_top_level_file_is_accepted(self):
        out = self._check('light: !include lights.yaml\n')
        assert out['includes'][0]['resolved'] == '/homeassistant/lights.yaml'

    def test_a_packages_file_is_accepted(self):
        out = self._check('light: !include packages/a/b.yaml\n')
        assert out['includes'][0]['resolved'] == \
            '/homeassistant/packages/a/b.yaml'

    @pytest.mark.parametrize('target', [
        'esphome/x.yaml',
        'custom_components/x.yaml',
        'www/x.yaml',
        './x.yaml',
        '../outside.yaml',
        '.storage/x.yaml',
    ])
    def test_esphome_and_escapes_are_refused(self, target):
        assert _code(validate_write, '/config/configuration.yaml',
                     'x: !include %s\n' % target) == 'config_include_refused'

    @pytest.mark.parametrize('target', [
        '/homeassistant/packages/a.yaml',
        '/homeassistant/x.yaml',
        '/etc/eshu-key.yaml',
    ])
    def test_an_absolute_include_is_refused(self, target):
        # BC1: the dashboard sees /homeassistant, HA Core sees /config.
        assert _code(validate_write, '/config/configuration.yaml',
                     'x: !include %s\n' % target) == 'config_include_refused'

    def test_secrets_yaml_is_refused_with_its_own_code(self):
        assert _code(validate_write, '/config/configuration.yaml',
                     'x: !include secrets.yaml\n') == \
            'config_secret_include_refused'

    def test_a_directory_include_of_packages_is_accepted(self):
        out = self._check('homeassistant:\n  packages: !include_dir_named '
                          'packages\n')
        assert out['includes'][0]['kind'] == 'dir'
        assert out['includes'][0]['resolved'] == '/homeassistant/packages'

    def test_a_directory_include_of_a_subpackage_is_accepted(self):
        out = self._check('x: !include_dir_merge_named packages/house\n')
        assert out['includes'][0]['resolved'] == '/homeassistant/packages/house'

    @pytest.mark.parametrize('target', ['.', './', '/homeassistant',
                                        '/homeassistant/',
                                        'packages/..', 'esphome'])
    def test_a_directory_include_of_the_root_is_refused(self, target):
        assert _code(validate_write, '/config/configuration.yaml',
                     'x: !include_dir_named %s\n' % target) == \
            'config_include_refused'

    def test_the_include_is_resolved_against_the_including_file(self):
        # HA resolves relative to the file being written, not always the root.
        out = validate_write('/config/packages/a.yaml',
                             'x: !include b.yaml\n')
        assert out['includes'][0]['resolved'] == '/homeassistant/packages/b.yaml'


# ── tags, env vars, invalid YAML ──────────────────────────────────────

class TestTagsAndYaml:
    def test_a_tag_directive_is_refused(self):
        assert _code(validate_write, '/config/x.yaml',
                     '%TAG !e! tag:example.com,2020:\n---\na: 1\n') == \
            'config_tag_refused'

    def test_a_verbatim_tag_is_refused(self):
        assert _code(validate_write, '/config/x.yaml',
                     'a: !<tag:example.com,2020:x> 1\n') == \
            'config_tag_refused'

    def test_env_var_is_refused(self):
        assert _code(validate_write, '/config/x.yaml',
                     'a: !env_var SECRET\n') == 'config_env_var_refused'

    def test_tab_indentation_is_refused(self):
        # The structural scan cannot parse YAML; a tab indent is the one
        # invalid form it can name. `ha core check` is the validity gate.
        assert _code(validate_write, '/config/x.yaml',
                     'a:\n\tb: 1\n') == 'config_yaml_invalid'

    def test_secret_and_input_tags_are_allowed(self):
        out = validate_write('/config/x.yaml',
                             'a: !secret a_key\nb: !input an_input\n')
        assert out['includes'] == []


# ── size and H4 ───────────────────────────────────────────────────────

class TestSizeAndRiskyKeys:
    def test_an_oversized_file_is_refused(self):
        assert _code(validate_write, '/config/x.yaml',
                     'a: ' + 'b' * (MAX_CONTENT + 1)) == 'config_too_large'

    @pytest.mark.parametrize('key', ['shell_command', 'command_line',
                                     'python_script', 'rest_command', 'http',
                                     'packages'])
    def test_each_risky_key_is_named(self, key):
        out = validate_write('/config/x.yaml', '%s:\n  a: 1\n' % key)
        assert key in out['risky']

    @pytest.mark.parametrize('spelling', [
        'shell_command:',
        'shell_command :',
        '"shell_command":',
        "'shell_command':",
    ])
    def test_quoted_and_spaced_spellings_are_found(self, spelling):
        out = validate_write('/config/x.yaml', spelling + '\n  a: 1\n')
        assert 'shell_command' in out['risky']

    def test_a_comment_is_not_a_key(self):
        out = validate_write('/config/x.yaml', '# shell_command: rm -rf /\na: 1\n')
        assert 'shell_command' not in out['risky']

    def test_a_key_inside_a_block_scalar_is_not_a_key(self):
        out = validate_write('/config/x.yaml',
                             'script:\n  - shell_command: "x"\n'
                             'note: |\n  shell_command: not a key\n')
        assert out['risky'] == []

    def test_the_auth_providers_key_is_named(self):
        out = validate_write('/config/x.yaml',
                             'homeassistant:\n  auth_providers:\n    - type: x\n')
        assert 'homeassistant.auth_providers' in out['risky']

    def test_the_digest_and_size_are_returned(self):
        out = validate_write('/config/x.yaml', 'a: 1\n')
        assert out['size'] == 5
        assert len(out['sha256']) == 64

    def test_the_diff_names_only_the_path(self):
        diff = unified_diff('/config/x.yaml', 'a: 1\n', 'a: 2\n')
        assert '-a: 1' in diff and '+a: 2' in diff

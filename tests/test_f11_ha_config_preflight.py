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

    def test_an_unchanged_themes_include_is_allowed(self):
        current = ('themes: !include_dir_merge_named themes\n'
                   'default_config:\n')
        proposed = current + 'light:\n'
        out = validate_write('/config/configuration.yaml', proposed,
                             current=current)
        assert out['includes'][0]['resolved'] == '/homeassistant/themes'

    def test_moving_an_unchanged_themes_include_is_allowed(self):
        current = ('themes: !include_dir_merge_named themes\n'
                   'a: 1\n')
        proposed = ('a: 1\n'
                    'themes: !include_dir_merge_named themes\n')
        assert validate_write('/config/configuration.yaml', proposed,
                              current=current)['includes']

    def test_adding_a_new_themes_include_is_refused(self):
        assert _code(validate_write, '/config/configuration.yaml',
                     'themes: !include_dir_merge_named themes\n',
                     'a: 1\n') == 'config_include_refused'

    def test_changing_themes_to_a_dot_directory_is_refused(self):
        current = 'themes: !include_dir_merge_named themes\n'
        assert _code(validate_write, '/config/configuration.yaml',
                     'themes: !include_dir_merge_named .storage\n',
                     current) == 'config_include_refused'

    def test_changing_the_include_tag_is_refused(self):
        current = 'themes: !include_dir_merge_named themes\n'
        assert _code(validate_write, '/config/configuration.yaml',
                     'themes: !include_dir_named themes\n',
                     current) == 'config_include_refused'

    def test_duplicating_an_unchanged_themes_include_is_refused(self):
        line = 'themes: !include_dir_merge_named themes\n'
        assert _code(validate_write, '/config/configuration.yaml', line + line,
                     line) == 'config_include_refused'

    def test_the_packages_rule_still_applies_to_new_includes(self):
        current = ('homeassistant:\n'
                   '  packages: !include_dir_named packages\n')
        proposed = current + 'x: !include_dir_named esphome\n'
        assert _code(validate_write, '/config/configuration.yaml', proposed,
                     current) == 'config_include_refused'

    def test_an_unchanged_packages_include_is_still_allowed(self):
        current = ('homeassistant:\n'
                   '  packages: !include_dir_named packages\n')
        out = validate_write('/config/configuration.yaml', current,
                             current=current)
        assert out['includes'][0]['resolved'] == '/homeassistant/packages'


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


class TestGrandfatheredLines:
    def test_the_operator_shape_passes_and_is_reported(self):
        current = ('frontend:\n'
                   '  themes: !include_dir_merge_named themes\n')
        out = validate_write('/config/configuration.yaml',
                             current + '\n# appended comment\n',
                             current=current)
        assert out['includes'][0]['resolved'] == '/homeassistant/themes'
        assert out['grandfathered'] == [{
            'kind': 'dir', 'target': 'themes',
            'resolved': '/homeassistant/themes',
            'line': '  themes: !include_dir_merge_named themes',
            'parent': ('frontend:',),
        }]

    def test_a_normal_packages_include_is_not_called_grandfathered(self):
        current = ('homeassistant:\n'
                   '  packages: !include_dir_named packages\n')
        out = validate_write('/config/configuration.yaml', current,
                             current=current)
        assert out['grandfathered'] == []

    def test_reindenting_the_same_include_is_refused(self):
        current = ('frontend:\n'
                   '  themes: !include_dir_merge_named themes\n')
        proposed = ('frontend:\n'
                    '    themes: !include_dir_merge_named themes\n')
        assert _code(validate_write, '/config/configuration.yaml', proposed,
                     current) == 'config_include_refused'

    def test_moving_the_same_line_under_another_key_is_refused(self):
        line = '  themes: !include_dir_merge_named themes\n'
        assert _code(validate_write, '/config/configuration.yaml',
                     'other:\n' + line,
                     'frontend:\n' + line) == 'config_include_refused'

    def test_duplicating_the_grandfathered_line_is_refused(self):
        line = '  themes: !include_dir_merge_named themes\n'
        current = 'frontend:\n' + line
        assert _code(validate_write, '/config/configuration.yaml',
                     current + line, current) == 'config_include_refused'

    def test_a_new_anchor_in_the_file_is_refused(self):
        current = ('frontend:\n'
                   '  themes: !include_dir_merge_named themes\n')
        proposed = current + 'base: &t 1\n'
        assert _code(validate_write, '/config/configuration.yaml', proposed,
                     current) == 'config_anchor_refused'

    def test_a_new_alias_is_refused(self):
        current = ('frontend:\n'
                   '  themes: !include_dir_merge_named themes\n'
                   'base: &t 1\n')
        proposed = current + 'other: *t\n'
        assert _code(validate_write, '/config/configuration.yaml', proposed,
                     current) == 'config_anchor_refused'

    def test_a_new_merge_key_is_refused(self):
        current = ('frontend:\n'
                   '  themes: !include_dir_merge_named themes\n')
        proposed = current + 'other:\n  <<: {a: 1}\n'
        assert _code(validate_write, '/config/configuration.yaml', proposed,
                     current) == 'config_anchor_refused'

    def test_existing_anchors_and_aliases_are_counted_not_refused(self):
        current = ('frontend:\n'
                   '  themes: !include_dir_merge_named themes\n'
                   'base: &t 1\nother: *t\n')
        assert validate_write('/config/configuration.yaml',
                              current + '# still here\n',
                              current=current)['grandfathered']

    def test_an_unchanged_secrets_include_is_still_refused(self):
        current = 'x: !include secrets.yaml\n'
        assert _code(validate_write, '/config/configuration.yaml', current,
                     current) == 'config_secret_include_refused'

    def test_an_unchanged_absolute_include_is_still_refused(self):
        current = 'x: !include /homeassistant/x.yaml\n'
        assert _code(validate_write, '/config/configuration.yaml', current,
                     current) == 'config_include_refused'

    def test_an_unchanged_dot_segment_include_is_still_refused(self):
        current = 'x: !include .storage/x.yaml\n'
        assert _code(validate_write, '/config/configuration.yaml', current,
                     current) == 'config_include_refused'

    def test_a_new_file_has_no_grandfathered_include(self):
        assert _code(validate_write, '/config/configuration.yaml',
                     'frontend:\n'
                     '  themes: !include_dir_merge_named themes\n',
                     '') == 'config_include_refused'


def test_an_unchanged_anchored_include_is_refused():
    current = ('frontend:\n'
               '  themes: &t !include_dir_merge_named themes\n')
    assert _code(validate_write, '/config/configuration.yaml', current,
                 current) == 'config_anchor_refused'


class TestListShapedFiles:
    def test_an_include_on_a_line_after_a_list_block_is_checked(self):
        body = ('- alias: x\n'
                '  trigger: !include .storage/x.yaml\n')
        assert _code(validate_write, '/config/automations.yaml', body) == \
            'config_include_refused'

    def test_an_env_var_on_a_line_after_a_list_block_is_checked(self):
        body = ('- alias: x\n'
                '  trigger: !env_var SECRET\n')
        assert _code(validate_write, '/config/automations.yaml', body) == \
            'config_env_var_refused'

    def test_a_secrets_include_after_a_list_block_is_checked(self):
        body = ('- alias: x\n'
                '  trigger: !include secrets.yaml\n')
        assert _code(validate_write, '/config/automations.yaml', body) == \
            'config_secret_include_refused'


class TestSameIndentLists:
    def test_the_operator_shape_passes(self):
        current = ('first:\n'
                   '- themes: !include_dir_merge_named themes\n'
                   'second:\n'
                   '- themes: !include_dir_merge_named themes\n')
        out = validate_write('/config/configuration.yaml', current,
                             current=current)
        assert [item['parent'] for item in out['grandfathered']] == [
            ('first:',), ('second:',)]

    def test_an_unchanged_include_cannot_move_to_the_other_key(self):
        current = ('first:\n'
                   '- themes: !include_dir_merge_named themes\n')
        proposed = ('second:\n'
                    '- themes: !include_dir_merge_named themes\n')
        assert _code(validate_write, '/config/configuration.yaml', proposed,
                     current) == 'config_include_refused'

    def test_two_keys_with_the_same_list_do_not_share_a_path(self):
        current = ('first:\n'
                   '- themes: !include_dir_merge_named themes\n'
                   'second:\n')
        proposed = ('first:\n'
                    'second:\n'
                    '- themes: !include_dir_merge_named themes\n')
        assert _code(validate_write, '/config/configuration.yaml', proposed,
                     current) == 'config_include_refused'


    def test_an_anchor_only_value_keeps_its_list(self):
        current = ('first: &a\n'
                   '- themes: !include_dir_merge_named themes\n')
        out = validate_write('/config/configuration.yaml', current,
                             current=current)
        assert [item['parent'] for item in out['grandfathered']] == [
            ('first: &a',)]

    def test_a_tag_only_value_keeps_its_list(self):
        current = ('first: !my_tag\n'
                   '- themes: !include_dir_merge_named themes\n')
        proposed = ('second: !my_tag\n'
                    '- themes: !include_dir_merge_named themes\n')
        assert _code(validate_write, '/config/configuration.yaml', proposed,
                     current) == 'config_include_refused'


class TestMultilineQuotes:
    def test_plain_flow_quotes_do_not_hide_an_include(self):
        body = ('x: [a.\', !include "secrets.yaml", b.\']\n')
        assert _code(validate_write, '/config/x.yaml', body) == \
            'config_secret_include_refused'

    def test_a_real_quoted_flow_item_keeps_its_hash_and_the_next_include(self):
        body = ('x: ["safe # !include secrets.yaml", '
                '!include "secrets.yaml"]\n')
        assert _code(validate_write, '/config/x.yaml', body) == \
            'config_secret_include_refused'

    def test_a_real_quoted_scalar_can_contain_include_looking_text(self):
        out = validate_write('/config/x.yaml',
                             'x: "safe # !include secrets.yaml"\n')
        assert out['includes'] == []


    def test_a_quoted_value_across_lines_is_refused(self):
        assert _code(validate_write, '/config/x.yaml',
                     'a: "one\n  two"\n') == 'config_yaml_multiline_quote'

    def test_a_multiline_quote_cannot_hide_a_secrets_include(self):
        body = ('a: "text\n'
                '  b: !include secrets.yaml\n')
        assert _code(validate_write, '/config/x.yaml', body) == \
            'config_yaml_multiline_quote'

    def test_an_apostrophe_in_a_plain_scalar_is_not_a_quote(self):
        validate_write('/config/x.yaml', "note: it's fine\n")

    def test_an_apostrophe_in_a_block_scalar_is_not_scanned(self):
        body = ('note: |\n'
                "  it's fine\n"
                '  trigger: !include secrets.yaml\n')
        assert validate_write('/config/x.yaml', body)['includes'] == []

    def test_a_nested_block_scalar_is_skipped(self):
        body = ('- action: >\n'
                "   it's fine\n"
                '   trigger: !include secrets.yaml\n')
        assert validate_write('/config/automations.yaml', body)['includes'] == []

    def test_a_bare_list_block_scalar_is_skipped(self):
        body = ('- |\n'
                "  it's fine\n"
                '  !include secrets.yaml\n')
        assert validate_write('/config/automations.yaml', body)['includes'] == []

    def test_a_less_indented_block_scalar_line_is_refused(self):
        body = ('note: |\n'
                '    one\n'
                '  two\n')
        assert _code(validate_write, '/config/x.yaml', body) == \
            'config_yaml_invalid'


def test_a_nested_include_moved_to_another_parent_is_refused():
    line = '    themes: !include_dir_merge_named themes\n'
    current = ('frontend:\n'
               '  one:\n' + line)
    proposed = ('frontend:\n'
                '  two:\n' + line)
    assert _code(validate_write, '/config/configuration.yaml', proposed,
                 current) == 'config_include_refused'


def test_renaming_an_include_parent_is_refused():
    line = '    themes: !include_dir_merge_named themes\n'
    current = ('frontend:\n'
               '  one:\n' + line)
    proposed = ('frontend:\n'
                '  renamed:\n' + line)
    assert _code(validate_write, '/config/configuration.yaml', proposed,
                 current) == 'config_include_refused'


def test_moving_an_existing_anchor_onto_an_include_parent_is_refused():
    current = ('base: &t 1\n'
               'frontend:\n'
               '  themes: !include_dir_merge_named themes\n')
    proposed = ('frontend:\n'
                '  base: &t 1\n'
                '  themes: !include_dir_merge_named themes\n')
    assert _code(validate_write, '/config/configuration.yaml', proposed,
                 current) == 'config_anchor_refused'

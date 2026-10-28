"""Security-boundary tests for URL parsing / allowlist matching.

These are pure functions: no DNS and no HTTP. The bypass cases are the point.
"""
import pytest

from core.web_fetch_policy import (
    evaluate,
    host_matches,
    normalize_entry,
    parse_url,
    validate_policy,
)


class TestParseUrl:
    def test_https_default_port(self):
        p = parse_url('https://bromley.gov.uk/page')
        assert p.scheme == 'https'
        assert p.host == 'bromley.gov.uk'
        assert p.port == 443
        assert p.path == '/page'
        assert p.url == 'https://bromley.gov.uk/page'

    def test_http_default_port(self):
        p = parse_url('http://example.com')
        assert p.scheme == 'http'
        assert p.port == 80
        assert p.path == '/'

    def test_case_and_trailing_dot_normalised(self):
        p = parse_url('HTTPS://WWW.Example.COM./x')
        assert p.host == 'www.example.com'
        assert p.url == 'https://www.example.com/x'

    def test_fragment_stripped(self):
        p = parse_url('https://example.com/page#section')
        assert p.url == 'https://example.com/page'
        assert p.query == ''

    def test_unicode_dot_is_normalised(self):
        p = parse_url('https://bromley.gov.uk\u3002page/x')
        assert p.host == 'bromley.gov.uk.page'

    @pytest.mark.parametrize('url,code', [
        ('file:///etc/passwd', 'scheme_not_allowed'),
        ('ftp://example.com/', 'scheme_not_allowed'),
        ('gopher://example.com/', 'scheme_not_allowed'),
        ('not a url', 'invalid_url'),
        ('', 'invalid_url'),
        ('   ', 'invalid_url'),
        ('https://', 'invalid_url'),
        ('https://user:pass@example.com/', 'invalid_url'),
        ('https://user@example.com/', 'invalid_url'),
        ('https://example.com:8443/', 'port_not_allowed'),
        ('https://example.com:22/', 'port_not_allowed'),
        ('https://192.168.1.235:8123/', 'host_not_allowed'),
        ('http://10.0.0.1/', 'host_not_allowed'),
        ('http://[::1]/', 'host_not_allowed'),
        ('http://localhost/', 'host_not_allowed'),
        ('http://example.123/', 'host_not_allowed'),
        ('https://example%2ecom/', 'invalid_url'),
        ('https://example.com\\@evil.tld/', 'invalid_url'),
        ('https://example.com/pa\nth', 'invalid_url'),
        ('https://a..b/', 'invalid_url'),
        ('https://exa mple.com/', 'invalid_url'),
        ('https://example.com/' + 'a' * 2050, 'invalid_url'),
    ])
    def test_rejections(self, url, code):
        with pytest.raises(Exception) as exc:
            parse_url(url)
        assert getattr(exc.value, 'code', None) == code


class TestNormalizeEntry:
    @pytest.mark.parametrize('entry,expected', [
        ('bromley.gov.uk', 'bromley.gov.uk'),
        ('BROMLEY.GOV.UK.', 'bromley.gov.uk'),
        ('gov.uk', 'gov.uk'),
        ('www.tesco.com', 'www.tesco.com'),
    ])
    def test_accepts(self, entry, expected):
        assert normalize_entry(entry) == expected

    @pytest.mark.parametrize('entry', [
        '', '   ', '*.gov.uk', 'gov.uk/path', 'https://gov.uk',
        '1.2.3.4', '::1', 'localhost', 'gov.uk:443', 'user@gov.uk',
        '_dmarc.gov.uk', '-bad.gov.uk', 'bad-.gov.uk', 'a..b',
        'example.123', 'gov.uk?x=1', 'gov.uk#frag', 'a' * 64 + '.com',
    ])
    def test_rejects(self, entry):
        with pytest.raises(ValueError):
            normalize_entry(entry)

    def test_idna_entry(self):
        assert normalize_entry('münchen.de').startswith('xn--')
        assert normalize_entry('münchen.de').endswith('.de')


class TestHostMatches:
    @pytest.mark.parametrize('host,entry,expected', [
        ('bromley.gov.uk', 'bromley.gov.uk', True),
        ('www.bromley.gov.uk', 'bromley.gov.uk', True),
        ('recyclingservices.bromley.gov.uk', 'bromley.gov.uk', True),
        ('evilbromley.gov.uk', 'bromley.gov.uk', False),
        ('bromley.gov.uk.attacker.tld', 'bromley.gov.uk', False),
        ('notbromley.gov.uk', 'bromley.gov.uk', False),
        ('www.gov.uk', 'gov.uk', True),
        ('service.gov.uk', 'gov.uk', True),
        ('notgov.uk', 'gov.uk', False),
        ('gov.uk.attacker.tld', 'gov.uk', False),
    ])
    def test_boundary(self, host, entry, expected):
        assert host_matches(host, entry) is expected


class TestValidatePolicy:
    def test_dedupes_and_normalises(self):
        allow, deny = validate_policy(
            ['Bromley.gov.uk.', 'bromley.gov.uk'], ['Reddit.com'])
        assert allow == ['bromley.gov.uk']
        assert deny == ['reddit.com']

    def test_rejects_bad_entry(self):
        with pytest.raises(ValueError):
            validate_policy(['*.gov.uk'], [])

    def test_rejects_non_list(self):
        with pytest.raises(ValueError):
            validate_policy('gov.uk', [])


class TestEvaluate:
    def test_disabled(self):
        d = evaluate({'enabled': False, 'allow': ['gov.uk']}, 'https://www.gov.uk/')
        assert d.allowed is False
        assert d.code == 'disabled'

    def test_empty_allow_fails_closed(self):
        d = evaluate({'enabled': True, 'allow': [], 'deny': []}, 'https://www.gov.uk/')
        assert d.allowed is False
        assert d.code == 'host_not_allowed'

    def test_allow_suffix(self):
        d = evaluate({'enabled': True, 'allow': ['gov.uk']}, 'https://www.gov.uk/x')
        assert d.allowed is True
        assert d.matched == 'gov.uk'

    def test_deny_beats_allow(self):
        d = evaluate(
            {'enabled': True, 'allow': ['gov.uk'], 'deny': ['www.gov.uk']},
            'https://www.gov.uk/x')
        assert d.allowed is False
        assert d.code == 'host_denied'
        assert d.matched == 'www.gov.uk'

    def test_lookalike_domain_denied(self):
        d = evaluate({'enabled': True, 'allow': ['bromley.gov.uk']},
                     'https://evilbromley.gov.uk/x')
        assert d.allowed is False
        assert d.code == 'host_not_allowed'

    def test_suffix_trick_denied(self):
        d = evaluate({'enabled': True, 'allow': ['bromley.gov.uk']},
                     'https://bromley.gov.uk.attacker.tld/x')
        assert d.allowed is False
        assert d.code == 'host_not_allowed'

    def test_unicode_dot_trick_denied(self):
        d = evaluate({'enabled': True, 'allow': ['bromley.gov.uk']},
                     'https://bromley.gov.uk\u3002attacker.tld/x')
        assert d.allowed is False
        assert d.code == 'host_not_allowed'

    def test_bad_url_code_propagates(self):
        d = evaluate({'enabled': True, 'allow': ['gov.uk']}, 'file:///etc/passwd')
        assert d.allowed is False
        assert d.code == 'scheme_not_allowed'

    def test_malformed_policy_fails_closed(self):
        d = evaluate({'enabled': True, 'allow': ['*.gov.uk']}, 'https://www.gov.uk/')
        assert d.allowed is False
        assert d.code == 'config_error'

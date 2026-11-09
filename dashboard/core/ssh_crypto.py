"""F12: the signed SSH prefix and its derived key.

The dashboard and the gateway must compute the same MAC over the same bytes,
so the canonical form lives here and is mirrored by the `canonical=` block in
`eshu-gateway.sh` (§5). The MAC key is derived from the gateway token, which
keeps it separate from the token's role as a bearer credential and makes a
token rotation rotate the key (security condition G1).

`derive_key` is called when a token is set or rotated, and the result is
stored in `gateways.ssh_key_derived`; the signing path reads only that column.
That matters beyond tidiness: security Chunk 7 stores only a hash of the
token, after which `K` could no longer be computed.
"""
import hashlib
import hmac
import re

LABEL = b'eshu-ssh-agent-v1'
CANONICAL_VERSION = 'v1'

# Mirrors `validate_prefix` in eshu-gateway.sh §5. Every field is checked
# before it enters the canonical form, so the MACed text cannot contain a
# newline or a shell metacharacter.
AGENT_RE = re.compile(r'[A-Za-z0-9_.:-]{1,64}')
SESSION_RE = re.compile(r'[A-Za-z0-9_.:-]{0,64}')
TS_RE = re.compile(r'[0-9]{1,12}')
NONCE_RE = re.compile(r'[0-9a-f]{16,64}')
GATEWAY_RE = re.compile(r'[A-Za-z0-9_.:-]{1,64}')
SIG_RE = re.compile(r'[0-9a-f]{64}')


def derive_key(token: str) -> str:
    """`K = HMAC-SHA256(token, "eshu-ssh-agent-v1")`, lowercase hex.

    An empty token has no key: the caller stores '' and signing fails closed.
    """
    token = (token or '').strip()
    if not token:
        return ''
    return hmac.new(token.encode('utf-8'), LABEL, hashlib.sha256).hexdigest()


def command_sha(command: str) -> str:
    """sha256 of exactly the bytes the gateway will hash (UTF-8, no trim)."""
    return hashlib.sha256((command or '').encode('utf-8')).hexdigest()


def canonical(command, agent, session='', mcp='1', force_jit='0', ts='0',
              nonce='', gateway_ip=''):
    """The one-field-per-line, version-first canonical form (§5 A2).

    Raises ValueError for a value outside its charset. The caller has already
    validated the command bytes (G3); this only guards the prefix fields.
    """
    fields = {
        'agent': (agent, AGENT_RE),
        'session': (session or '', SESSION_RE),
        'mcp': (str(mcp), re.compile(r'[01]')),
        'force_jit': (str(force_jit), re.compile(r'[01]')),
        'ts': (str(ts), TS_RE),
        'nonce': (nonce or '', NONCE_RE),
        'gateway': (gateway_ip or '', GATEWAY_RE),
    }
    for name, (value, pattern) in fields.items():
        if not pattern.fullmatch(value or ''):
            raise ValueError(f'invalid {name}')
    return '\n'.join([CANONICAL_VERSION, fields['agent'][0],
                      fields['session'][0], fields['mcp'][0],
                      fields['force_jit'][0], fields['ts'][0],
                      fields['nonce'][0], fields['gateway'][0],
                      command_sha(command)])


def sign(key_hex: str, canonical_text: str) -> str:
    """HMAC-SHA256 with the derived key. Empty key -> ValueError (fail closed)."""
    try:
        key = bytes.fromhex(key_hex or '')
    except ValueError:
        raise ValueError('invalid key')
    if len(key) != 32:
        raise ValueError('invalid key')
    return hmac.new(key, canonical_text.encode('utf-8'),
                    hashlib.sha256).hexdigest()


def prefix_line(agent, session, force_jit, ts, nonce, signature):
    """The ssh prefix, ending with a space so the command follows directly."""
    parts = [f'ESHU_AGENT={agent}', f'ESHU_AGENT_SIG={signature}',
             f'ESHU_TS={ts}', f'ESHU_NONCE={nonce}', 'ESHU_MCP=1']
    if force_jit is True or str(force_jit) == '1':
        parts.append('ESHU_FORCE_JIT=1')
    if session:
        parts.append(f'ESHU_SESSION_ID={session}')
    return ' '.join(parts) + ' '

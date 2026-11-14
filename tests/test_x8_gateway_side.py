"""X8 (gateway side): report the installer hash, re-send it from the logger.

The dashboard side (channel badge, counts) stays on its own branch; a 1.3
dashboard ignores the extra field, and a 1.3 gateway sends nothing.
"""
import hashlib
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "dashboard" / "eshu-installer-template.sh"
GENERATED = REPO / "dashboard" / "eshu-gateway-install.sh"
POLLER = REPO / "dashboard" / "eshu-poller.sh"
LOGGER = REPO / "dashboard" / "eshu-logger.sh"

HEX12 = re.compile(r'^[0-9a-f]{12}$')


def test_the_installer_hashes_itself_and_sends_it():
    text = TEMPLATE.read_text()
    assert 'INSTALLER_HASH=$(sha256sum -- "$0"' in text
    assert '"installer_hash":"\'"$INSTALLER_HASH"\'"' in text
    assert "/etc/eshu-installer-hash" in text
    assert "chmod 644 /etc/eshu-installer-hash.tmp" in text
    assert "chown root:root /etc/eshu-installer-hash.tmp" in text
    generated = GENERATED.read_text()
    assert 'INSTALLER_HASH=$(sha256sum -- "$0"' in generated
    assert '"installer_hash":"\'"$INSTALLER_HASH"\'"' in generated


def test_the_hash_snippet_produces_the_first_twelve_hex(tmp_path):
    # Extract the block from the template and run it against a real file.
    text = TEMPLATE.read_text()
    start = text.index('INSTALLER_HASH=""')
    end = text.index("esac\n", start) + len("esac\n")
    snippet = text[start:end]
    # Run the block from a file: the point is that it hashes "$0", the file
    # it was run from (the official flows always download it first).
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text("#!/bin/bash\n" + snippet + '\nprintf "%s" "$INSTALLER_HASH"\n')
    proc = subprocess.run(["bash", str(wrapper)], capture_output=True, text=True,
                          timeout=30)
    # The wrapper's own hash, not the target's: that is the point ($0).
    want = hashlib.sha256(wrapper.read_bytes()).hexdigest()[:12]
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == want
    assert HEX12.match(proc.stdout)


def test_the_poller_re_sends_the_hash_after_any_update():
    text = POLLER.read_text()
    assert "read_installer_hash()" in text
    assert 'head -n1 "/etc/eshu-installer-hash"' in text
    assert text.count('"installer_hash":"\'"$(read_installer_hash)"\'"') == 2


def test_the_logger_re_sends_the_hash_every_heartbeat():
    text = LOGGER.read_text()
    assert 'INSTALLER_HASH=$(head -n1 "/etc/eshu-installer-hash"' in text
    assert '\\"installer_hash\\":\\"$INSTALLER_HASH\\"' in text

"""The site-specific privacy settings come from the configuration file.

Moving them out of the code must not change what Mu2e's deployment does: the
fingerprints below were taken from the code before the move (2026-11-15,
v0.2.11), so the scan prompt -- and with it PROMPT_VERSION -- the restriction
markers and the global redaction patterns are byte-for-byte what they were.
If one of these fails after a deliberate change to the prompt or a pattern,
update the fingerprint (and, for the prompt, bump PROMPT_VERSION).

conftest.py points PRIVACY_CONFIG_FILE at deploy/mu2e-privacy.toml; the
generic defaults are checked in a subprocess, since the settings are read
when the modules are imported.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kb_mcp.privacy import config, redaction, sensitivity

MU2E = Path(__file__).resolve().parents[2] / "deploy" / "mu2e-privacy.toml"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_tests_run_with_mu2e_configuration():
    assert config.config_file() == MU2E


def test_mu2e_scan_prompt_is_unchanged():
    messages = sensitivity.build_messages("Some text with a passage.", title="T", source="mu2e-docdb",
                                          source_type="application/pdf", window=1, windows=2)
    assert _sha(json.dumps(messages)) == "470c526915623b2f82bc0bfaa8b643206190659d452dc079c7f7d97799f32153"
    assert _sha(repr(sensitivity.CATEGORIES)) == "75ae7a49e5707e97098fca8200dd3e1d4e89d861af4cb80d11cbc548c1c9392d"
    assert sensitivity.PROMPT_VERSION == "v4"


def test_mu2e_markers_and_hosts_are_unchanged():
    markers = repr([(n, sev, rx.pattern, rx.flags) for n, sev, rx in sensitivity.MARKERS])
    assert _sha(markers) == "9ba08893f94b83a9fc59aff208b6352391bb90771c4f0db2b37f317eefe0d478"
    assert sensitivity.DEFAULT_ALLOWED_HOSTS == ("vllm.fnal.gov", "inference-api.alcf.anl.gov")


def test_mu2e_global_redaction_patterns_are_unchanged():
    patterns = repr([(p.name, p.label, p.regex.pattern, p.regex.flags, p.group) for p in redaction.GLOBAL_PATTERNS])
    assert _sha(patterns) == "b71e41bdb682c2da81e61dcba22e5077075e66d0f85d24299608f36f656edead"
    assert "[redacted: network address]" in redaction.redact("ipmi 131.225.1.2 and 10.0.0.1")


GENERIC = r"""
import json
from kb_mcp.privacy import sensitivity, redaction
from kb_mcp.kb.visibility_rules import load_rules
m = sensitivity.build_messages("x")
print(json.dumps({
    "prompt": m[0]["content"] + m[1]["content"],
    "hosts": list(sensitivity.DEFAULT_ALLOWED_HOSTS),
    "markers": [f["span"] for f in sensitivity.find_markers("collaboration internal only. Mu2e internal only.")],
    "redacted": redaction.redact("10.0.0.1 and 131.225.1.2"),
    "rules": sorted(load_rules()),
}))
"""


def test_without_a_file_the_defaults_are_generic():
    env = dict(os.environ, PRIVACY_CONFIG_FILE="")
    out = subprocess.run([sys.executable, "-c", GENERIC], env=env, capture_output=True, text=True, check=True)
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert "Mu2e" not in got["prompt"] and "Fermilab" not in got["prompt"]
    assert "an organization" in got["prompt"]
    assert got["hosts"] == []                           # the scan needs --allow-endpoint
    assert got["markers"] == ["collaboration internal only"]
    assert got["redacted"] == "[redacted: network address] and 131.225.1.2"
    assert got["rules"] == []


@pytest.mark.parametrize("text, message", [
    ("[scna]\norganization = 'x'\n", "unknown section"),
    ("[scan]\norganisation = 'x'\n", "unknown key"),
    ("[scan]\nallowed_hosts = 'vllm'\n", "list of non-empty strings"),
    ("[scan]\norganization = ''\n", "non-empty string"),
])
def test_mistakes_in_the_file_are_errors(tmp_path, text, message):
    f = tmp_path / "privacy.toml"
    f.write_text(text)
    with pytest.raises(ValueError, match=message):
        config.read_config(f)


def test_network_prefixes_must_be_two_octets():
    with pytest.raises(ValueError, match="first two octets"):
        redaction._network_address_regex(["131"])

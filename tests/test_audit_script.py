"""The sanitization audit must catch what it claims to catch - and stay quiet on safe demo data.

audit:ignore-file  (this file deliberately contains FAKE sensitive-looking samples; none are real)
"""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "audit_public_repo.py"
spec = importlib.util.spec_from_file_location("audit_public_repo", SCRIPT)
audit = importlib.util.module_from_spec(spec)
sys.modules["audit_public_repo"] = audit
spec.loader.exec_module(audit)


def rules(text: str, denylist=None) -> set[str]:
    return {f.rule for f in audit.scan_text("x.py", text, denylist)}


@pytest.mark.parametrize(
    "snippet, rule",
    [
        ('api_key = "FAKEk3yFAKEk3yFAKEk3y0000"', "secret-literal"),
        ('password: "hunter2hunter2hunter2"', "secret-literal"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private-key"),
        ("key = AKIAABCDEFGHIJKLMNOP", "cloud-key"),
        ("server = 10.20.30.40", "private-ip"),
        ("gateway 192.168.0.1", "private-ip"),
        ("contact jane.doe@realcompany.org", "email"),
        ("call (212) 867-5309", "phone"),
        (r"path C:\Users\someone\Desktop", "user-path"),
    ],
)
def test_dangerous_strings_are_flagged(snippet, rule):
    assert rule in rules(snippet)


@pytest.mark.parametrize(
    "snippet",
    [
        "ip = 198.51.100.17",  # RFC 5737 documentation range
        "ip = 127.0.0.1",
        "firmware 1.0.78.0",  # looks like an IP but is a public-range version string
        "mail ada@democorp.example",
        "call +12025550123",  # fictional 555-01xx block
        'SECRET_KEY = "change-me-for-anything-other-than-a-local-demo"',
        "DEVICEADMIN_SECRET_KEY=change-me-for-anything-other-than-a-local-demo",
    ],
)
def test_safe_demo_values_are_not_flagged(snippet):
    assert rules(snippet) == set()


def test_denylist_is_case_insensitive():
    assert "denylist" in rules("Welcome to Initech Medical", ["initech medical"])


def test_forbidden_files_are_flagged(tmp_path):
    (tmp_path / "customer.db").write_bytes(b"x")
    (tmp_path / "notes.docx").write_bytes(b"x")
    (tmp_path / ".env").write_text("A=1")
    (tmp_path / ".env.example").write_text("A=1")
    (tmp_path / "ok.py").write_text("print('hi')")
    found = {(f.path, f.rule) for f in audit.audit(tmp_path)}
    assert found == {("customer.db", "forbidden-file"), ("notes.docx", "forbidden-file"), (".env", "forbidden-file")}


def test_this_repository_passes_its_own_audit():
    assert audit.main(["--root", str(SCRIPT.parent.parent)]) == 0

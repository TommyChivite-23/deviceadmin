"""Scan the repository for anything that should never be published.

Generic rules (always on)
  * private keys, cloud keys, secrets/tokens/passwords assigned to string literals
  * long high-entropy strings (likely API keys)
  * private-network IPv4 addresses (10/8, 172.16/12, 192.168/16, link-local)
  * email addresses outside reserved example domains
  * US phone numbers outside the fictional 555-01xx block
  * Windows user-profile paths
  * forbidden file types (databases, logs, certificates, Office documents, .env)

Optional private denylist
  Pass ``--denylist path/to/file.txt`` with one case-insensitive term per line
  (real names, hostnames, the old organisation's name...). Keep that file OUTSIDE
  the repository - it is the one place the real identifiers are written down.

Exit status: 0 = clean, 1 = findings. Run it before every commit and in CI.
"""

from __future__ import annotations

import argparse
import ipaddress
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", "htmlcov"}
FORBIDDEN_SUFFIXES = {
    ".db", ".sqlite", ".sqlite3", ".log", ".pem", ".key", ".pfx", ".p12", ".crt", ".cer",
    ".docx", ".doc", ".xlsx", ".pptx", ".zip", ".bak", ".dump", ".pcap",
}  # fmt: skip
TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".json", ".html", ".css", ".js", ".sh",
    ".ps1", ".xml", ".example", ".gitignore", ".dockerignore", ".gitattributes", "",
}  # fmt: skip
SELF = Path(__file__).resolve()
TEST_FIXTURE_MARK = "audit:ignore-file"

PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
AWS_KEY = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
SECRET_LITERAL = re.compile(
    r"""(?ix)(api[_-]?key|secret|token|passw(?:or)?d|pwd|client[_-]?secret)\w*["']?\s*[:=]\s*["']([A-Za-z0-9_\-+/=.]{12,})["']"""
)
SECRET_UNQUOTED = re.compile(
    r"(?im)^\s*[A-Za-z_]*(api[_-]?key|secret|token|passw(?:or)?d)[A-Za-z_]*\s*[:=]\s*([A-Za-z0-9_\-+/=.]{16,})\s*$"
)
LONG_TOKEN = re.compile(r"(?<![A-Za-z0-9+/_\-.])[A-Za-z0-9+/_\-]{40,}(?![A-Za-z0-9+/_\-])")
IPV4 = re.compile(r"(?<![\d.])(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(?![\d.])")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})\b")
PHONE = re.compile(r"(?<![\d.])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?![\d.])")
WIN_PATH = re.compile(r"[A-Za-z]:\\Users\\[A-Za-z0-9._-]+", re.IGNORECASE)

SAFE_EMAIL_DOMAINS = ("example", "example.com", "example.org", "example.net", "users.noreply.github.com")
SAFE_VALUE_HINTS = ("change-me", "changeme", "dev-only", "example", "placeholder", "your-", "xxxx", "<", "not-a-secret")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}  [{self.rule}]  {self.detail}"


def entropy(text: str) -> float:
    counts = {c: text.count(c) for c in set(text)}
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def _is_private_ip(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    return ip.is_private and not ip.is_loopback and not ip.is_unspecified and not _is_documentation(ip)


def _is_documentation(ip) -> bool:
    nets = ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
    return any(ip in ipaddress.ip_network(n) for n in nets)


def _phone_is_fictional(match: str) -> bool:
    digits = re.sub(r"\D", "", match)[-10:]
    return digits[3:6] == "555" and digits[6:8] == "01"


def scan_text(rel: str, text: str, denylist: list[str] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    # Files that deliberately contain fake sensitive-looking samples (the audit's own tests) opt out of the
    # generic rules with this marker. The private denylist ALWAYS applies.
    generic = TEST_FIXTURE_MARK not in text
    lowered_deny = [t.lower() for t in (denylist or []) if t.strip()]
    for number, line in enumerate(text.splitlines(), start=1):
        low = line.lower()
        for term in lowered_deny:
            if term in low:
                findings.append(Finding(rel, number, "denylist", f"contains '{term}'"))
        if not generic:
            continue
        if PRIVATE_KEY.search(line):
            findings.append(Finding(rel, number, "private-key", "PEM private key block"))
        if AWS_KEY.search(line):
            findings.append(Finding(rel, number, "cloud-key", "looks like an AWS access key id"))
        for m in SECRET_LITERAL.finditer(line):
            if not any(h in m.group(2).lower() for h in SAFE_VALUE_HINTS):
                findings.append(Finding(rel, number, "secret-literal", f"{m.group(1)} assigned a literal value"))
        for m in SECRET_UNQUOTED.finditer(line):
            if not any(h in m.group(2).lower() for h in SAFE_VALUE_HINTS):
                findings.append(Finding(rel, number, "secret-unquoted", f"{m.group(1)} has a literal value"))
        for m in LONG_TOKEN.finditer(line):
            token = m.group(0)
            if entropy(token) > 4.2 and not token.startswith(("http", "sha")) and "/" not in token[:3]:
                findings.append(Finding(rel, number, "high-entropy", f"{token[:6]}... ({len(token)} chars)"))
        for m in IPV4.finditer(line):
            if _is_private_ip(m.group(1)):
                findings.append(Finding(rel, number, "private-ip", m.group(1)))
        for m in EMAIL.finditer(line):
            domain = m.group(1).lower()
            if not domain.endswith(SAFE_EMAIL_DOMAINS):
                findings.append(Finding(rel, number, "email", m.group(0)))
        for m in PHONE.finditer(line):
            if not _phone_is_fictional(m.group(0)):
                findings.append(Finding(rel, number, "phone", m.group(0)))
        if WIN_PATH.search(line):
            findings.append(Finding(rel, number, "user-path", "Windows user-profile path"))
    return findings


def candidate_files(root: Path) -> list[Path]:
    """Files that would be committed: use git when possible, else walk the tree."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard"],
            cwd=root, capture_output=True, text=True, check=True,
        ).stdout  # fmt: skip
        return [root / line for line in out.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        pass
    files = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if path.is_file() and not any(part in SKIP_DIRS for part in rel.parts) and not _gitignored(rel):
            files.append(path)
    return files


def _gitignored(rel: Path) -> bool:
    """Minimal stand-in for .gitignore when git is not installed."""
    name = rel.name
    # Deliberately does NOT hide .env files: without git we cannot know they are ignored, so flag them.
    return (
        (rel.parts[0] == "data" and name != ".gitkeep")
        or name.endswith((".db-wal", ".db-shm"))
        or name in {".coverage", "coverage.xml"}
    )


def audit(root: Path, denylist: list[str] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for path in candidate_files(root):
        rel = path.relative_to(root).as_posix()
        if path.resolve() == SELF:
            continue
        name = path.name
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            findings.append(Finding(rel, 0, "forbidden-file", f"{path.suffix} files must not be published"))
            continue
        if name.startswith(".env") and name != ".env.example":
            findings.append(
                Finding(rel, 0, "forbidden-file", "environment file (must stay git-ignored; never commit it)")
            )
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".mp4"}:
            continue  # images and the demo video are reviewed by eye; see the checklist
        if path.suffix.lower() not in TEXT_SUFFIXES and name not in {"Dockerfile", "LICENSE"}:
            findings.append(Finding(rel, 0, "unknown-type", "unreviewed file type - inspect it manually"))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            findings.append(Finding(rel, 0, "unreadable", "could not be read as UTF-8 text"))
            continue
        findings.extend(scan_text(rel, text, denylist))
        for term in (t.lower() for t in (denylist or []) if t.strip()):
            if term in name.lower() or term in rel.lower():
                findings.append(Finding(rel, 0, "denylist", f"file name contains '{term}'"))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--denylist", type=Path, help="private file of terms that must never appear")
    args = parser.parse_args(argv)

    terms: list[str] = []
    if args.denylist:
        terms = [ln.strip() for ln in args.denylist.read_text(encoding="utf-8").splitlines()
                 if ln.strip() and not ln.startswith("#")]  # fmt: skip
    findings = audit(args.root, terms)
    scanned = len(candidate_files(args.root))
    if findings:
        print(f"AUDIT FAILED - {len(findings)} finding(s) in {scanned} files:\n")
        for finding in findings:
            print("  " + str(finding))
        return 1
    extra = f" and {len(terms)} denylist term(s)" if terms else ""
    print(f"Audit passed: {scanned} files checked against the generic rules{extra}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""AT-21: guard against committing research data, derived data, large files or secrets.

Usage: ``python -m quantedge.devtools.hygiene`` (checks ``git ls-files``) or with explicit paths
(pre-commit passes staged files). Exits non-zero on any violation.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath

MAX_BYTES = 1_000_000
FORBIDDEN_DIRS = ("data/", "runs/")
FORBIDDEN_SUFFIXES = (".parquet", ".xlsx", ".xls", ".zip", ".pkl", ".feather", ".arrow", ".db")
CSV_ALLOWED_DIRS = ("tests/", "src/quantedge/reference/")
# Raw provider responses are JSON/JSONL: only synthetic test fixtures may be tracked.
JSON_ALLOWED_DIRS = ("tests/",)

SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(
        r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{60,}\b"
    ),
    "assigned_secret": re.compile(
        r"(?i)\b(?:api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{20,}"
    ),
    "odds_api_key_value": re.compile(r"\bQE_ODDS_API_KEY\s*=\s*[A-Za-z0-9]{8,}"),
}


def path_violations(paths: Iterable[str], root: Path | None = None) -> list[str]:
    problems = []
    for raw in paths:
        path = PurePosixPath(raw.replace("\\", "/"))
        text = str(path)
        name = path.name
        if text.startswith(FORBIDDEN_DIRS):
            problems.append(f"{text}: research/derived data directory must not be tracked")
        if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
            problems.append(f"{text}: environment file must not be tracked")
        if text.lower().endswith(FORBIDDEN_SUFFIXES):
            problems.append(f"{text}: data/binary artifact type must not be tracked")
        if text.lower().endswith(".csv") and not text.startswith(CSV_ALLOWED_DIRS):
            problems.append(f"{text}: CSV outside tests/ and reference tables")
        if text.lower().endswith((".json", ".jsonl", ".json.gz")) and not text.startswith(
            JSON_ALLOWED_DIRS
        ):
            problems.append(f"{text}: JSON outside tests/ (possible raw provider response)")
        if root is not None:
            file = root / text
            if file.is_file() and file.stat().st_size > MAX_BYTES:
                problems.append(f"{text}: larger than {MAX_BYTES} bytes")
    return problems


def secret_violations(paths: Iterable[str], root: Path) -> list[str]:
    problems = []
    for raw in paths:
        file = root / raw
        if not file.is_file() or file.stat().st_size > MAX_BYTES:
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for name, pattern in SECRET_PATTERNS.items():
                if pattern.search(line):
                    problems.append(f"{raw}:{line_no}: possible {name}")
    return problems


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
    ).stdout.decode("utf-8")
    return [p for p in out.split("\0") if p]


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path.cwd()
    paths = args or tracked_files(root)
    problems = path_violations(paths, root) + secret_violations(paths, root)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

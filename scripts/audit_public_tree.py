"""Fail fast on common private files and secrets in the committed tree.

This is a guardrail, not a substitute for reviewing every file before publication.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PATH = re.compile(
    r"(^|/)(?:\.env|tmp|storage|uploads|downloads|node_modules|data)(?:/|$)|"
    r"(?:payloads?|receipts?)/",
    re.IGNORECASE,
)
FORBIDDEN_TEXT = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"/Users/[^/\s]+/",
        r"[\w.+-]+@(?:gmail|mail|yandex|protonmail)\.(?:com|ru)",
        r"(?:AIza[\w-]{20,}|sk-[\w-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"(?:https?://[^\s\"']*/transportation/watch\?[^\s\"']*token=)",
    )
]


def main() -> None:
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode().split("\0")
    problems = []
    for name in filter(None, tracked):
        if name == "scripts/audit_public_tree.py":
            continue  # The matcher definitions naturally match their own source.
        if name != ".env.example" and FORBIDDEN_PATH.search(name):
            problems.append(f"forbidden path: {name}")
            continue
        path = ROOT / name
        if path.suffix == ".docx":
            with ZipFile(path) as archive:
                text = "\n".join(
                    archive.read(member).decode("utf-8", "ignore")
                    for member in archive.namelist()
                    if member.endswith(".xml")
                )
        else:
            text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in FORBIDDEN_TEXT:
            if pattern.search(text):
                problems.append(f"sensitive pattern in: {name} ({pattern.pattern})")
    if problems:
        raise SystemExit("\n".join(problems))
    print(f"Reviewed {len(tracked) - 1} tracked files: no blocked paths or patterns")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Structurally verify files restored by restore_vetted.py.

Size alone proves nothing - an empty file and a corrupt file both differ from
the source. Each restored file must (a) be byte-identical to its source, and
(b) actually parse/compile with the toolchain that will consume it.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from restore_vetted import PAIRS  # noqa: E402

NODE = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python311\Scripts\node.exe"
if not os.path.exists(NODE):
    NODE = "node"


def md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def reject_if_truncated(path: str) -> str | None:
    """`node --check` accepts an empty .mjs as a valid empty module, so a syntax
    check alone cannot see truncation. Refuse empties before parsing anything."""
    with open(path, "rb") as f:
        data = f.read()
    if not data.strip():
        return "file is empty/whitespace-only (%dB) - truncated" % len(data)
    return None


def check_syntax(path: str) -> tuple[bool, str]:
    trunc = reject_if_truncated(path)
    if trunc:
        return False, trunc
    r = subprocess.run([NODE, "--check", path], capture_output=True, timeout=60)
    ok = r.returncode == 0
    return ok, r.stderr.decode("utf-8", "replace").strip()[:200] or "compiles clean"


def check_json(path: str) -> tuple[bool, str]:
    trunc = reject_if_truncated(path)
    if trunc:
        return False, trunc
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:  # noqa: BLE001
        return False, "%s: %s" % (type(exc).__name__, exc)
    kind = type(data).__name__
    n = len(data) if isinstance(data, (list, dict)) else 0
    return True, "%s len=%d" % (kind, n)


def check_markdown(path: str) -> tuple[bool, str]:
    trunc = reject_if_truncated(path)
    if trunc:
        return False, trunc
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---"):
        return False, "no leading frontmatter delimiter"
    end = text.find("\n---", 3)
    if end < 0:
        return False, "frontmatter never closed"
    fm = text[3:end]
    if "name:" not in fm or "description:" not in fm:
        return False, "frontmatter missing name/description"
    name = [l for l in fm.splitlines() if l.startswith("name:")]
    return True, "frontmatter ok (%s), body=%dB" % (name[0].strip() if name else "?", len(text))


CHECKS = {".mjs": check_syntax, ".json": check_json, ".md": check_markdown}


def main() -> int:
    fails = []
    print("== VERIFY RESTORED FILES ==")
    for src, dst, _ in PAIRS:
        if not os.path.isfile(dst):
            fails.append((dst, "destination does not exist"))
            print("  FAIL missing  %s" % dst)
            continue
        ext = os.path.splitext(dst)[1].lower()
        same = md5(src) == md5(dst)
        size = os.path.getsize(dst)
        checker = CHECKS.get(ext)
        if checker is None:
            fails.append((dst, "no structural checker for %s" % ext))
            print("  FAIL nochecker %s" % dst)
            continue
        ok, detail = checker(dst)
        status = "OK  " if (same and ok) else "FAIL"
        if not (same and ok):
            fails.append((dst, "identical=%s structural=%s %s" % (same, ok, detail)))
        print(
            "  %s %7dB md5match=%-5s %-9s %s"
            % (status, size, same, ext or "?", detail)
        )
        print("        %s" % dst)

    print("\nverified=%d failed=%d" % (len(PAIRS) - len(fails), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())

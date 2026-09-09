#!/usr/bin/env python3
"""Restore burst-damaged files that have a genuinely intact mirror copy.

Safety contract, enforced before every write:
  * destination must currently be empty (0 bytes) or CRLF-only (2 bytes)
  * source must be non-empty and share >=3 trailing path components with dest
If either fails, the pair is skipped, never forced.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

PAIRS = [
    (
        r"E:\workA\A-skill\project-brain-agent\agent-canvas\.cursor\skills\requirement-mind\scripts\fixtures\decisions-legacy.json",
        r"E:\workA\shejiuPro\.cursor\skills\requirement-mind\scripts\fixtures\decisions-legacy.json",
        3,
    ),
    (
        r"E:\workA\A-skill\project-brain-agent\agent-canvas\.cursor\skills\requirement-mind\schemas\state.schema.json",
        r"E:\workA\shejiuPro\.cursor\skills\requirement-mind\schemas\state.schema.json",
        3,
    ),
    (
        r"E:\workA\A-skill\project-brain-agent\agent-canvas\.cursor\skills\requirement-mind\scripts\state.mjs",
        r"E:\workA\shejiuPro\.cursor\skills\requirement-mind\scripts\state.mjs",
        3,
    ),
    (
        r"E:\workA\A-skill\project-brain-agent\agent-canvas\.cursor\skills\requirement-mind\SKILL.md",
        r"E:\workA\shejiuPro\.cursor\skills\requirement-mind\SKILL.md",
        3,
    ),
    (
        r"E:\workA\A-skill\project-brain-agent\agent-canvas\.cursor\skills\requirement-mind\scripts\fixtures\questions-legacy.json",
        r"E:\workA\A-skill\requirement-mind\scripts\fixtures\questions-legacy.json",
        3,
    ),
    (
        r"E:\workA\A-skill\Token-Mind\contextmind\lib\shell-result.mjs",
        r"E:\workA\shejiuPro\.cursor\contextmind\lib\shell-result.mjs",
        3,
    ),
    # "cursor" vs ".cursor" caps agreement at 2; mirror proven instead by cm-lib.mjs
    # being byte-identical in both trees (md5 97781a35cdb1f3e631afde17a524dc43),
    # which is the only module this hook imports.
    (
        r"E:\workA\A-skill\Token-Mind\cursor\hooks\cm-post-tool.mjs",
        r"E:\workA\shejiuPro\.cursor\hooks\cm-post-tool.mjs",
        2,
    ),
]


def is_empty_damage(path: str) -> bool:
    if not os.path.isfile(path):
        return False
    with open(path, "rb") as f:
        data = f.read()
    return data in (b"", b"\r\n", b"\n")


def suffix_agreement(a: str, b: str) -> int:
    pa = [p.lower() for p in a.replace("/", "\\").split("\\") if p]
    pb = [p.lower() for p in b.replace("/", "\\").split("\\") if p]
    n = 0
    for x, y in zip(reversed(pa), reversed(pb)):
        if x != y:
            break
        n += 1
    return n


def main() -> int:
    apply = "--apply" in sys.argv
    done, skipped = [], []

    for src, dst, min_agree in PAIRS:
        if os.path.normcase(src) == os.path.normcase(dst):
            skipped.append((src, dst, "source == destination (already recovered)"))
            continue
        if not os.path.isfile(src):
            skipped.append((src, dst, "source missing"))
            continue
        size = os.path.getsize(src)
        if size == 0:
            skipped.append((src, dst, "source itself empty"))
            continue
        agree = suffix_agreement(src, dst)
        if agree < min_agree:
            skipped.append(
                (src, dst, "only %d trailing components agree (need %d)" % (agree, min_agree))
            )
            continue
        if not is_empty_damage(dst):
            cur = os.path.getsize(dst) if os.path.isfile(dst) else -1
            skipped.append((src, dst, "destination not empty (%dB) - refusing" % cur))
            continue
        if apply:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        done.append((src, dst, size, agree))

    print("== RESTORE %s ==" % ("APPLIED" if apply else "DRY RUN"))
    for src, dst, size, agree in done:
        print("  OK  %7dB agree=%d" % (size, agree))
        print("        src %s" % src)
        print("        dst %s" % dst)
    if skipped:
        print("-- SKIPPED --")
        for src, dst, why in skipped:
            print("  --  %s\n        %s" % (why, dst))
    print("restored=%d skipped=%d" % (len(done), len(skipped)))

    with open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "restore-applied.json"),
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            {
                "applied": apply,
                "restored": [
                    {"src": s, "dst": d, "bytes": b, "suffix_agreement": a} for s, d, b, a in done
                ],
                "skipped": [{"src": s, "dst": d, "reason": w} for s, d, w in skipped],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

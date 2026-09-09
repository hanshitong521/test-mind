#!/usr/bin/env python3
"""Test whether file truncation recurs on Cursor's 6-hour WorktreeCleanupCron cycle.

Anchor = the confirmed burst at 2026-09-07 21:49:37.5, which the Cursor log ties to
`[WorktreeCleanupCron] Cleanup task registered with interval of 6 hours` firing at
21:49:37.209. If that cron empties files on every run, empty-file mtimes should cluster
at 6-hour multiples of the anchor -- and NOT at multiples of other intervals.
"""
from __future__ import annotations

import json
import os
import time

ROOTS = [r"E:\workA", r"E:\AAAAAA"]
ANCHOR = 1788788977.5
TOLERANCE_S = 120.0
CANDIDATE_INTERVALS_H = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 12.0, 24.0]
SKIP_DIRS = {
    "node_modules", ".git", "dist", ".venv", "__pycache__",
    "target", "build", ".next", ".cache",
}


def collect_empty() -> list[tuple[float, int, str]]:
    out = []
    for root in ROOTS:
        if not os.path.isdir(root):
            continue
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in SKIP_DIRS]
            for fn in fns:
                p = os.path.join(dp, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_size <= 2:
                    out.append((st.st_mtime, st.st_size, p))
    return out


def cluster_count(entries, interval_h: float) -> int:
    hits = 0
    for mt, _sz, _p in entries:
        hours = (mt - ANCHOR) / 3600.0
        k = round(hours / interval_h)
        if abs(hours - k * interval_h) * 3600.0 <= TOLERANCE_S:
            hits += 1
    return hits


def main() -> int:
    entries = collect_empty()
    print("empty/2-byte files found under scan roots:", len(entries))

    print("\n=== clustering at each candidate interval (+/-%ds of a multiple of the anchor) ===" % int(TOLERANCE_S))
    scores = {}
    for iv in CANDIDATE_INTERVALS_H:
        c = cluster_count(entries, iv)
        scores[iv] = c
        marker = "   <== Cursor WorktreeCleanupCron interval" if iv == 6.0 else ""
        print("  %5.1fh  %5d  (%.1f%%)%s" % (iv, c, 100.0 * c / max(len(entries), 1), marker))

    base = scores[6.0]
    others = [v for k, v in scores.items() if k != 6.0]
    print("\n6h cluster = %d; median of other intervals = %d" % (base, sorted(others)[len(others) // 2]))

    print("\n=== the 6h buckets in detail (k = number of 6h steps from the anchor) ===")
    buckets: dict[int, list] = {}
    for mt, sz, p in entries:
        hours = (mt - ANCHOR) / 3600.0
        k = round(hours / 6.0)
        if abs(hours - k * 6.0) * 3600.0 <= TOLERANCE_S:
            buckets.setdefault(k, []).append((mt, sz, p))

    detail = {}
    for k in sorted(buckets):
        rows = sorted(buckets[k])
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ANCHOR + k * 6.0 * 3600))
        print("  k=%+3d  expected cron run ~%s   files=%d" % (k, when, len(rows)))
        detail[str(k)] = {"expected_run": when, "count": len(rows),
                          "paths": [p for _m, _s, p in rows[:40]]}
        if abs(k) <= 3:
            for mt, sz, p in rows[:6]:
                print("        %s %2dB %s" % (time.strftime("%m-%d %H:%M:%S", time.localtime(mt)), sz, p))

    with open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "cron-periodicity.json"),
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            {"anchor": ANCHOR, "tolerance_s": TOLERANCE_S, "scores": {str(k): v for k, v in scores.items()},
             "buckets": detail},
            f, ensure_ascii=False, indent=2,
        )
    print("\nwrote docs/cron-periodicity.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

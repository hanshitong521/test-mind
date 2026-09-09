#!/usr/bin/env python3
"""Find every truncation burst and correlate it with Cursor session starts.

The user reports this happens repeatedly. Rather than assume a period, cluster all
empty/2-byte files by mtime, then check each cluster against the Cursor log session
directories (named yyyymmddTHHMMSS = the moment that window launched) and against the
WorktreeManager / WorktreeCleanupCron lines inside those logs.
"""
from __future__ import annotations

import glob
import json
import os
import re
import time

ROOTS = [r"E:\workA", r"E:\AAAAAA"]
CURSOR_LOGS = os.path.expanduser(r"~\AppData\Roaming\Cursor\logs")
GAP_S = 5.0          # empty files within this many seconds belong to one burst
MIN_BURST = 3        # ignore clusters smaller than this
SKIP_DIRS = {
    "node_modules", ".git", "dist", ".venv", "__pycache__",
    "target", "build", ".next", ".cache",
}

# Legitimately empty by design. Without this filter, test-run artifacts such as
# reports/<stamp>/sut.log register as "bursts" - the 2026-09-07 23:29 cluster was
# three sut.log files written by our own testmind runs, not truncation damage.
BENIGN_EMPTY = (
    "__init__.py",
    "sut.log",
    "schema-tests-summary.txt",
    ".gitkeep",
    ".keep",
)
BENIGN_DIRS = ("reports" + os.sep, "logs" + os.sep)


def is_benign(path: str) -> bool:
    base = os.path.basename(path).lower()
    if base in BENIGN_EMPTY:
        return True
    low = path.lower()
    return any(d in low for d in BENIGN_DIRS)


def collect_empty():
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
                if st.st_size <= 2 and not is_benign(p):
                    out.append((st.st_mtime, st.st_size, p))
    out.sort()
    return out


def cluster(entries):
    bursts, cur = [], []
    for e in entries:
        if cur and e[0] - cur[-1][0] > GAP_S:
            bursts.append(cur)
            cur = []
        cur.append(e)
    if cur:
        bursts.append(cur)
    return [b for b in bursts if len(b) >= MIN_BURST]


def cursor_sessions():
    """Map session-dir start epoch -> dir name."""
    out = []
    for d in glob.glob(os.path.join(CURSOR_LOGS, "*")):
        name = os.path.basename(d)
        m = re.match(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})$", name)
        if not m:
            continue
        import calendar
        t = time.strptime(name, "%Y%m%dT%H%M%S")
        out.append((calendar.timegm(t) - 8 * 3600, name, d))  # local CST -> epoch
    out.sort()
    return out


def worktree_lines(session_dir):
    hits = []
    for path in glob.glob(os.path.join(session_dir, "**", "*.log"), recursive=True):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "WorktreeManager" in line or "WorktreeCleanupCron" in line:
                        hits.append((os.path.basename(path), line.strip()[:150]))
        except OSError:
            continue
        if len(hits) > 6:
            break
    return hits


def main() -> int:
    entries = collect_empty()
    bursts = cluster(entries)
    sessions = cursor_sessions()
    print("empty/2-byte files: %d   bursts (>=%d files, gap<=%.0fs): %d"
          % (len(entries), MIN_BURST, GAP_S, len(bursts)))
    print("cursor log sessions found: %d" % len(sessions))

    report = []
    print("\n=== bursts, nearest preceding Cursor session start, and worktree evidence ===")
    for b in bursts:
        start, end = b[0][0], b[-1][0]
        zero = sum(1 for _m, s, _p in b if s == 0)
        # nearest session start at or before the burst
        prev = [s for s in sessions if s[0] <= start]
        near = prev[-1] if prev else None
        delta = (start - near[0]) if near else None
        line = "  %s .. %s  files=%-4d (0B=%d, 2B=%d)  span=%.3fs" % (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start)),
            time.strftime("%H:%M:%S", time.localtime(end)),
            len(b), zero, len(b) - zero, end - start,
        )
        print(line)
        if near:
            print("      nearest cursor session start: %s  (%.1f s earlier)" % (near[1], delta))
            wl = worktree_lines(near[2])
            for fn, txt in wl[:3]:
                print("        [%s] %s" % (fn[:34], txt))
            if not wl:
                print("        (no WorktreeManager/WorktreeCleanupCron lines in that session)")
        else:
            print("      no cursor session precedes this burst")
        roots = {}
        for _m, _s, p in b:
            top = p.split(os.sep)
            roots[os.sep.join(top[:3])] = roots.get(os.sep.join(top[:3]), 0) + 1
        for r, c in sorted(roots.items(), key=lambda kv: -kv[1])[:4]:
            print("        %-42s %d" % (r, c))
        report.append({
            "start": start, "end": end, "files": len(b), "zero_byte": zero,
            "cursor_session": near[1] if near else None,
            "seconds_after_session_start": delta,
            "paths": [p for _m, _s, p in b],
        })

    with open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "burst-history.json"),
        "w", encoding="utf-8",
    ) as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\nwrote docs/burst-history.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

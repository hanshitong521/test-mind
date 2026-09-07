"""Authoritative scan for the 2026-09-07 21:49 truncation burst.

A file counts as burst-damaged only when BOTH hold:
  * size is 0 bytes, or exactly 2 bytes of CRLF  (the two observed payloads)
  * mtime falls inside the burst window

The mtime test is what removes the false positives a size-only heuristic gives:
empty test logs (reports/*/sut.log), package __init__.py markers and other
legitimately-empty files are not damage.

  python scripts/scan_burst_damage.py            # scan the default roots
  python scripts/scan_burst_damage.py --json out.json
"""
import json
import os
import sys
import time

# 2026-09-07 21:49:37.0 .. 21:49:39.5 local -- covers both observed passes
# (0-byte pass .546->.580, then a 706ms gap, then the CRLF pass .286->.301 of :38)
WIN_START = time.mktime(time.strptime("2026-09-07 21:49:36", "%Y-%m-%d %H:%M:%S"))
WIN_END = time.mktime(time.strptime("2026-09-07 21:49:41", "%Y-%m-%d %H:%M:%S"))

ROOTS = [r"E:\workA\A-skill", r"E:\AAAAAA\ai-restriction-document",
         r"E:\workA\shejiuPro", r"E:\workA\shejiuTest"]
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
             "dist", "build", ".next", "target", ".context-compress", ".m2"}
SKIP_EXT = {".pyc", ".pyo", ".class", ".jar", ".png", ".jpg", ".jpeg", ".gif", ".ico",
            ".zip", ".gz", ".tar", ".db", ".sqlite", ".woff", ".woff2", ".ttf", ".pdf",
            ".exe", ".dll", ".so", ".mp4", ".log"}


def classify(path):
    """Return ('zero'|'crlf'|None, size)."""
    try:
        sz = os.path.getsize(path)
    except OSError:
        return None, -1
    if sz == 0:
        return "zero", 0
    if sz == 2:
        try:
            with open(path, "rb") as f:
                return ("crlf", 2) if f.read(2) == b"\r\n" else (None, 2)
        except OSError:
            return None, 2
    return None, sz


def scan(root):
    hits = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != ".git"]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in SKIP_EXT:
                continue
            p = os.path.join(dirpath, fn)
            if os.path.islink(p):
                continue
            scanned += 1
            kind, sz = classify(p)
            if kind is None:
                continue
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            if WIN_START <= mt <= WIN_END:
                hits.append({"path": p, "kind": kind, "size": sz, "mtime": mt})
    return scanned, hits


def main():
    out_json = None
    if "--json" in sys.argv:
        out_json = sys.argv[sys.argv.index("--json") + 1]

    all_hits = []
    total_scanned = 0
    print("=" * 86)
    print("2026-09-07 21:49 truncation burst -- authoritative scan")
    print("window: %s .. %s" % (time.ctime(WIN_START), time.ctime(WIN_END)))
    print("=" * 86)
    for root in ROOTS:
        if not os.path.isdir(root):
            print("\n%-42s (absent)" % root)
            continue
        scanned, hits = scan(root)
        total_scanned += scanned
        all_hits.extend(hits)
        print("\n%-42s scanned=%-7d burst-damaged=%d" % (root, scanned, len(hits)))
        by_tree = {}
        for h in hits:
            rel = os.path.relpath(h["path"], root)
            top = rel.split(os.sep)[0]
            by_tree.setdefault(top, []).append(h)
        for top in sorted(by_tree, key=lambda k: -len(by_tree[k])):
            hs = by_tree[top]
            z = sum(1 for x in hs if x["kind"] == "zero")
            print("   %-34s %3d  (0B=%d, CRLF=%d)" % (top, len(hs), z, len(hs) - z))

    # also report size-only damage OUTSIDE the window, so legitimately-empty
    # files are visible but clearly separated from the burst
    print("\n" + "=" * 86)
    zero = [h for h in all_hits if h["kind"] == "zero"]
    crlf = [h for h in all_hits if h["kind"] == "crlf"]
    print("TOTAL files scanned : %d" % total_scanned)
    print("TOTAL burst-damaged : %d   (0-byte=%d, CRLF=%d)" % (len(all_hits), len(zero), len(crlf)))
    if all_hits:
        ts = sorted(h["mtime"] for h in all_hits)
        print("mtime span          : %.6f .. %.6f  (%.3f s)"
              % (ts[0], ts[-1], ts[-1] - ts[0]))
        print("                      %s .. %s"
              % (time.strftime("%H:%M:%S", time.localtime(ts[0])),
                 time.strftime("%H:%M:%S", time.localtime(ts[-1]))))
        zt = sorted(h["mtime"] for h in zero)
        ct = sorted(h["mtime"] for h in crlf)
        if zt:
            print("  0-byte pass       : %d files in %.3f s (%.2f ms/file)"
                  % (len(zt), zt[-1] - zt[0], (zt[-1] - zt[0]) * 1000 / max(1, len(zt) - 1)))
        if ct:
            print("  CRLF pass         : %d files in %.3f s (%.2f ms/file)"
                  % (len(ct), ct[-1] - ct[0], (ct[-1] - ct[0]) * 1000 / max(1, len(ct) - 1)))
        if zt and ct:
            gap = ct[0] - zt[-1]
            overlap = sum(1 for a in zt for b in ct if abs(a - b) < 0.001)
            print("  gap between passes: %.3f s   temporal overlap=%d" % (gap, overlap))

    if out_json:
        json.dump({"window": [WIN_START, WIN_END], "scanned": total_scanned,
                   "hits": all_hits}, open(out_json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print("\nwrote %s" % out_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())

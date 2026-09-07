"""For every burst-damaged file, look for an intact same-name copy elsewhere.

This is the recovery route that worked for Token-Mind (24 of 28 files came from
its installed mirror). For each damaged file it reports the best candidate copy
by basename, with size and path, so a restore is a deliberate per-file decision
rather than a blind sync.

  python scripts/find_recovery_copies.py
  python scripts/find_recovery_copies.py --json docs/recovery-candidates.json
"""
import json
import os
import sys
import time
from collections import defaultdict

MANIFEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "docs", "burst-damage-20260907.json")
SEARCH_ROOTS = [r"E:\workA", r"E:\AAAAAA"]
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
             "dist", "build", ".next", "target", ".context-compress", ".m2",
             ".gradle", "site-packages"}
MAX_SIZE = 40 * 1024 * 1024


def build_index(names):
    """basename -> [(path, size, mtime)] for every non-empty match on disk."""
    idx = defaultdict(list)
    walked = 0
    for root in SEARCH_ROOTS:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != ".git"]
            for fn in filenames:
                if fn not in names:
                    continue
                p = os.path.join(dirpath, fn)
                if os.path.islink(p):
                    continue
                try:
                    sz = os.path.getsize(p)
                    mt = os.path.getmtime(p)
                except OSError:
                    continue
                walked += 1
                if sz > 2:  # an intact copy must not itself be truncated
                    idx[fn].append((p, sz, mt))
    return idx, walked


def main():
    out_json = None
    if "--json" in sys.argv:
        out_json = sys.argv[sys.argv.index("--json") + 1]

    man = json.load(open(MANIFEST, encoding="utf-8"))
    hits = man["hits"]
    names = {os.path.basename(h["path"]) for h in hits}
    print("=" * 86)
    print("recovery-copy search: %d damaged files, %d distinct basenames"
          % (len(hits), len(names)))
    print("searching %s" % ", ".join(SEARCH_ROOTS))
    print("=" * 86)
    idx, walked = build_index(names)
    print("candidate files examined: %d\n" % walked)

    recoverable, gone = [], []
    for h in sorted(hits, key=lambda x: x["path"]):
        p = h["path"]
        base = os.path.basename(p)
        cands = [c for c in idx.get(base, []) if os.path.realpath(c[0]) != os.path.realpath(p)]
        if cands:
            # biggest intact copy wins; report all so the choice stays explicit
            cands.sort(key=lambda c: -c[1])
            recoverable.append((p, h["kind"], cands))
        else:
            gone.append((p, h["kind"]))

    for p, kind, cands in recoverable:
        print("COPY  [%s] %s" % (kind, p))
        for cp, sz, mt in cands[:3]:
            print("        <- %8dB  %s  %s"
                  % (sz, time.strftime("%Y-%m-%d %H:%M", time.localtime(mt)), cp))
        if len(cands) > 3:
            print("        <- ... %d more" % (len(cands) - 3))
    print()
    for p, kind in gone:
        print("GONE  [%s] %s" % (kind, p))

    print("\n" + "=" * 86)
    print("damaged total       : %d" % len(hits))
    print("has an intact copy  : %d" % len(recoverable))
    print("no copy anywhere    : %d" % len(gone))
    if recoverable:
        print("recoverable bytes   : %d (largest copy per file)"
              % sum(cs[0][1] for _, _, cs in recoverable))

    if out_json:
        json.dump({
            "recoverable": [{"damaged": p, "kind": k,
                             "copies": [{"path": c[0], "size": c[1], "mtime": c[2]} for c in cs]}
                            for p, k, cs in recoverable],
            "gone": [{"damaged": p, "kind": k} for p, k in gone],
        }, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("wrote %s" % out_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())

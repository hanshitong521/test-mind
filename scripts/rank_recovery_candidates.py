"""Rank recovery candidates by how much of the path actually agrees.

Basename-only matching is dangerous: it paired dev-hub/README.md with an
unrelated 144KB README and several SKILL.md files with NexMind's 360KB
review/SKILL.md. Restoring from those would corrupt the files far worse than
leaving them empty. Here a candidate is only STRONG when the trailing path
components agree, which is the signature of a genuine mirror relationship.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANDS = os.path.join(HERE, "docs", "recovery-candidates.json")


def parts(p):
    return [x.lower() for x in p.replace("\\", "/").split("/") if x]


def suffix_agreement(a, b):
    n = 0
    for x, y in zip(reversed(a), reversed(b)):
        if x == y:
            n += 1
        else:
            break
    return n


def main():
    d = json.load(open(CANDS, encoding="utf-8"))
    rows = []
    for r in d["recoverable"]:
        dp = parts(r["damaged"])
        best = None
        for c in r["copies"]:
            n = suffix_agreement(dp, parts(c["path"]))
            if best is None or n > best[0] or (n == best[0] and c["size"] > best[1]["size"]):
                best = (n, c)
        rows.append((best[0], r["damaged"], best[1]))
    rows.sort(key=lambda x: (-x[0], x[1]))

    strong = [r for r in rows if r[0] >= 3]
    medium = [r for r in rows if r[0] == 2]
    weak = [r for r in rows if r[0] <= 1]

    for label, group in (("STRONG (>=3 trailing components agree -- genuine mirror)", strong),
                         ("MEDIUM (2 agree -- inspect before use)", medium),
                         ("WEAK (basename only -- DO NOT RESTORE)", weak)):
        print("\n" + "=" * 86)
        print("%s : %d" % (label, len(group)))
        print("=" * 86)
        for n, p, c in group:
            print("  damaged : %s" % p)
            print("  copy    : %s  (%dB, suffix=%d)" % (c["path"], c["size"], n))
            print()

    print("=" * 86)
    print("strong=%d medium=%d weak=%d  (total with any copy=%d, no copy=%d)"
          % (len(strong), len(medium), len(weak), len(rows), len(d["gone"])))
    print("strong recoverable bytes = %d" % sum(c["size"] for _, _, c in strong))
    out = os.path.join(HERE, "docs", "recovery-strong.json")
    json.dump([{"damaged": p, "copy": c} for _, p, c in strong],
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("wrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

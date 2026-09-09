import datetime
import glob
import json
import os
import urllib.parse

H = os.path.join(os.environ["APPDATA"], "Cursor", "User", "History")
SLASH = chr(92)

TARGETS = [
    "fixtures/demo-spring-project/docs/large-appendix.md",
    "src/brain_services/knowledge_service.py",
    "tests/acceptance/test_t1_t2_t3.py",
]

for want in TARGETS:
    print("=" * 74)
    print("TARGET", want)
    found = False
    for d in glob.glob(os.path.join(H, "*")):
        ej = os.path.join(d, "entries.json")
        if not os.path.exists(ej):
            continue
        try:
            m = json.loads(open(ej, encoding="utf-8").read())
        except Exception:
            continue
        res = m.get("resource", "")
        p = urllib.parse.unquote(res[len("file:///"):]) if res.startswith("file:///") else res
        if not p.replace(SLASH, "/").endswith(want):
            continue
        found = True
        rows = []
        for e in sorted(m.get("entries", []), key=lambda x: x.get("timestamp", 0)):
            sp = os.path.join(d, e["id"])
            if not os.path.exists(sp):
                continue
            ts = datetime.datetime.fromtimestamp(e.get("timestamp", 0) / 1000)
            body = open(sp, "rb").read()
            note = ""
            if want.endswith("knowledge_service.py"):
                for line in body.decode("utf-8", "replace").splitlines():
                    if "_MAX_FILE_BYTES" in line and "=" in line and "def" not in line:
                        note = line.strip()
                        break
            rows.append((ts, e["id"], len(body), e.get("source", ""), note))
        for ts, eid, sz, src, note in rows[-14:]:
            print("  %s  %-10s %9dB  %-20s %s"
                  % (ts.strftime("%Y-%m-%d %H:%M:%S"), eid, sz, src, note))
        if len(rows) > 14:
            print("  ... (%d earlier snapshots)" % (len(rows) - 14))
    if not found:
        print("  (no Cursor history)")

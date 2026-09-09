import glob
import json
import os
import urllib.parse

H = os.path.join(os.environ["APPDATA"], "Cursor", "User", "History")
SLASH = chr(92)


def norm(p):
    return p.replace(SLASH, "/")


targets = ["brain_mcp/server.py", "test_mcp_why_tools.py"]
for want in targets:
    print("=" * 70)
    print("TARGET", want)
    found = 0
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
        p = norm(p)
        if "project-brain-agent" not in p or not p.endswith(want):
            continue
        found += 1
        print(" ", p)
        for e in sorted(m.get("entries", []), key=lambda x: x.get("timestamp", 0)):
            sp = os.path.join(d, e["id"])
            sz = os.path.getsize(sp) if os.path.exists(sp) else -1
            mark = ""
            if sz > 0:
                t = open(sp, encoding="utf-8", errors="replace").read()
                mark = "HAS WHY_TOOLS" if "WHY_TOOLS" in t else "no WHY_TOOLS"
                ntools = t.count("@mcp.tool()")
                mark += " tools=%d" % ntools
            print("    %-12s %8dB  %-22s %s" % (e["id"], sz, e.get("source", ""), mark))
    if not found:
        print("  (no Cursor history)")

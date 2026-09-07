"""Diagnose context-compress sandbox root + index/search anomalies.

Answers, with measured results:
  1. Does the server's file sandbox follow its launch cwd?
  2. Does CLAUDE_PROJECT_DIR override it, and can it widen the sandbox?
  3. Does index(path) outside the sandbox fail, and does it fail loudly?
  4. Does the `source` filter on search actually scope results?
  5. What does discover really print?
"""
import json
import os
import subprocess
import sys
import time

NODE = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python311\Scripts\node.exe"
IDX = r"E:\workA\A-skill\Token-Mind\context-compress-main\dist\index.js"
TM = r"E:\workA\A-skill\test-Mind"
CC = r"E:\workA\A-skill\Token-Mind\context-compress-main"
TMP = os.path.join(TM, ".probe-tmp")


class C:
    def __init__(self, cwd, extra=None):
        env = {**os.environ, **(extra or {})}
        self.p = subprocess.Popen([NODE, IDX], cwd=cwd, env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
        self.i = 0
        self.s("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                              "clientInfo": {"name": "diag", "version": "1"}})
        self.r()
        self.n("notifications/initialized", {})

    def s(self, m, p):
        self.i += 1
        self.p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": self.i, "method": m,
                                        "params": p}) + "\n").encode())
        self.p.stdin.flush()

    def n(self, m, p):
        self.p.stdin.write((json.dumps({"jsonrpc": "2.0", "method": m, "params": p}) + "\n").encode())
        self.p.stdin.flush()

    def r(self, timeout=120):
        dl = time.time() + timeout
        while time.time() < dl:
            ln = self.p.stdout.readline()
            if not ln:
                return {"_closed": self.p.stderr.read().decode("utf-8", "replace")[:300]}
            m = json.loads(ln.decode("utf-8", "replace"))
            if m.get("id") == self.i:
                return m.get("result", m.get("error"))
        return {"_timeout": True}

    def call(self, tool, args):
        self.s("tools/call", {"name": tool, "arguments": args})
        res = self.r()
        if not isinstance(res, dict) or "content" not in res:
            return "<<%s>>" % json.dumps(res, ensure_ascii=False)[:200], True
        return "".join(b.get("text", "") for b in res["content"]), res.get("isError", False)

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def hr(t):
    print("\n" + "-" * 78 + "\n" + t + "\n" + "-" * 78)


def main():
    os.makedirs(TMP, exist_ok=True)
    inside = os.path.join(TM, "scripts", "mcp_smoke.py")
    outside = os.path.join(TM, ".probe-tmp", "probe.txt")
    with open(outside, "w", encoding="utf-8") as f:
        f.write("hello from probe-tmp\n")

    hr("Q1  sandbox follows launch cwd?  (cwd=test-Mind, no CLAUDE_PROJECT_DIR)")
    c = C(TM)
    o, e = c.call("execute_file", {"path": inside, "language": "python",
                                   "code": "print('LINES', len(FILE_CONTENT.splitlines()))"})
    print("  test-Mind file      isError=%s  out=%s" % (e, o.strip()[:110]))
    o2, e2 = c.call("execute_file", {"path": os.path.join(CC, "package.json"), "language": "python",
                                     "code": "print('LINES', len(FILE_CONTENT.splitlines()))"})
    print("  other-project file  isError=%s  out=%s" % (e2, o2.strip()[:110]))
    o3, _ = c.call("execute", {"language": "shell", "code": "cd"})
    print("  sandbox subprocess cwd reported by `cd`: %s" % o3.strip()[:110])
    c.close()

    hr("Q2  CLAUDE_PROJECT_DIR widens the sandbox?  (cwd=test-Mind, root=E:\\workA)")
    c = C(TM, {"CLAUDE_PROJECT_DIR": r"E:\workA"})
    o, e = c.call("execute_file", {"path": os.path.join(CC, "package.json"), "language": "python",
                                   "code": "print('LINES', len(FILE_CONTENT.splitlines()))"})
    print("  cross-project file  isError=%s  out=%s" % (e, o.strip()[:110]))
    o2, e2 = c.call("execute_file", {"path": r"C:\Users\Administrator\.qoder-cn\mcp.json",
                                     "language": "python", "code": "print('LINES', len(FILE_CONTENT.splitlines()))"})
    print("  off-root file (C:)  isError=%s  out=%s" % (e2, o2.strip()[:110]))
    c.close()

    hr("Q3  index(path) outside the sandbox: does it fail, and loudly?")
    c = C(CC)   # registered cwd -> sandbox = context-compress-main
    o, e = c.call("index", {"path": outside, "source": "Outside"})
    print("  index outside  isError=%s  out=%s" % (e, o.strip()[:200]))
    o, e = c.call("index", {"path": os.path.join(CC, "package.json"), "source": "Inside"})
    print("  index inside   isError=%s  out=%s" % (e, o.strip()[:160]))
    c.close()

    hr("Q4  does search's `source` filter actually scope?  (two clearly distinct sources)")
    c = C(TM)
    d1 = os.path.join(TM, ".probe-tmp", "s1.md")
    d2 = os.path.join(TM, ".probe-tmp", "s2.md")
    open(d1, "w", encoding="utf-8").write("# S1\n\nQUOKA_TOKEN_ONE unique alpha content here.\n")
    open(d2, "w", encoding="utf-8").write("# S2\n\nWALRS_TOKEN_TWO unique beta content here.\n")
    print("  index s1 ->", c.call("index", {"path": d1, "source": "SrcOne"})[0].strip()[:110])
    print("  index s2 ->", c.call("index", {"path": d2, "source": "SrcTwo"})[0].strip()[:110])
    o, _ = c.call("search", {"queries": ["QUOKA_TOKEN_ONE", "WALRS_TOKEN_TWO"]})
    print("  unscoped: one=%s two=%s" % ("QUOKA_TOKEN_ONE" in o, "WALRS_TOKEN_TWO" in o))
    o, _ = c.call("search", {"queries": ["QUOKA_TOKEN_ONE", "WALRS_TOKEN_TWO"], "source": "SrcOne"})
    print("  source=SrcOne: one=%s two=%s (two should be False)"
          % ("QUOKA_TOKEN_ONE" in o, "WALRS_TOKEN_TWO" in o))
    print("  raw scoped output (first 300):\n    " + o.strip()[:300].replace("\n", "\n    "))
    c.close()

    hr("Q5  what does discover actually print?")
    c = C(TM)
    c.call("index", {"path": d1, "source": "SrcOne"})
    o, e = c.call("discover", {})
    print("  isError=%s len=%d\n%s" % (e, len(o), o.strip()[:700]))
    c.close()

    for f in ("probe.txt", "s1.md", "s2.md"):
        p = os.path.join(TMP, f)
        if os.path.exists(p):
            os.remove(p)
    try:
        os.rmdir(TMP)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

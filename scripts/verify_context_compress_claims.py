"""Verify each behavioural claim made in skills/context-compress/SKILL.md.

Launches the server exactly as ~/.qoder-cn/mcp.json registers it (same command,
args and env), so what is tested is what Qoder will run.

IMPORTANT: `search` echoes every query as a "## <query>" heading even when it
found nothing, followed by "No results found." A naive `"needle" in output`
check therefore always passes. Every hit assertion here goes through real_hit(),
which requires an actual "--- [source] ---" attribution inside that query's
section and rejects "No results found.".
"""
import json
import os
import re
import subprocess
import sys
import time

MCP_JSON = os.path.expanduser(r"~\.qoder-cn\mcp.json")
TMP = r"E:\workA\A-skill\test-Mind\.probe-tmp"
FAILS = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (("  | " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


def sections(out):
    """Split search output into {query: body} using the '## <query>' headings."""
    parts = re.split(r"(?m)^##\s+", out)
    res = {}
    for p in parts[1:]:
        nl = p.find("\n")
        if nl < 0:
            continue
        res[p[:nl].strip()] = p[nl + 1:]
    return res


def real_hit(out, query, source=None):
    """True only if that query's section has a real attributed result."""
    body = sections(out).get(query, "")
    if not body or "No results found" in body:
        return False
    if "--- [" not in body:
        return False
    return source is None or ("--- [%s] ---" % source) in body


class C:
    def __init__(self, entry):
        env = {**os.environ, **(entry.get("env") or {})}
        self.p = subprocess.Popen(
            [entry["command"]] + entry.get("args", []), cwd=entry.get("cwd"), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.i = 0
        self.s("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                              "clientInfo": {"name": "cc-verify", "version": "1"}})
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

    def r(self, timeout=180):
        dl = time.time() + timeout
        while time.time() < dl:
            ln = self.p.stdout.readline()
            if not ln:
                raise RuntimeError("closed: " + self.p.stderr.read().decode("utf-8", "replace")[:400])
            m = json.loads(ln.decode("utf-8", "replace"))
            if m.get("id") == self.i:
                if "error" in m:
                    raise RuntimeError(json.dumps(m["error"], ensure_ascii=False)[:300])
                return m["result"]
        raise TimeoutError(timeout)

    def call(self, tool, args):
        self.s("tools/call", {"name": tool, "arguments": args})
        res = self.r()
        txt = "".join(b.get("text", "") for b in res.get("content", []))
        return txt, res.get("isError", False)

    def tools(self):
        self.s("tools/list", {})
        return sorted(t["name"] for t in self.r()["tools"])

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def main():
    cfg = json.load(open(MCP_JSON, encoding="utf-8"))
    E = cfg["mcpServers"]["context-compress"]
    root = (E.get("env") or {}).get("CLAUDE_PROJECT_DIR", "")
    os.makedirs(TMP, exist_ok=True)
    print("=" * 78)
    print("context-compress SKILL.md claim verification")
    print("registered: %s" % " ".join([E["command"]] + E["args"]))
    print("sandbox root (CLAUDE_PROJECT_DIR) = %s" % (root or "<unset -> cwd>"))
    print("=" * 78)
    check("C0 注册里显式设了 CLAUDE_PROJECT_DIR（否则文件沙箱会锁死在工具自身目录）",
          bool(root) and os.path.isdir(root), root)
    check("C0b 沙箱根覆盖 test-Mind 与 Token-Mind 两个项目",
          os.path.realpath(TMP).startswith(os.path.realpath(root))
          and os.path.realpath(r"E:\workA\A-skill\Token-Mind").startswith(os.path.realpath(root)),
          "root=%s" % root)

    c = C(E)
    try:
        doc = sorted(["batch_execute", "execute", "execute_file", "index", "search",
                      "fetch_and_index", "stats", "discover"])
        actual = c.tools()
        check("C1 SKILL.md 列的 8 个工具与服务器实际一致", actual == doc,
              "actual=%s" % ",".join(actual))

        # --- batch_execute: the documented PRIMARY tool -------------------
        out, err = c.call("batch_execute", {
            "commands": [
                {"label": "counts", "command": "seq 1 20000"},
                {"label": "needle-cmd", "command": "echo ZEBRA_MARKER_42 hidden in the middle"},
                {"label": "words", "command": "printf 'alpha beta gamma\\ndelta epsilon\\n'"},
            ],
            "queries": ["ZEBRA_MARKER_42", "delta epsilon"],
        })
        check("C2 batch_execute 一次跑 3 命令 + 2 查询，单往返返回", not err and out.strip() != "",
              "isError=%s out=%d chars" % (err, len(out)))
        check("C3 batch_execute 真的索引并检索到深埋的针（按 source 归属判定，不是回显）",
              real_hit(out, "ZEBRA_MARKER_42") and real_hit(out, "delta epsilon"),
              "zebra=%s delta=%s" % (real_hit(out, "ZEBRA_MARKER_42"), real_hit(out, "delta epsilon")))
        check("C4 20000 行原始输出没有灌进上下文（压缩生效）",
              len(out) < 20000, "returned=%d chars vs 20000 raw lines" % len(out))

        # ERROR INJECTION: if a malformed command list were silently accepted,
        # C2/C3 would prove nothing about the documented required fields.
        out2, err2 = c.call("batch_execute", {"commands": [{"label": "x"}], "queries": ["x"]})
        check("C5 注错对照：缺 command 字段会被拒（文档写的 required 是真的）",
              err2 is True, "isError=%s out=%s" % (err2, out2[:110].replace("\n", " ")))

        # --- execute ------------------------------------------------------
        out3, err3 = c.call("execute", {
            "language": "javascript",
            "code": "const a=[...Array(5000)].map((_,i)=>i+1);"
                    "console.log('median',a[2500],'sum',a.reduce((x,y)=>x+y,0));",
        })
        check("C6 execute(javascript) 只有 stdout 进上下文",
              not err3 and "median 2501" in out3 and "sum 12502500" in out3,
              "out=%s" % out3.strip()[:80])

        # --- execute_file + FILE_CONTENT ----------------------------------
        fp = os.path.join(TMP, "data.csv")
        with open(fp, "w", encoding="utf-8") as f:
            f.write("id,name,score\n")
            for i in range(1, 3001):
                f.write("%d,row%d,%d\n" % (i, i, i * 3))
        out4, err4 = c.call("execute_file", {
            "path": fp, "language": "python",
            "code": "import statistics\nL=[l for l in FILE_CONTENT.splitlines()[1:] if l]\n"
                    "v=[int(l.split(',')[2]) for l in L]\n"
                    "print('rows',len(v),'max',max(v),'mean',round(statistics.mean(v),1))",
        })
        check("C7 execute_file 用 FILE_CONTENT 处理项目内文件（3000 行不进上下文）",
              not err4 and "rows 3000" in out4 and "max 9000" in out4,
              "isError=%s out=%s" % (err4, out4.strip()[:100]))

        # ERROR INJECTION: the guard must still reject paths off the root,
        # otherwise C7 passing would just mean the sandbox is wide open.
        out4b, err4b = c.call("execute_file", {
            "path": r"C:\Users\Administrator\.qoder-cn\mcp.json", "language": "python",
            "code": "print('LEAK', len(FILE_CONTENT))"})
        check("C7b 注错对照：沙箱根之外的文件仍被拒（护栏没被放开）",
              err4b is True and "outside the project directory" in out4b,
              "isError=%s out=%s" % (err4b, out4b.strip()[:100]))

        # --- index(path) + search(queries, source) ------------------------
        d1 = os.path.join(TMP, "alpha.md")
        d2 = os.path.join(TMP, "beta.md")
        with open(d1, "w", encoding="utf-8") as f:
            f.write("# Alpha\n\n## Refund policy\n\nThe QUOKKA_REFUND token allows full refund within 30 days.\n")
        with open(d2, "w", encoding="utf-8") as f:
            f.write("# Beta\n\n## Shipping\n\nThe WALRUS_SHIPPING token describes overnight delivery.\n")
        i1, e1 = c.call("index", {"path": d1, "source": "AlphaDocs"})
        i2, e2 = c.call("index", {"path": d2, "source": "BetaDocs"})
        check("C8a index(path) 对两个项目内文件都成功（不是静默失败）",
              not e1 and not e2 and "AlphaDocs" in i1 and "BetaDocs" in i2,
              "i1=%s | i2=%s" % (i1.strip()[:60], i2.strip()[:60]))

        qa, qb = "QUOKKA_REFUND refund policy", "WALRUS_SHIPPING delivery"
        out5, err5 = c.call("search", {"queries": [qa, qb], "limit": 3})
        check("C8b 一次 search 批量两个查询，两个 source 都真实命中",
              not err5 and real_hit(out5, qa, "AlphaDocs") and real_hit(out5, qb, "BetaDocs"),
              "alpha=%s beta=%s" % (real_hit(out5, qa, "AlphaDocs"), real_hit(out5, qb, "BetaDocs")))

        out6, _ = c.call("search", {"queries": [qa, qb], "source": "AlphaDocs"})
        check("C9 source 过滤真的生效（限定 AlphaDocs 就查不到 BetaDocs 的内容）",
              real_hit(out6, qa, "AlphaDocs") and not real_hit(out6, qb),
              "alpha=%s beta-real-hit=%s" % (real_hit(out6, qa, "AlphaDocs"), real_hit(out6, qb)))

        out7, err7 = c.call("discover", {})
        check("C10 discover 列出已索引的 source 与 chunk 数",
              not err7 and "AlphaDocs" in out7 and "BetaDocs" in out7,
              "AlphaDocs=%s BetaDocs=%s len=%d" % ("AlphaDocs" in out7, "BetaDocs" in out7, len(out7)))

        out8, err8 = c.call("stats", {})
        check("C11 stats 返回上下文消耗统计（有实测数字）",
              not err8 and "Tool calls" in out8, "len=%d head=%s" % (len(out8), out8.strip().replace("\n", " ")[:90]))

        # --- the documented `intent` behaviour ----------------------------
        q9 = "PENGUIN_INTENT_MARKER"
        out9, err9 = c.call("execute", {
            "language": "shell",
            "code": "for i in $(seq 1 4000); do echo \"line $i PENGUIN_INTENT_MARKER payload payload\"; done",
            "intent": "find the PENGUIN_INTENT_MARKER lines",
        })
        check("C12 intent + 大输出：4000 行没有整块返回（走了索引/预览路径）",
              not err9 and len(out9) < 40000, "returned=%d chars for 4000 raw lines" % len(out9))
        out10, _ = c.call("search", {"queries": [q9]})
        check("C13 intent 索引的输出之后能被 search 真实检索到",
              real_hit(out10, q9), "real_hit=%s" % real_hit(out10, q9))

        out11, err11 = c.call("index", {"content": "x", "path": d1})
        check("C14 注错对照：index 同时给 content 和 path 会被拒（exactly one 是真的）",
              err11 is True and "exactly one" in out11.lower(),
              "isError=%s out=%s" % (err11, out11[:110].replace("\n", " ")))

        # --- the documented browser-use handoff ---------------------------
        snap = os.path.join(TMP, "snap.txt")
        with open(snap, "w", encoding="utf-8") as f:
            f.write("# Page snapshot\n\n- textbox 'Email' id=user_email\n"
                    "- button 'Submit order' id=place_order\n- text CAPYBARA_CHECKOUT_ERR\n")
        i3, e3 = c.call("index", {"path": snap, "source": "CheckoutPage"})
        qs = "CAPYBARA_CHECKOUT_ERR"
        out12, _ = c.call("search", {"queries": [qs], "source": "CheckoutPage"})
        check("C15 文档写的 browser 落盘流程可行：filePath -> index(path) -> search(source)",
              not e3 and real_hit(out12, qs, "CheckoutPage"),
              "indexed=%s real_hit=%s" % (not e3, real_hit(out12, qs, "CheckoutPage")))
    finally:
        c.close()
        for f in ("data.csv", "alpha.md", "beta.md", "snap.txt"):
            p = os.path.join(TMP, f)
            if os.path.exists(p):
                os.remove(p)
        try:
            os.rmdir(TMP)
        except OSError:
            pass

    print("=" * 78)
    print("FAILED: %d" % len(FAILS))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

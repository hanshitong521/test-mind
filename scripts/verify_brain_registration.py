"""Verify the project-brain entry in Qoder's real mcp.json, end to end.

Reads ~/.qoder-cn/mcp.json and launches the server with EXACTLY the registered
command/args/cwd/env, so what is tested is what Qoder will run.

Alias proof is READ-ONLY: search_project_context echoes the resolved project_id,
so querying "shejiuTest" must come back as "shejiuPro" without writing anything
to the user's real 48KB memory pool.
"""
import json
import os
import subprocess
import sys
import time

MCP_JSON = os.path.expanduser(r"~\.qoder-cn\mcp.json")
BRAIN = r"E:\workA\A-skill\project-brain-agent"
MEMDIR = os.path.join(BRAIN, ".data", "memories")
REAL_POOL = os.path.join(MEMDIR, "shejiuPro.jsonl")
FAILS = []


def probe_term():
    """从真实池里取一个必然存在的检索词。

    硬编码 '口径' 会随池内容变更而假红（数据耦合 flaky：池里已无该词 → count=0）。
    改成运行时从池里现取一个词，既保留"必须命中真实池、不是空壳"的标准，
    又不依赖某条具体记忆恰好还在。
    """
    try:
        with open(REAL_POOL, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                for field in ("title", "memory"):
                    t = (rec.get(field) or "").strip()
                    if t:
                        return t[:10]
    except Exception:
        pass
    return None


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (("  | " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


class Client:
    def __init__(self, entry):
        env = {**os.environ, **(entry.get("env") or {})}
        self.cmd = [entry["command"], *entry.get("args", [])]
        self.p = subprocess.Popen(
            self.cmd, cwd=entry.get("cwd"), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.id = 0
        self._send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                  "clientInfo": {"name": "regverify", "version": "1"}})
        init = self._read()
        self.info = init.get("serverInfo", {})
        self._notify("notifications/initialized", {})

    def _send(self, method, params):
        self.id += 1
        self.p.stdin.write((json.dumps(
            {"jsonrpc": "2.0", "id": self.id, "method": method, "params": params}
        ) + "\n").encode("utf-8"))
        self.p.stdin.flush()
        return self.id

    def _notify(self, method, params):
        self.p.stdin.write((json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params}) + "\n").encode("utf-8"))
        self.p.stdin.flush()

    def _read(self, timeout=90):
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("stdout closed: " +
                                   self.p.stderr.read().decode("utf-8", "replace")[:500])
            line = line.decode("utf-8", "replace").strip()
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("id") == self.id:
                if "error" in msg:
                    raise RuntimeError(json.dumps(msg["error"], ensure_ascii=False)[:300])
                return msg["result"]
        raise TimeoutError("no response in %ss" % timeout)

    def tools(self):
        self._send("tools/list", {})
        return [t["name"] for t in self._read()["tools"]]

    def call(self, tool, args):
        self._send("tools/call", {"name": tool, "arguments": args})
        return json.loads(self._read()["content"][0]["text"])

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def main():
    print("=" * 78)
    with open(MCP_JSON, encoding="utf-8") as fh:
        cfg = json.load(fh)
    servers = cfg["mcpServers"]
    check("R1 mcp.json 可解析，已注册 %d 个 server: %s"
          % (len(servers), ", ".join(sorted(servers))), "project-brain" in servers)
    entry = servers["project-brain"]
    check("R2 注册的解释器真实存在", os.path.exists(entry["command"]), entry["command"])
    check("R3 PYTHONPATH 指向真实 src", os.path.isdir(entry["env"]["PYTHONPATH"]),
          entry["env"]["PYTHONPATH"])

    print("launching exactly as registered:\n  cwd=%s\n  %s"
          % (entry.get("cwd"), " ".join([entry["command"]] + entry.get("args", []))))
    print("=" * 78)

    before = os.path.getsize(REAL_POOL)
    before_mtime = os.path.getmtime(REAL_POOL)
    files_before = set(os.listdir(MEMDIR))

    c = Client(entry)
    try:
        # 检索词从真实池现取（避免硬编码词随池内容变更而假红）；池为空才退回默认词
        query = probe_term() or "口径"
        check("R4 initialize 握手成功", c.info.get("name") == "project-brain",
              json.dumps(c.info, ensure_ascii=False))
        tl = c.tools()
        check("R5 tools/list 返回 5 个工具", len(tl) == 5, ", ".join(sorted(tl)))

        aliases = entry["env"].get("BRAIN_PROJECT_ALIASES", "")
        pairs = [p.split("=", 1) for p in aliases.split(",") if "=" in p]
        check("R6 注册里配置了别名映射", len(pairs) > 0, aliases)

        for src, dst in pairs:
            # READ-ONLY alias proof: the response echoes the RESOLVED project_id.
            r = c.call("search_project_context",
                       {"project_id": src.strip(), "query": query, "limit": 3})
            check("R7 别名生效：%s -> %s（返回值里的 project_id 已被解析）"
                  % (src.strip(), dst.strip()),
                  r.get("project_id") == dst.strip(),
                  "returned project_id=%s memories=%s" % (r.get("project_id"), r.get("count")))
            # It must reach the REAL shared pool, not an empty lookalike.
            check("R8 %s 真的读到了共享池 %s 的真实记忆（不是空壳）"
                  % (src.strip(), dst.strip()),
                  r.get("count", 0) > 0,
                  "count=%s doc_count=%s query=%r" % (r.get("count"), r.get("doc_count"), query))

        # NEGATIVE CONTROL: an id outside the alias map must NOT be rewritten.
        #   Without this, R7 would also pass if every id were flattened to shejiuPro.
        bogus = "zz-unaliased-probe"
        rb = c.call("search_project_context", {"project_id": bogus, "query": query, "limit": 3})
        check("R9 注错对照：未配别名的 id 不被改写（仍返回自己，且报 unknown）",
              rb.get("project_id") == bogus
              and (rb.get("project") or {}).get("error") == "unknown project_id"
              and rb.get("count", 0) == 0,
              "project_id=%s project.error=%s count=%s"
              % (rb.get("project_id"), (rb.get("project") or {}).get("error"), rb.get("count")))

        # Default-id fallback: empty project_id must land in the configured default.
        rd = c.call("search_project_context", {"project_id": "", "query": query, "limit": 3})
        check("R10 空 project_id 落到 BRAIN_DEFAULT_PROJECT_ID=%s"
              % entry["env"]["BRAIN_DEFAULT_PROJECT_ID"],
              rd.get("project_id") == entry["env"]["BRAIN_DEFAULT_PROJECT_ID"]
              and rd.get("count", 0) > 0,
              "project_id=%s count=%s query=%r" % (rd.get("project_id"), rd.get("count"), query))

        # get_change_context must resolve the alias too (it is a separate code path).
        rc = c.call("get_change_context", {"project_id": pairs[0][0].strip(),
                                           "file": "src/main/java/Demo.java", "limit": 3})
        check("R11 get_change_context 也走别名（另一条代码路径同样收敛）",
              rc.get("project_id") == pairs[0][1].strip(),
              "project_id=%s repo_root=%s" % (rc.get("project_id"), rc.get("repo_root")))
    finally:
        c.close()

    # The whole verification must have been read-only against real memory.
    after = os.path.getsize(REAL_POOL)
    check("R12 全程只读：真实记忆池 shejiuPro.jsonl 字节数未变",
          before == after, "%dB -> %dB" % (before, after))
    check("R13 全程只读：shejiuPro.jsonl mtime 未变",
          before_mtime == os.path.getmtime(REAL_POOL),
          "%s -> %s" % (time.ctime(before_mtime), time.ctime(os.path.getmtime(REAL_POOL))))
    new = set(os.listdir(MEMDIR)) - files_before
    check("R14 全程只读：没有新建任何记忆 jsonl（未配别名的探针 id 也没落盘）",
          not new, "new files=%s" % (sorted(new) or "none"))

    print("=" * 78)
    print("FAILED: %d" % len(FAILS))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

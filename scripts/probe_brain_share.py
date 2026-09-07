"""Black-box probe of project-brain cross-project memory sharing.

Spawns the REAL MCP server over stdio from two different working directories.
Never imports brain_* internals. Uses throwaway project_ids only, so the user's
real .data/memories/shejiuPro.jsonl is never touched.

Baseline mode (no aliases):   python probe_brain_share.py
Alias mode (after the patch):  python probe_brain_share.py --alias
"""
import json
import os
import subprocess
import sys
import time

BRAIN = r"E:\workA\A-skill\project-brain-agent"
PYE = os.path.join(BRAIN, ".venv", "Scripts", "python.exe")
CWD_A = r"E:\workA\A-skill\test-Mind"
CWD_B = r"E:\workA\A-skill\Token-Mind"
MEMDIR = os.path.join(BRAIN, ".data", "memories")

RUN = str(int(time.time()))[-6:]
FAILS = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (("  | " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


class Client:
    """One MCP server process, driven over stdio from a chosen cwd."""

    def __init__(self, cwd, extra_env=None):
        env = {
            **os.environ,
            "PYTHONPATH": os.path.join(BRAIN, "src"),
            "PYTHONIOENCODING": "utf-8",
        }
        if extra_env:
            env.update(extra_env)
        self.p = subprocess.Popen(
            [PYE, "-u", "-m", "brain_mcp.server"],
            cwd=cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.id = 0
        self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "probe", "version": "1"},
        })
        self._read()
        self._notify("notifications/initialized", {})

    def _send(self, method, params):
        self.id += 1
        msg = {"jsonrpc": "2.0", "id": self.id, "method": method, "params": params}
        self.p.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.p.stdin.flush()
        return self.id

    def _notify(self, method, params):
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        self.p.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.p.stdin.flush()

    def _read(self, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("server closed stdout: " +
                                   self.p.stderr.read().decode("utf-8", "replace")[:400])
            line = line.decode("utf-8", "replace").strip()
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("id") == self.id:
                if "error" in msg:
                    raise RuntimeError(json.dumps(msg["error"], ensure_ascii=False)[:300])
                return msg["result"]
        raise TimeoutError("no response in %ss" % timeout)

    def call(self, tool, args):
        self._send("tools/call", {"name": tool, "arguments": args})
        res = self._read()
        txt = res["content"][0]["text"]
        return json.loads(txt)

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def files_for(pid):
    return os.path.join(MEMDIR, pid + ".jsonl")


def cleanup(pids):
    for pid in pids:
        p = files_for(pid)
        if os.path.exists(p):
            os.remove(p)


def main():
    alias_mode = "--alias" in sys.argv
    print("=" * 78)
    print("project-brain cross-project memory sharing probe  (run=%s, mode=%s)"
          % (RUN, "alias" if alias_mode else "baseline"))
    print("server cwd A = %s\nserver cwd B = %s" % (CWD_A, CWD_B))
    print("=" * 78)

    if not alias_mode:
        a = "zzprobe-a-" + RUN
        b = "zzprobe-b-" + RUN
        cleanup([a, b])
        na = "zzprobeAlpha" + RUN
        nb = "zzprobeBeta" + RUN
        try:
            # T1: write under id A, from cwd A (test-Mind).
            c = Client(CWD_A)
            r = c.call("record_task_outcome", {
                "project_id": a,
                "summary": "shared-memory probe A: token budget fix %s" % na,
            })
            c.close()
            check("T1 记录到 project_id=%s（cwd=test-Mind）" % a[:22],
                  isinstance(r, dict) and not r.get("error"), json.dumps(r, ensure_ascii=False)[:90])

            # T2: write under id B, from cwd B (Token-Mind) -- the other AI's pool.
            c = Client(CWD_B)
            r = c.call("record_task_outcome", {
                "project_id": b,
                "summary": "shared-memory probe B: redis timeout fix %s" % nb,
            })
            c.close()
            check("T2 记录到 project_id=%s（cwd=Token-Mind）" % b[:22],
                  isinstance(r, dict) and not r.get("error"), json.dumps(r, ensure_ascii=False)[:90])

            # T3: a FRESH server process, launched from the OTHER cwd, must see A's memory.
            #     This is the actual sharing claim: different cwd + different process.
            c = Client(CWD_B)
            hit = c.call("search_project_context", {"project_id": a, "query": "token budget"})
            c.close()
            mems = json.dumps(hit.get("memories", []), ensure_ascii=False)
            check("T3 跨进程跨cwd 能读回 A 的记忆（记忆池与 cwd 无关）",
                  na in mems, "hits=%s found_needle=%s" % (hit.get("count"), na in mems))

            # T4 NEGATIVE CONTROL: query B with B's OWN keywords, so a non-match can only
            #     mean isolation -- not that the search found nothing at all.
            c = Client(CWD_A)
            miss = c.call("search_project_context", {"project_id": b, "query": "redis timeout"})
            c.close()
            bmems = json.dumps(miss.get("memories", []), ensure_ascii=False)
            check("T4 注错对照：B 池查自己的关键词能命中，但查不到 A 的针",
                  na not in bmems and nb in bmems,
                  "hits=%s A-needle-in-B=%s  B-own-needle=%s"
                  % (miss.get("count"), na in bmems, nb in bmems))

            # T5: the two ids really are two separate files on disk.
            check("T5 磁盘上是两个独立 jsonl（不是同一文件的假象）",
                  os.path.exists(files_for(a)) and os.path.exists(files_for(b))
                  and os.path.getsize(files_for(a)) > 0 and os.path.getsize(files_for(b)) > 0,
                  "A=%sB B=%sB" % (
                      os.path.getsize(files_for(a)) if os.path.exists(files_for(a)) else -1,
                      os.path.getsize(files_for(b)) if os.path.exists(files_for(b)) else -1))
        finally:
            cleanup([a, b])
            check("T6 清理探针 jsonl，未污染用户真实记忆",
                  not os.path.exists(files_for(a)) and not os.path.exists(files_for(b)),
                  "shejiuPro.jsonl 未触碰=%s" % os.path.exists(os.path.join(MEMDIR, "shejiuPro.jsonl")))
    else:
        shared = "zzprobe-shared-" + RUN
        p1 = "zzprobe-p1-" + RUN
        p2 = "zzprobe-p2-" + RUN
        lonely = "zzprobe-lonely-" + RUN
        cleanup([shared, p1, p2, lonely])
        n1 = "zzaliasOne" + RUN
        n2 = "zzaliasTwo" + RUN
        n3 = "zzlonelyThree" + RUN
        alias_env = {"BRAIN_PROJECT_ALIASES": "%s=%s,%s=%s" % (p1, shared, p2, shared)}
        try:
            # A1: "AI in project 1" writes under its own physical id p1.
            c = Client(CWD_A, alias_env)
            c.call("record_task_outcome", {"project_id": p1, "summary": "alias probe p1: %s" % n1})
            c.close()
            # A2: "AI in project 2", different cwd, different physical id p2.
            c = Client(CWD_B, alias_env)
            c.call("record_task_outcome", {"project_id": p2, "summary": "alias probe p2: %s redis" % n2})
            c.close()

            # A3: querying p1 must surface p2's memory -> the two AIs share one brain.
            c = Client(CWD_B, alias_env)
            h1 = c.call("search_project_context", {"project_id": p1, "query": "redis"})
            c.close()
            m1 = json.dumps(h1.get("memories", []), ensure_ascii=False)
            check("A3 p1 能读到 p2 写的记忆（不同项目名 -> 同一记忆池）",
                  n2 in m1, "p1_hits=%s p2-needle-in-p1=%s" % (h1.get("count"), n2 in m1))

            # A4: symmetric direction.
            c = Client(CWD_A, alias_env)
            h2 = c.call("search_project_context", {"project_id": p2, "query": "alias probe"})
            c.close()
            m2 = json.dumps(h2.get("memories", []), ensure_ascii=False)
            check("A4 反向也成立：p2 能读到 p1 写的记忆",
                  n1 in m2, "p2_hits=%s p1-needle-in-p2=%s" % (h2.get("count"), n1 in m2))

            # A5: both must land in the SHARED file, and no per-physical-id file may exist.
            check("A5 记忆落在别名目标 jsonl，且没有为物理 id 另开文件",
                  os.path.exists(files_for(shared)) and not os.path.exists(files_for(p1))
                  and not os.path.exists(files_for(p2)),
                  "shared=%sB p1_file=%s p2_file=%s" % (
                      os.path.getsize(files_for(shared)) if os.path.exists(files_for(shared)) else -1,
                      os.path.exists(files_for(p1)), os.path.exists(files_for(p2))))

            # A6 NEGATIVE CONTROL: an id NOT in the alias map must stay isolated.
            #     Query lonely with its OWN keywords, so a non-match means isolation
            #     rather than "the search matched nothing at all".
            c = Client(CWD_A, alias_env)
            c.call("record_task_outcome", {"project_id": lonely, "summary": "lonely probe: %s" % n3})
            hl = c.call("search_project_context", {"project_id": lonely, "query": "lonely probe"})
            c.close()
            ml = json.dumps(hl.get("memories", []), ensure_ascii=False)
            check("A6 注错对照：未配别名的 id 仍隔离（自己命中，看不到共享池）",
                  n3 in ml and n1 not in ml and n2 not in ml,
                  "own=%s shared-leak-in=%s/%s" % (n3 in ml, n1 in ml, n2 in ml))
            # A7: the reverse leak check, querying shared with keywords it DOES match.
            c = Client(CWD_B, alias_env)
            hs = c.call("search_project_context", {"project_id": shared, "query": "alias probe"})
            c.close()
            ms = json.dumps(hs.get("memories", []), ensure_ascii=False)
            check("A7 注错对照反向：共享池命中自己的记忆，但查不到 lonely 的",
                  n3 not in ms and n1 in ms,
                  "hits=%s lonely-in-shared=%s p1-in-shared=%s"
                  % (hs.get("count"), n3 in ms, n1 in ms))

            # A8: a malformed alias string must not take the server down.
            c = Client(CWD_A, {"BRAIN_PROJECT_ALIASES": "  ,, = , x= "})
            r = c.call("record_task_outcome", {"project_id": "zzprobe-junk-" + RUN, "summary": "junk alias probe"})
            c.close()
            check("A8 畸形别名串不炸服务（仍正常写入）",
                  isinstance(r, dict) and not r.get("error"), json.dumps(r, ensure_ascii=False)[:80])
            cleanup(["zzprobe-junk-" + RUN])
        finally:
            cleanup([shared, p1, p2, lonely])
            check("A9 清理全部探针 jsonl",
                  not any(os.path.exists(files_for(x)) for x in (shared, p1, p2, lonely)),
                  "shejiuPro.jsonl 仍在=%s" % os.path.exists(os.path.join(MEMDIR, "shejiuPro.jsonl")))

    print("=" * 78)
    print("FAILED: %d" % len(FAILS))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())

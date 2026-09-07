# scripts/verify_restored.py — 独立验收：sut 自动拉起 + risk_floor 终判地板
# 黑盒：只经 stdio JSON-RPC 打真实 mcp.py 子进程，不 import testmind 内部。
# 判据：正向必须真活（用仓库外的独立目标 + 外部探测），注错必须红（BLOCKED/NOT_TESTED，绝不假 PASS）。
# 每个场景 try/finally 收尾，失败也不留孤儿进程。
import atexit, json, os, socket, subprocess, sys, tempfile, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP = os.path.join(ROOT, "testmind", "mcp.py")
PY = sys.executable
RUN = str(os.getpid())
results, clients = [], []


def free_port(start=18400):
    """找一个真没人用的端口。Windows 上 SO_REUSEADDR 会允许绑定已被监听的端口，绝不能用它探测。"""
    for p in range(start, start + 600):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                continue
        with socket.socket() as t:
            t.settimeout(0.3)
            if t.connect_ex(("127.0.0.1", p)) == 0:
                continue
        return p
    raise RuntimeError("no free port")


def port_open(port, timeout=1.5):
    with socket.socket() as s:
        s.settimeout(timeout)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ps(cmd):
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                          capture_output=True, text=True).stdout.strip()


def orphans(marker):
    """按命令行唯一标记找残留进程；排除查询自身（powershell/cmd 的命令行里也含该标记）。"""
    out = ps("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*" + marker +
             "*' -and $_.Name -ne 'powershell.exe' -and $_.Name -ne 'cmd.exe' } | "
             "ForEach-Object { $_.ProcessId }")
    return [int(x) for x in out.split() if x.strip().isdigit()]


def sweep():
    for c in list(clients):
        try:
            c.hard_close()
        except Exception:
            pass
    for m in (f"sutmark-{RUN}",):
        for pid in orphans(m):
            ps(f"Stop-Process -Id {pid} -Force")


atexit.register(sweep)


class Client:
    """stdio JSON-RPC 客户端：每行一条请求，读一条响应。"""

    def __init__(self):
        self.p = subprocess.Popen([PY, MCP], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
        self.id = 0
        clients.append(self)
        self.call("initialize")

    def call(self, name, args=None):
        self.id += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.id, "method": "tools/call",
                                       "params": {"name": name, "arguments": args or {}}}) + "\n")
        self.p.stdin.flush()
        while True:
            ln = self.p.stdout.readline()
            if not ln:
                raise RuntimeError("server died: " + (self.p.stderr.read() or "")[:800])
            d = json.loads(ln)
            if d.get("id") == self.id:
                return json.loads(d["result"]["content"][0]["text"])

    def done(self):
        self.call("cleanup")
        self.hard_close()

    def hard_close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=20)
        except Exception:
            self.p.kill()
        if self in clients:
            clients.remove(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self.call("cleanup")
        except Exception:
            pass
        self.hard_close()


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)


# ============================== T1 正向：拉起一个仓库外的独立服务 ==============================
P1 = free_port()
tmpd = tempfile.mkdtemp(prefix="sut-target-")
open(os.path.join(tmpd, "probe.txt"), "w").write("independent-target")
M1 = f"sutmark-{RUN}-t1"
with Client() as c:
    r = c.call("prepare_environment", {"sut": {
        "command": f'"{PY}" -m http.server {P1} --directory "{tmpd}" & rem {M1}',
        "health_check": {"port": P1, "timeout_s": 40}}})
    check("T1a sut 自动拉起 = PASS", r["status"] == "PASS", r["summary"][:120])
    check("T1b base_url 从 health_check 正确推导", f"http://127.0.0.1:{P1}" in r["summary"], r["summary"][:120])
    body = None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{P1}/probe.txt", timeout=5) as resp:
            body = resp.read().decode()
    except Exception as e:
        body = f"ERR {e!r}"
    check("T1c 外部独立探测：服务真的活着（不是自证）", body == "independent-target", repr(body)[:80])
time.sleep(2.0)
check("T1d cleanup 后进程树被回收（端口已释放）", not port_open(P1), f"port {P1}")
check("T1e cleanup 后无残留 http.server 进程", orphans(M1) == [], str(orphans(M1)))

# ============================== T2 注错：进程秒退 → 必须 BLOCKED ==============================
P2 = free_port(P1 + 1)
with Client() as c:
    r = c.call("prepare_environment", {"sut": {
        "command": f'"{PY}" -c "import sys; sys.exit(7)  # sutmark-{RUN}-t2"',
        "health_check": {"port": P2, "timeout_s": 25}}})
    check("T2 秒退 = BLOCKED 并报退出码（绝不带死服务前进）",
          r["status"] == "BLOCKED" and "sut exited early code=7" in r["summary"],
          f"{r['status']}: {r['summary'][:120]}")

# ============================== T3 注错：活着但端口不开 → 超时 BLOCKED，不留孤儿 ==============================
P3 = free_port(P2 + 1)
M3 = f"sutmark-{RUN}-t3"
with Client() as c:
    t0 = time.time()
    r = c.call("prepare_environment", {"sut": {
        "command": f'"{PY}" -c "import time; time.sleep(120)" & rem {M3}',
        "health_check": {"port": P3, "timeout_s": 4}}})
    el = time.time() - t0
    check("T3a 端口不开 = BLOCKED timeout（不是假 PASS）",
          r["status"] == "BLOCKED" and "health check timeout" in r["summary"],
          f"{r['status']}: {r['summary'][:120]}")
    check("T3b 按 timeout_s 及时放弃（不空等 120s）", el < 20, f"elapsed={el:.1f}s")
time.sleep(2.0)
check("T3c 超时路径也回收了 sleeper（无孤儿）", orphans(M3) == [], str(orphans(M3)))

# ============================== T4/T5 注错：参数缺失 ==============================
with Client() as c:
    r = c.call("prepare_environment", {"sut": {"health_check": {"port": 1}}})
    check("T4 缺 command = BLOCKED", r["status"] == "BLOCKED" and "sut.command required" in r["summary"],
          r["summary"][:100])
    r = c.call("prepare_environment", {"sut": {"command": "echo hi"}})
    check("T5 缺 health_check = BLOCKED（不猜启动完成）",
          r["status"] == "BLOCKED" and "needs url or port" in r["summary"], r["summary"][:100])
    r = c.call("prepare_environment", {})
    check("T5b 什么都没有 = BLOCKED 并提示 sut 选项",
          r["status"] == "BLOCKED" and "sut={command,health_check}" in r["summary"], r["summary"][:120])

# ============================== T6 risk_floor：有事实就必须真跑对应 P0 ==============================
CANON = {"name": "floor", "amount_cents": 100, "quantity": 10, "influencer_id": 1, "created_by": 1}
FACTS = [{"topic": "idempotency:create", "statement": "idem_key 重复创建返回 200 duplicate", "source": "sut.py:create()"},
         {"topic": "relation:influencer", "statement": "失效/删除/不存在达人创建返回 404", "source": "sut.py:create()"}]
FLOOR = ["P0-CONC-create", "P0-IDEM-replay", "P0-REL-missing"]


def run_gate(case_ids):
    with Client() as c:
        c.call("add_facts", {"facts": FACTS})
        c.call("prepare_environment", {"example": "red-packet"})
        c.call("build_contract", {"contract_id": "FLOOR", "target": {"endpoint": "POST /red-packets"},
                                  "inputs": CANON, "ok_status": 201})
        c.call("plan_tests", {"method": "POST", "path": "/red-packets", "base_input": CANON, "ok_status": 201})
        ev = c.call("get_evidence")["summary"]
        ran = c.call("run_suite", {"case_ids": case_ids} if case_ids else {})
        gate = c.call("final_gate")
    pj = json.load(open(os.path.join(ev, "plan.json"), encoding="utf-8"))
    raw = json.load(open(os.path.join(ev, "results.json"), encoding="utf-8"))
    rows = raw["results"] if isinstance(raw, dict) else raw
    planned = {c["id"] for c in pj.get("cases", [])}
    return pj, planned, {r["id"] for r in rows}, ran, gate, ev


pj, planned, executed, ran, gate, ev = run_gate(["P0-HAPPY"])
check("T6a plan_risk_cases 已接线：事实 → P0 风险 case 真被规划", set(FLOOR) <= planned,
      f"planned={len(planned)} missing={sorted(set(FLOOR) - planned)}")
check("T6b risk_floor 落盘进 plan.json（可审计）", sorted(pj.get("risk_floor", [])) == FLOOR,
      str(pj.get("risk_floor")))
check("T6c 注错必红：只跑 happy path → 终判 NOT_TESTED 并点名缺失地板",
      gate["status"] == "NOT_TESTED" and all(x in gate["summary"] for x in FLOOR),
      f"{gate['status']}: {gate['summary'][:160]}")
check("T6c2 注错真的生效：地板 case 被规划了但确实没执行（红灯非空转）",
      set(FLOOR) <= planned and not (set(FLOOR) & executed),
      f"planned={len(planned)} executed={sorted(executed)}")
check("T6d gate.json 落盘进证据目录", os.path.exists(os.path.join(ev, "gate.json")), ev)

pj2, planned2, executed2, ran2, gate2, ev2 = run_gate(None)
check("T6e 对照绿：全量跑 → 地板 case 真被执行", set(FLOOR) <= executed2,
      f"executed={len(executed2)} missing={sorted(set(FLOOR) - executed2)}")
check("T6f 对照绿：地板满足后终判放行 PASS", gate2["status"] == "PASS",
      f"{gate2['status']}: {gate2['summary'][:160]}")
check("T6g 两次唯一变量是跑没跑地板 case，终判确实不同（gate 非空转）",
      planned == planned2 and gate["status"] != gate2["status"],
      f"planned identical={planned == planned2}; {gate['status']} vs {gate2['status']}")

bad = [n for n, ok, _ in results if not ok]
print(f"\n=== {len(results) - len(bad)}/{len(results)} passed" + (f"; FAILED: {bad}" if bad else ""))
sys.exit(1 if bad else 0)

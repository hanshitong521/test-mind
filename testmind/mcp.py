# testmind/mcp.py — MCP Server（stdio JSON-RPC），规范§37全部21工具 + add_facts/ask/answer/run_pipeline
# 通用版：宿主 AI 从任意项目提取事实 → add_facts 注入 → 参数化契约/环境/执行 → Gate。
import json, os, socket, subprocess, sys, time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from testmind import core

TOOLS = {
    "inspect_project":       {"desc": "扫描可测面(SQL/OpenAPI/构建文件)+runner可用性", "args": {"path": "项目目录(可选)"}},
    "analyze_change":        {"desc": "git diff 变更文件清单", "args": {"path": "git仓库(可选)"}},
    "collect_facts":         {"desc": "从 schema/契约文件抽 CONFIRMED 事实", "args": {"schema_sql": "SQL路径或内联DDL", "openapi": "OpenAPI JSON 路径/URL/内联"}},
    "add_facts":             {"desc": "注入宿主AI自提事实，source 必填（§44 自答部分）", "args": {"facts": "[{topic,statement,source,status?,derived_from?}]"}},
    "ask_user":              {"desc": "登记 USER_REQUIRED 问题（事实查尽才许用）", "args": {"question": "", "why": "", "impact": "", "options": "[]"}},
    "answer_question":       {"desc": "回答/自答问题 → CONFIRMED", "args": {"qid": "", "answer": ""}},
    "list_unknowns":         {"desc": "USER_REQUIRED 队列", "args": {}},
    "build_contract":        {"desc": "生成契约（冲突=BLOCKED）", "args": {"contract_id": "", "target": "{endpoint,table}", "inputs": "L0基准数据", "ok_status": 201}},
    "plan_tests":            {"desc": "从事实展开边界/负例/类型毒化 + 有事实才发的 P0 风险(幂等/关系/调度/并发/故障)", "args": {"method": "POST", "path": "/", "base_input": {}, "ok_status": 201, "bad_status": 400, "overrides": "字段→{值:状态}(须挂事实)"}},
    "static_precheck":       {"desc": "静态预检：DDL vs 写库语句确定性对拍(列错位/非空/CHECK越界)+口径冲突。产出前置情报，不产生判定——final_gate 仍须真实执行",
                              "args": {"schema_sql": "DDL 路径或内联", "statements": "[{sql,source}] 或 [sql]（宿主AI从改动代码提取）"}},
    "sql_perf_check":        {"desc": "SQL性能巡检(只读)：计时抓慢SQL+EXPLAIN；compare_sql 替代写法对拍——数据一致且实测更快才报 faster_equivalent。advisory 不进 final_gate",
                              "args": {"queries": "[{id,sql,source,slow_threshold_ms?,compare_sql?}]", "db": "可选,缺省用已prepare的连接",
                                       "runs": 1, "threshold_ms": 1000, "max_rows": 1000}},
    "generate_cases":        {"desc": "追加自定义场景 case（关系/状态机/幂等/并发/故障/时间）", "args": {"cases": "[case dict，schema 见 Engine docstring]"}},
    "prepare_environment":   {"desc": "连接被测服务+DB对拍+故障代理；example=red-packet 自启演示；sut={command,health_check} 通用自动拉起",
                              "args": {"base_url": "", "db": "{kind:sqlite|mysql|none}", "tables": "[]", "proxy_upstream": "", "example": "",
                                       "sut": "{command,cwd?,env?,base_url?,health_check:{url|port,expect_status?,timeout_s?}}"}},
    "seed_database":         {"desc": "精确 fixture SQL（L3，带出处）", "args": {"statements": "[sql]"}},
    "run_case":              {"desc": "真实执行 1 case + DB对拍 + 证据", "args": {"case": "{case}"}},
    "run_suite":             {"desc": "执行全部计划 case", "args": {"case_ids": "可选过滤"}},
    "run_concurrency_tests": {"desc": "并发专项", "args": {"path": "", "body": "{}", "count": 10, "success_count": "N", "max_success": "N", "db_count": "[]", "setup": "[]"}},
    "run_failure_injection": {"desc": "依赖故障专项（需 proxy）", "args": {"path": "", "mode": "ok|status|delay|refuse|bad_json", "code": 500, "http": 502, "db_count": "[]", "setup": "[]"}},
    "run_schema_tests":      {"desc": "Schemathesis 契约/负例测试", "args": {"openapi_url": "可选，默认 env 内"}},
    "run_scenario_tests":    {"desc": "Karate 业务场景 runner（feature 文件，JDK17 自动定位）", "args": {"feature": "path"}},
    "provision_environment": {"desc": "Testcontainers 语义起真依赖容器（create→use→destroy）", "args": {"image": "", "name": "", "host_port": 0, "env": "{}", "release": "容器名→删除"}},
    "run_regression":        {"desc": "重跑回归 Registry", "args": {}},
    "add_regression_case":   {"desc": "缺陷修复后永久回归（§27）", "args": {"case": "{}", "reason": "缺陷描述"}},
    "get_coverage":          {"desc": "case 维度覆盖矩阵", "args": {}},
    "get_failures":          {"desc": "FAIL 明细", "args": {}},
    "get_evidence":          {"desc": "证据目录", "args": {}},
    "final_gate":            {"desc": "PASS/HOLD/FAIL/BLOCKED 终判", "args": {}},
    "run_pipeline":          {"desc": "一步到位 collect→contract→plan→env→suite→schema→gate",
                              "args": {"schema_sql": "", "openapi": "", "contract": "{}", "plan": "{}", "env": "{}"}},
    "cleanup":               {"desc": "关闭环境/代理", "args": {}},
}

HELP = {"type": "object", "properties": {"args": {"type": "object"}}}


class Session:
    def __init__(self):
        self.facts = core.Facts()
        self.reset()

    def reset(self):
        self.contract = None
        self.plan = []
        self.engine = None
        self.db = None
        self.proxy = None
        self.srv = None
        self.sut = None
        self.sut_proc = None
        self.sut_log = None
        self.precheck = None
        self.sqlperf = None
        self.ev = None
        self.results = []
        self.openapi_url = None
        self.schema = None
        self.hooks = None


S = Session()


def envelope(status, summary, **kw):
    return {"status": status, "summary": summary,
            "evidence": kw.pop("evidence", []), "facts": kw.pop("facts", []),
            "unknowns": kw.pop("unknowns", []), "next_actions": kw.pop("next_actions", []), **kw}


def make_proxy(upstream, port=18198):
    from testmind.faultproxy import FaultProxy
    return FaultProxy(upstream, port=port)


def _expand_overrides(ov):
    out = {}
    for k, w in ov.items():
        if isinstance(w, dict):
            def fn(v, m=w):
                key = str(v) if str(v) in m else v
                return m.get(key, m.get("default", 400))
            out[k] = fn
        else:
            out[k] = w
    return out


def _kill_sut():
    """回收 _spawn_sut 起的进程树。Windows shell=True 下 Popen 持有的是 cmd，必须 /T 连子进程。"""
    proc = getattr(S, "sut_proc", None)
    if proc and proc.poll() is None:
        try:
            if os.name == "nt":
                subprocess.run(f"taskkill /F /T /PID {proc.pid}", capture_output=True, shell=True, timeout=15)
            else:
                proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            pass
    log = getattr(S, "sut_log", None)
    if log:
        try:
            log.close()
        except Exception:
            pass
    S.sut_proc = S.sut_log = None


def _spawn_sut(sut, ev):
    """通用自动拉起被测服务：command 起进程 + health_check 轮询到活才放行。
    起不来 = BLOCKED（绝不带死服务前进）；日志落 sut.log 进证据目录。"""
    cmd = sut.get("command")
    if not cmd:
        return {"status": "BLOCKED", "reason": "sut.command required"}
    hc = sut.get("health_check") or {}
    if not hc.get("url") and not hc.get("port"):
        return {"status": "BLOCKED", "reason": "sut.health_check needs url or port (活探测，不猜启动完成)"}
    log = open(os.path.join(ev.dir, "sut.log"), "wb")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen(cmd, cwd=sut.get("cwd") or core.ROOT, shell=True,
                            env={**os.environ, **(sut.get("env") or {})},
                            stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    S.sut_proc, S.sut_log = proc, log
    deadline = time.time() + float(hc.get("timeout_s", 30))
    url, last = hc.get("url"), ""
    while time.time() < deadline:
        if proc.poll() is not None:
            _kill_sut()
            return {"status": "BLOCKED", "reason": f"sut exited early code={proc.returncode} (见 evidence/sut.log)"}
        try:
            if url:
                with urllib.request.urlopen(url, timeout=2) as r:
                    expect = hc.get("expect_status")
                    if expect is None or r.status == expect:
                        break
                last = "health check status mismatch"
            else:
                s = socket.create_connection(("127.0.0.1", int(hc["port"])), timeout=2)
                s.close()
                break
        except Exception as e:
            last = repr(e)
        time.sleep(0.5)
    else:
        _kill_sut()
        return {"status": "BLOCKED", "reason": f"sut health check timeout ({last})"}
    if sut.get("base_url"):
        base = sut["base_url"]
    elif url:
        base = url.split("://", 1)[0] + "://" + url.split("://", 1)[-1].split("/", 1)[0]
    else:
        base = f"http://127.0.0.1:{int(hc['port'])}"
    return {"status": "PASS", "base_url": base, "pid": proc.pid}


def dispatch(name, a):
    a = a or {}
    if name == "inspect_project":
        root = a.get("path") or core.ROOT
        found = {"sql": [], "openapi": [], "build": []}
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in ("node_modules", "reports", ".git", "__pycache__")]
            for fn in fns:
                low = fn.lower()
                if low.endswith(".sql"):
                    found["sql"].append(os.path.join(dp, fn))
                elif ("openapi" in low or "swagger" in low) and low.endswith((".json", ".yaml", ".yml")):
                    found["openapi"].append(os.path.join(dp, fn))
                elif fn in ("pom.xml", "package.json", "go.mod", "build.gradle", "requirements.txt", "pyproject.toml"):
                    found["build"].append(os.path.join(dp, fn))
        return envelope("PASS", f"scanned: {sum(len(v) for v in found.values())} candidate sources",
                        found=found, runners=core.scan_runners(), next_actions=["collect_facts", "add_facts"])
    if name == "analyze_change":
        import subprocess
        r = subprocess.run("git diff --name-only HEAD", capture_output=True, text=True, shell=True,
                           cwd=a.get("path") or core.ROOT)
        files = [x for x in (r.stdout or "").splitlines() if x.strip()]
        return envelope("PASS", f"{len(files)} changed files", changed=files[:100],
                        next_actions=["plan_tests", "run_regression"])
    if name == "collect_facts":
        if a.get("schema_sql"):
            src = a["schema_sql"]
            sql = open(src, encoding="utf-8").read() if os.path.exists(src) else src
            for x in core.FactResolver.from_schema_sql(sql, str(a["schema_sql"])[:60]).f:
                S.facts.f.append({**x, "id": f"F{len(S.facts.f)+1:03d}"})
        if a.get("openapi"):
            spec = a["openapi"]
            if os.path.exists(spec):
                spec = json.load(open(spec, encoding="utf-8"))
            elif str(spec).startswith("http"):
                import urllib.request
                spec = json.loads(urllib.request.urlopen(spec, timeout=20).read())
            elif isinstance(spec, str):
                spec = json.loads(spec)
            for x in core.FactResolver.from_openapi(spec, str(a["openapi"])[:60]).f:
                S.facts.f.append({**x, "id": f"F{len(S.facts.f)+1:03d}"})
        cf = S.facts.conflicts()
        return envelope("BLOCKED" if cf else "PASS", f"{len(S.facts.f)} facts, {len(cf)} conflicts",
                        conflicts=cf, unknowns=[q["id"] + ": " + q["question"] for q in S.facts.questions],
                        next_actions=["add_facts"] if cf else ["build_contract"])
    if name == "add_facts":
        ids = [S.facts.add_raw(item) for item in a.get("facts", [])]
        cf = S.facts.conflicts()
        return envelope("BLOCKED" if cf else "PASS", f"added {len(ids)}; conflicts={len(cf)}",
                        facts=ids, conflicts=cf, next_actions=["build_contract"] if not cf else ["add_facts"])
    if name == "ask_user":
        S.facts.ask(a["question"], a.get("why", ""), a.get("impact", ""), a.get("options", []))
        return envelope("PASS", f"queued {S.facts.questions[-1]['id']}",
                        unknowns=[q["id"] + ": " + q["question"] for q in S.facts.questions])
    if name == "answer_question":
        ok = S.facts.answer(a["qid"], a["answer"])
        return envelope("PASS" if ok else "TOOL_ERROR", f"answer registered: {ok}")
    if name == "list_unknowns":
        pend = [q for q in S.facts.questions if q.get("state", "USER_REQUIRED") == "USER_REQUIRED"]
        return envelope("PASS", f"{len(pend)} open unknowns",
                        unknowns=[q["id"] + ": " + q["question"] for q in pend],
                        next_actions=["answer_question"] if pend else [])
    if name == "build_contract":
        if not S.facts.f:
            return envelope("BLOCKED", "no facts — collect_facts/add_facts first", next_actions=["collect_facts"])
        try:
            S.contract = core.build_contract(a.get("contract_id", "CONTRACT_" + core.now()),
                                             a.get("target", {}), S.facts, a.get("inputs", {}),
                                             a.get("expected", {"http": a.get("ok_status", 201)}))
        except AssertionError as e:
            return envelope("BLOCKED", str(e))
        S.ev = S.ev or core.Evidence()
        S.ev.write("contract.json", S.contract)
        return envelope("PASS", "contract built", contract_id=S.contract["contract_id"],
                        next_actions=["plan_tests", "prepare_environment"])
    if name == "plan_tests":
        base = a.get("base_input") or (S.contract or {}).get("inputs", {})
        if not base:
            return envelope("BLOCKED", "base_input required (L0 canonical)", next_actions=["build_contract"])
        S.ev = S.ev or core.Evidence()
        S.plan = core.plan_cases(S.facts, base, method=a.get("method", "POST"), path=a.get("path", "/"),
                                 overrides=_expand_overrides(a.get("overrides") or {}),
                                 ok_status=a.get("ok_status", 201), bad_status=a.get("bad_status", 400))
        S.plan += core.plan_risk_cases(S.facts, base, method=a.get("method", "POST"), path=a.get("path", "/"),
                                       ok_status=a.get("ok_status", 201), bad_status=a.get("bad_status", 400),
                                       run_id=S.ev.run_id, hooks=S.hooks)
        S.ev.write("plan.json", {"cases": S.plan, "risk_floor": core.risk_floor(S.facts, S.hooks)})
        return envelope("PASS", f"{len(S.plan)} cases", next_actions=["generate_cases", "prepare_environment"])
    if name == "static_precheck":
        if not a.get("schema_sql"):
            return envelope("BLOCKED", "schema_sql required (DDL 路径或内联)", next_actions=["collect_facts"])
        src = a["schema_sql"]
        schema = open(src, encoding="utf-8").read() if os.path.exists(src) else src
        rep = core.static_precheck(schema, a.get("statements", []))
        rep["fact_conflicts"] = S.facts.conflicts()
        S.ev = S.ev or core.Evidence()
        S.ev.write("precheck.json", rep)
        S.precheck = rep
        errors = [f for f in rep["findings"] if f["severity"] == "error"]
        warns = [f for f in rep["findings"] if f["severity"] == "warn"]
        return envelope("FAIL" if errors else "PASS",
                        f"static precheck: {len(errors)} errors, {len(warns)} warnings, "
                        f"{len(rep['fact_conflicts'])} fact conflicts（静态发现≠判定；不部署不执行就没有运行时结论，final_gate 仍须真实执行）",
                        findings=rep["findings"][:50], fact_conflicts=rep["fact_conflicts"],
                        evidence=[S.ev.dir], next_actions=["plan_tests", "prepare_environment"])
    if name == "sql_perf_check":
        dbc, own = S.db, None
        if dbc is None:
            conn = core.open_db(a.get("db"))
            if conn is None:
                return envelope("BLOCKED", "no db — prepare_environment 带 db 连接，或本工具传 db 参数",
                                next_actions=["prepare_environment"])
            dbc, own = core.DBCheck(conn, tables=()), conn
        try:
            rep = core.sql_perf_check(dbc, a.get("queries", []), threshold_ms=a.get("threshold_ms", 1000),
                                      runs=a.get("runs", 1), max_rows=a.get("max_rows", 1000))
        finally:
            if own:
                own.close()
        S.ev = S.ev or core.Evidence()
        S.ev.write("sql-perf.json", rep)
        S.sqlperf = rep
        errors = [f for f in rep["findings"] if f["severity"] == "error"]
        advisories = [f for f in rep["findings"] if f["severity"] == "warn"]
        return envelope("FAIL" if errors else "PASS",
                        f"sql perf: {len(rep['queries'])} queries, {len(advisories)} advisories (slow/faster_equivalent), "
                        f"{len(errors)} errors（巡检≠判定，不进 final_gate）",
                        findings=rep["findings"][:50], queries=rep["queries"],
                        evidence=[S.ev.dir], next_actions=[])
    if name == "generate_cases":
        S.plan += a.get("cases", [])
        return envelope("PASS", f"plan={len(S.plan)}", next_actions=["prepare_environment", "run_suite"])
    if name == "prepare_environment":
        if S.engine:
            return envelope("PASS", "environment already prepared")
        if a.get("example") == "red-packet":
            sys.path.insert(0, os.path.join(core.ROOT, "examples", "red-packet"))
            from sut import serve as ex_serve, seed_packet_sql
            dbp = os.path.join(core.ROOT, "examples", "red-packet", "mcp-sut.db")
            if os.path.exists(dbp):
                os.remove(dbp)
            S.srv, S.sut = ex_serve(db_path=dbp, port=18102)
            S.hooks = core.red_packet_hooks(seed_packet_sql)
            a.setdefault("base_url", "http://127.0.0.1:18102")
            a.setdefault("db", {"kind": "sqlite", "path": dbp})
            a.setdefault("tables", ["red_packet", "grant_log"])
            a.setdefault("proxy_upstream", a["base_url"])
        if a.get("sut"):
            S.ev = S.ev or core.Evidence()
            r = _spawn_sut(a["sut"], S.ev)
            if r.get("status") != "PASS":
                return envelope(r["status"], r.get("reason", "sut failed to start"),
                                evidence=[S.ev.dir], next_actions=[])
            a.setdefault("base_url", r["base_url"])
        if not a.get("base_url"):
            return envelope("BLOCKED", "base_url required (start your service, local/test only §53；或传 sut={command,health_check} 让 TestMind 替你起)")
        conn = core.open_db(a.get("db"))
        S.db = core.DBCheck(conn, tables=a.get("tables", ())) if conn else None
        if a.get("proxy_upstream"):
            S.proxy = make_proxy(a["proxy_upstream"])
        S.engine = core.Engine(a["base_url"], S.ev or core.Evidence(), db=S.db, proxy=S.proxy)
        S.ev = S.engine.ev
        S.openapi_url = a.get("openapi_url") or (a["base_url"] + "/openapi.json")
        if S.sut is not None and S.proxy is not None:
            S.sut.dependency_url = S.proxy.url() + "/risk"
        return envelope("PASS", f"engine ready base={a['base_url']} db={'on' if S.db else 'API-only'}",
                        next_actions=["run_suite"])
    if name == "seed_database":
        if not S.db:
            return envelope("BLOCKED", "no db connection")
        for sql in a.get("statements", []):
            S.db.exec(sql)
        return envelope("PASS", f"{len(a.get('statements', []))} fixtures applied")
    if name in ("run_case", "run_suite", "run_concurrency_tests", "run_failure_injection", "run_regression"):
        if not S.engine:
            return envelope("BLOCKED", "environment not prepared", next_actions=["prepare_environment"])
        if name == "run_regression":
            S.results += core.run_regression(S.engine)
        else:
            if name == "run_case":
                cases = [a["case"]]
            elif name == "run_suite":
                cases = [c for c in S.plan if c["id"] in set(a["case_ids"])] if a.get("case_ids") else S.plan
            elif name == "run_concurrency_tests":
                cases = [{"id": a.get("id", "CONC-x"), "category": "CONCURRENCY", "priority": "P0",
                          "source": a.get("source", "user-defined"), "setup": a.get("setup", []),
                          "action": {"kind": "concurrent", "path": a["path"], "body": a.get("body"),
                                     "count": a.get("count", 10), "method": a.get("method", "POST")},
                          "expected": {k: a[k] for k in ("success_count", "max_success", "db_count", "db_rows") if k in a}}]
            else:
                if not S.proxy:
                    return envelope("BLOCKED", "no fault proxy — prepare_environment with proxy_upstream",
                                    next_actions=["prepare_environment"])
                mode = a.get("mode", "status")
                if isinstance(mode, str) and mode == "status":
                    mode = {"kind": "status", "code": a.get("code", 500)}
                exp = {k: a[k] for k in ("http", "db_count", "db_rows") if k in a}
                cases = [{"id": a.get("id", f"DEP-{a.get('mode', 'status')}"), "category": "FAILURE_INJECTION",
                          "priority": "P0", "source": a.get("source", "user-defined"), "setup": a.get("setup", []),
                          "action": {"kind": "fault", "mode": mode, "path": a["path"], "body": a.get("body"),
                                     "method": a.get("method", "POST")},
                          "expected": exp or {"http": a.get("expect_http", 502)}}]
            S.results += [S.engine.run(c) for c in cases]
        S.ev.write("results.json", S.results)
        fails = [r["id"] for r in S.results if r["status"] == "FAIL"]
        return envelope("FAIL" if fails else "PASS", f"{len(S.results)} results, {len(fails)} fail",
                        failures=fails[:30], evidence=[S.ev.dir],
                        next_actions=["add_regression_case"] if fails else ["final_gate"])
    if name == "run_schema_tests":
        S.ev = S.ev or core.Evidence()
        pid = 1
        for row in S.results:
            if row.get("id") == "P0-HAPPY":
                pid = (row.get("detail") or {}).get("body", {}).get("id") or 1
                break
        r = core.run_schema_tests(a.get("openapi_url") or S.openapi_url, S.ev,
                                  path_params={"id": pid, "action": "start"})
        S.schema = r                       # 记录进 session：final_gate 会查
        return envelope(r["status"], f"schemathesis: {r.get('reason', r.get('exit_code'))}",
                        evidence=[r.get("evidence", "")])
    if name == "run_scenario_tests":
        S.ev = S.ev or core.Evidence()
        r = core.run_karate(a["feature"], S.ev)
        if r["status"] != "SKIPPED_WITH_REASON":
            S.results.append({"id": "KARATE-" + os.path.basename(a["feature"]), "priority": "P0",
                              "status": r["status"], "evidence": r.get("evidence", "")})
        return envelope(r["status"], f"karate: {r.get('reason', r.get('exit_code'))}",
                        evidence=[r.get("evidence", "")])
    if name == "provision_environment":
        if a.get("release"):
            core.docker_release(a["release"])
            return envelope("PASS", f"container {a['release']} destroyed")
        r = core.docker_provision(a["image"], a["name"], int(a["host_port"]),
                                  a.get("container_port"), a.get("env"))
        return envelope(r["status"], json.dumps(r, ensure_ascii=False),
                        next_actions=["prepare_environment"] if r["status"] == "AVAILABLE" else [])
    if name == "add_regression_case":
        return envelope("PASS", f"registry size {core.add_regression_case(a['case'], a.get('reason',''))}")
    if name == "get_coverage":
        dims = {}
        for c in S.plan:
            dims[c.get("category", "?")] = dims.get(c.get("category", "?"), 0) + 1
        return envelope("PASS", "case-dimension matrix", dimensions=dims,
                        jacoco=core.scan_runners()["jacoco"])
    if name == "get_failures":
        fails = [r for r in S.results if r["status"] == "FAIL"]
        return envelope("PASS" if not fails else "FAIL", f"{len(fails)} failures",
                        failures=[{"id": f["id"], "detail": f.get("detail")} for f in fails][:50])
    if name == "get_evidence":
        return envelope("PASS", S.ev.dir if S.ev else "no run yet", evidence=[S.ev.dir] if S.ev else [])
    if name == "final_gate":
        if not S.results:
            return envelope("NOT_TESTED", "nothing executed — No Execution → No PASS")
        st, why = core.Gate.evaluate(S.results, S.facts.questions, S.facts.conflicts(),
                                     floor=core.risk_floor(S.facts, S.hooks))
        if st == "PASS" and S.schema and S.schema["status"] == "FAIL":
            st, why = "FAIL", "schemathesis findings unexplained（见 schema-tests-summary.txt）"   # §25 失败必须回分析
        if S.ev:
            S.ev.write("gate.json", {"status": st, "why": why})
        return envelope(st, why, evidence=[S.ev.dir] if S.ev else [])
    if name == "run_pipeline":
        steps = [("prepare_environment", a.get("env", {})),
                 ("collect_facts", {k: a[k] for k in ("schema_sql", "openapi") if k in a}),
                 ("build_contract", a.get("contract", {})),
                 ("plan_tests", a.get("plan", {})),
                 ("run_suite", {}),
                 ("run_regression", {}),
                 ("run_schema_tests", {}),
                 ("final_gate", {})]
        out = []
        for tname, targs in steps:
            r = dispatch(tname, targs)
            out.append((tname, r["status"], r["summary"]))
            if r["status"] in ("BLOCKED", "TOOL_ERROR"):
                break
        final = out[-1]
        return envelope(final[1], " | ".join(f"{t}:{s}" for t, s, _ in out), steps=out,
                        evidence=[S.ev.dir] if S.ev else [],
                        next_actions=["add_regression_case"] if final[1] == "FAIL" else ["cleanup"])
    if name == "cleanup":
        _kill_sut()
        if S.srv:
            S.srv.shutdown()
        if S.proxy:
            S.proxy.shutdown()
        S.reset()
        return envelope("PASS", "closed")
    return envelope("TOOL_ERROR", f"unknown tool {name}")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")     # MCP 协议 = UTF-8，Windows GBK 控制台必须切
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                         "error": {"code": -32700, "message": "parse error"}}) + "\n")
            sys.stdout.flush()
            continue
        m = req.get("method", "")
        if m == "initialize":
            resp = {"jsonrpc": "2.0", "id": req.get("id"), "result": {
                "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "testmind", "version": "1.1.0"}}}
        elif m == "tools/list":
            resp = {"jsonrpc": "2.0", "id": req.get("id"), "result": {"tools": [
                {"name": n, "description": d["desc"], "inputSchema": HELP} for n, d in TOOLS.items()]}}
        elif m == "tools/call":
            p = req.get("params", {})
            try:
                text = json.dumps(dispatch(p.get("name", ""), p.get("arguments", {})),
                                  ensure_ascii=False, default=str)
            except Exception as e:
                text = json.dumps(envelope("TOOL_ERROR", repr(e)))
            resp = {"jsonrpc": "2.0", "id": req.get("id"), "result": {"content": [{"type": "text", "text": text}]}}
        elif m == "ping":
            resp = {"jsonrpc": "2.0", "id": req.get("id"), "result": {}}
        else:
            resp = {"jsonrpc": "2.0", "id": req.get("id"),
                    "error": {"code": -32601, "message": f"unknown method {m}"}}
        sys.stdout.write(json.dumps(resp, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

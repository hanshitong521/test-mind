# testmind/mcp.py — MCP Server（stdio JSON-RPC）。V10 §29 工具面收敛：对外仅 7 门面。
# 旧 30 个工具的内部能力全部下沉为 _do_* 私有函数，由门面按 §30 新管道编排；
# tools/list 只返回 FACADES，dispatch 只接受门面名，未知即 TOOL_ERROR。
import json, os, re, socket, subprocess, sys, time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from testmind import core
from testmind import coverage, env_policy, env_registry, export, git_change, impact, java_scan, score, session_store, tool_schemas, triage
from testmind.secrets import redact_obj

# V10 §29：对外唯一工具面（7 个门面）。旧 30 工具的内部能力下沉为 _do_* 私有函数。
FACADES = {
    "analyze_impact":        {"desc": "[门面] 影响面分析：git 变更→调用图→受影响端点/表/任务+Regression Radius（含项目可测面扫描与 Java 调用图）",
                              "args": {"path": "项目根", "base_sha": "", "head_sha": "", "changed": "{file:[[start,end]]}(可选，覆盖 git)",
                                       "java_graph": "true 时先建 Java 调用图", "inspect": "true 时附带可测面扫描",
                                       "patterns": "[[id,regex,说明]] 可选，缺省用内置高风险调用点清单（§29 增强）", "max_files": 800}},
    "plan_verification":     {"desc": "[门面] 规划验证：TaskBundle/事实→契约→计划 case + RuleMind/ScenarioMind 场景推导（§5-10，证据驱动；含事实澄清 ask/answer）",
                              "args": {"task_bundle": "{}", "consumer_root": "", "schema_sql": "", "openapi": "",
                                       "facts": "[]", "contract": "{}", "plan": "{}", "cases": "[]",
                                       "ask": "{question,why,impact,options}", "answer": "{qid,answer}",
                                       "rules": "[{rule_id,name,resource,predicate,applies_to,surfaces}]",
                                       "scenario": "{roles,states,times,entry_points,operations,history_cases,actor_evidence:{label:SELECT_SQL}}",
                                       "oracle": "{endpoints,spec,actors,rules}——§11/§12 跨接口一致性+变形测试",
                                       "handoff_id": "DH-xxx 从 DevTest Hub 拉 contract.sections 转 facts（只读 contract；hints 禁入 facts/expected）"}},
    "prepare_verification":  {"desc": "[门面] 备环境：连接/起服务/DB/代理/KV(Redis) + 可选容器 provision/seed 台账/DataTruth data_plan",
                              "args": {"env": "{}（含 kv:{kind:memory|redis,args:{host,port,password,db}}）", "seed": "[sql]", "provision": "{}", "data_plan": "{requirements,ownership,template}"}},
    "run_verification":      {"desc": "[门面] 执行验证：跑计划/回归/并发/故障注入/契约/场景 runner + 可选 precheck/perf（advisory 输出）",
                              "args": {"case_ids": "[]", "case": "{}", "regression": "bool", "precheck": "{}", "perf": "{}",
                                       "concurrency": "{}", "failure": "{}", "schema": "{openapi_url,required}",
                                       "scenario": "{feature}", "add_regression": "{case,reason}"}},
    "replay_failure":        {"desc": "[门面] 复现失败：按 case_id 从计划重跑单 case + 证据",
                              "args": {"case_id": "", "path": ""}},
    "final_gate":            {"desc": "[门面] 终判：Gate + Quality Score（§28 十一维，去假绿）+ 接口覆盖台账/写入副作用证据/失败归因 + 自动导出四件套",
                              "args": {"require_schema": "bool", "require_db_evidence": "bool——写入类 case 无 DB/KV 前后态断言时 PASS 降 HOLD"}},
    "cleanup":               {"desc": "[门面] 收尾：Ledger 回滚(PASS)/现场冻结(FAIL) + 进程回收 + reset_task",
                              "args": {"force_rollback": "bool"}},
}

def normalize_args(a):
    if not a:
        return {}
    if isinstance(a, dict) and set(a.keys()) == {"args"} and isinstance(a.get("args"), dict):
        return a["args"]
    return a


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
        self.task_id = None
        self.consumer_root = None
        self.require_schema = False
        # V10 §18：任务态字段（reset_task 必须全清）
        self.env_class = None
        self.ledger = None
        self.data_plan = None
        self.actors = None
        self.rules = None
        self.impact_graph = None
        self.impact = None
        self.mutation = None
        self.scenarios = None
        # §29 增强：KV/覆盖台账/归因/事实源缓存
        self.kv = None
        self.openapi_spec = None
        self.triage = None
        self.executed_cases = []   # 与 S.results 对齐的已执行 case（覆盖台账反查 action 用）


S = Session()


def envelope(status, summary, **kw):
    body = {"schema_version": tool_schemas.schema_version(),
            "task_id": S.task_id,
            "status": status, "summary": summary,
            "evidence": kw.pop("evidence", []), "facts": kw.pop("facts", []),
            "unknowns": kw.pop("unknowns", []), "next_actions": kw.pop("next_actions", []), **kw}
    return redact_obj(body)


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
                import signal
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except Exception:
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
    popen_kw = {"cwd": sut.get("cwd") or core.ROOT, "shell": True,
                "env": {**os.environ, **(sut.get("env") or {})},
                "stdout": log, "stderr": subprocess.STDOUT}
    if os.name == "nt":
        popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kw["start_new_session"] = True
    proc = subprocess.Popen(cmd, **popen_kw)
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


# ───────────────────────── 内部能力（旧工具下沉为私有函数，仅供门面编排）─────────────────────────

def _do_intake_task(a):
    bundle = a.get("task_bundle") or {}
    tid = bundle.get("task_id")
    if not tid:
        return envelope("BLOCKED", "task_bundle.task_id required")
    # V10 §18：切换任务先 reset_task，杜绝上一 task 的 facts/contract/plan/ledger 污染
    if S.task_id and S.task_id != tid:
        from testmind import isolation
        isolation.reset_task(S)
    root = a.get("consumer_root") or core.ROOT
    session_store.save_snapshot(root, tid, bundle)
    S.task_id, S.consumer_root = tid, root
    verify = (bundle.get("spec") or {}).get("verify") or {}
    if verify.get("change_manifest"):
        S.ev = S.ev or core.Evidence()
        S.ev.write("change-manifest.json", verify["change_manifest"])
    return envelope("PASS", f"task {tid} intake", task_id=tid,
                    next_actions=["plan_verification"])


def _do_export_handoff(a):
    tid = a.get("task_id") or S.task_id
    if not tid or not S.ev:
        return envelope("BLOCKED", "no task_id or evidence run")
    root = S.consumer_root or core.ROOT
    task_dir = session_store.task_root(root, tid)
    rep = {"run_id": S.ev.run_id, "final": "UNKNOWN", "why": "",
           "facts_confirmed": len([x for x in S.facts.f if x.get("status") == "CONFIRMED"]),
           "conflicts": S.facts.conflicts(), "risk_floor": core.risk_floor(S.facts, S.hooks)}
    manifest = export.build_evidence_manifest(S.ev.dir)
    rep["evidence_manifest_hash"] = manifest.get("manifest_hash", "")
    paths = export.write_four_piece(task_dir, rep, S.results, S.plan, S.facts.snapshot(), S.ev.dir)
    return envelope("PASS", "artifacts exported", artifacts=list(paths.values()), task_id=tid)


def _handoff_endpoint():
    """DevTest Hub 地址/项目：env 优先，缺省与本地看板一致。"""
    return (os.environ.get("VERIFY_HANDOFF_BASE_URL", "http://127.0.0.1:18787").strip(),
            os.environ.get("VERIFY_HANDOFF_PROJECT_ID", "shejiuPro").strip() or "shejiuPro")


def _load_handoff_contract(handoff_id):
    """spec §VerifyMind 改动：plan_verification{handoff_id} → GET /api/handoffs/{id}
    → 只把 contract.sections 转成 facts。hints 由 handoff_client 三道机制隔离，
    拿不到就不用，绝不进 facts / expected / 断言。"""
    from scripts.handoff_client import HandoffClient, contract_facts

    base_url, project_id = _handoff_endpoint()
    handoff = HandoffClient(base_url, project_id=project_id).fetch_handoff(handoff_id)
    return contract_facts(handoff, handoff_id=handoff_id)


def _submit_handoff_run(gate_final, endpoint_coverage):
    if os.environ.get("VERIFY_HANDOFF_CALLBACK") != "1":
        return None
    handoff_id = os.environ.get("VERIFY_HANDOFF_ID", "").strip()
    if not handoff_id:
        return {"status": "SKIPPED", "reason": "VERIFY_HANDOFF_ID is not set"}
    from scripts.handoff_client import HandoffClient, build_run_summary

    base_url = os.environ.get("VERIFY_HANDOFF_BASE_URL", "http://127.0.0.1:18787")
    project_id = os.environ.get("VERIFY_HANDOFF_PROJECT_ID", "shejiuPro")
    run = build_run_summary(
        S.ev.run_id if S.ev else "",
        gate_final,
        S.ev.dir if S.ev else "",
        S.results,
        S.plan,
        endpoint_coverage=endpoint_coverage,
        root=core.ROOT,
    )
    return HandoffClient(base_url, project_id=project_id).submit_run(handoff_id, run)


def _do_scan_java(a):
    root = a.get("path") or core.ROOT
    res = java_scan.scan_java_tree(root, max_files=int(a.get("max_files", 500)))
    for x in res.f:
        S.facts.f.append({**x, "id": f"F{len(S.facts.f)+1:03d}"})
    S.facts.unparsed += res.unparsed
    return envelope("PASS", f"java scan: {len(res.f)} facts, {len(res.unparsed)} unparsed",
                    unparsed=res.unparsed, next_actions=["plan_verification"])


def _do_inspect_project(a):
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
                    found=found, runners=core.scan_runners(), next_actions=["plan_verification"])


def _do_analyze_change(a):
    info = git_change.analyze_git_change(
        a.get("path") or core.ROOT,
        base_sha=a.get("base_sha"),
        head_sha=a.get("head_sha"),
        include_untracked=a.get("include_untracked", True),
    )
    files = info["changed"] + info["untracked"]
    return envelope("PASS", f"{len(info['changed'])} changed, {len(info['untracked'])} untracked",
                    changed=files[:100], git=info,
                    next_actions=["plan_verification", "run_verification"])


def _do_collect_facts(a):
    op = a.get("operation_id")
    scoped = core.facts_for_operation(S.facts, operation_id=op, path=a.get("path"), method=a.get("method"))
    if a.get("schema_sql"):
        src = a["schema_sql"]
        if os.path.exists(src):
            with open(src, encoding="utf-8") as fh:
                sql = fh.read()
        else:
            sql = src
        res = core.FactResolver.from_schema_sql(sql, str(a["schema_sql"])[:60])
        for x in res.f:
            S.facts.f.append({**x, "id": f"F{len(S.facts.f)+1:03d}"})
        S.facts.unparsed += getattr(res, "unparsed", [])
    if a.get("openapi"):
        spec = a["openapi"]
        if os.path.exists(spec):
            with open(spec, encoding="utf-8") as fh:
                spec = json.load(fh)
        elif str(spec).startswith("http"):
            import urllib.request
            with urllib.request.urlopen(spec, timeout=20) as resp:
                spec = json.loads(resp.read())
        elif isinstance(spec, str):
            spec = json.loads(spec)
        S.openapi_spec = spec   # §29 增强：缓存声明端点全集，final_gate 覆盖台账用
        res = core.FactResolver.from_openapi(spec, str(a["openapi"])[:60])
        for x in res.f:
            S.facts.f.append({**x, "id": f"F{len(S.facts.f)+1:03d}"})
        S.facts.unparsed += getattr(res, "unparsed", [])
    cf = scoped.conflicts() if op or a.get("path") else S.facts.conflicts()
    unparsed = list(S.facts.unparsed)
    return envelope("BLOCKED" if cf else "PASS",
                    f"{len(S.facts.f)} facts, {len(cf)} conflicts, {len(unparsed)} unparsed",
                    conflicts=cf, unparsed=unparsed,
                    unknowns=[q["id"] + ": " + q["question"] for q in S.facts.questions],
                    next_actions=["plan_verification"])


def _do_add_facts(a):
    ids = [S.facts.add_raw(item) for item in a.get("facts", [])]
    cf = S.facts.conflicts()
    return envelope("BLOCKED" if cf else "PASS", f"added {len(ids)}; conflicts={len(cf)}",
                    facts=ids, conflicts=cf, next_actions=["plan_verification"])


def _do_ask_user(a):
    S.facts.ask(a["question"], a.get("why", ""), a.get("impact", ""), a.get("options", []))
    return envelope("PASS", f"queued {S.facts.questions[-1]['id']}",
                    unknowns=[q["id"] + ": " + q["question"] for q in S.facts.questions])


def _do_answer_question(a):
    ok = S.facts.answer(a["qid"], a["answer"])
    return envelope("PASS" if ok else "TOOL_ERROR", f"answer registered: {ok}")


def _do_list_unknowns(a):
    pend = [q for q in S.facts.questions if q.get("state", "USER_REQUIRED") == "USER_REQUIRED"]
    return envelope("PASS", f"{len(pend)} open unknowns",
                    unknowns=[q["id"] + ": " + q["question"] for q in pend],
                    next_actions=["plan_verification"] if pend else [])


def _do_build_contract(a):
    if not S.facts.f:
        return envelope("BLOCKED", "no facts — collect_facts/add_facts first", next_actions=["plan_verification"])
    try:
        S.contract = core.build_contract(a.get("contract_id", "CONTRACT_" + core.now()),
                                         a.get("target", {}), S.facts, a.get("inputs", {}),
                                         a.get("expected", {"http": a.get("ok_status", 201)}))
    except AssertionError as e:
        return envelope("BLOCKED", str(e))
    S.ev = S.ev or core.Evidence()
    S.ev.write("contract.json", S.contract)
    return envelope("PASS", "contract built", contract_id=S.contract["contract_id"],
                    next_actions=["plan_verification", "prepare_verification"])


def _do_plan_tests(a):
    facts = core.facts_for_operation(S.facts, operation_id=a.get("operation_id"), path=a.get("path"))
    base = a.get("base_input") or (S.contract or {}).get("inputs", {})
    if not base:
        return envelope("BLOCKED", "base_input required (L0 canonical)", next_actions=["plan_verification"])
    S.ev = S.ev or core.Evidence()
    S.plan = core.plan_cases(facts, base, method=a.get("method", "POST"), path=a.get("path", "/"),
                             overrides=_expand_overrides(a.get("overrides") or {}),
                             ok_status=a.get("ok_status", 201), bad_status=a.get("bad_status", 400))
    S.plan += core.plan_risk_cases(facts, base, method=a.get("method", "POST"), path=a.get("path", "/"),
                                   ok_status=a.get("ok_status", 201), bad_status=a.get("bad_status", 400),
                                   run_id=S.ev.run_id, hooks=S.hooks)
    S.ev.write("plan.json", {"cases": S.plan, "risk_floor": core.risk_floor(facts, S.hooks)})
    return envelope("PASS", f"{len(S.plan)} cases", next_actions=["prepare_verification", "run_verification"])


def _do_static_precheck(a):
    if not a.get("schema_sql"):
        return envelope("BLOCKED", "schema_sql required (DDL 路径或内联)", next_actions=["plan_verification"])
    src = a["schema_sql"]
    if os.path.exists(src):
        with open(src, encoding="utf-8") as fh:
            schema = fh.read()
    else:
        schema = src
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
                    evidence=[S.ev.dir], next_actions=["plan_verification", "prepare_verification"])


def _do_sql_perf_check(a):
    dbc, own = S.db, None
    if dbc is None:
        conn = core.open_db(a.get("db"))
        if conn is None:
            return envelope("BLOCKED", "no db — prepare_verification 带 db 连接，或本调用传 db 参数",
                            next_actions=["prepare_verification"])
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


def _do_generate_cases(a):
    S.plan += a.get("cases", [])
    return envelope("PASS", f"plan={len(S.plan)}", next_actions=["prepare_verification", "run_verification"])


def _do_prepare_environment(a):
    if S.engine:
        return envelope("PASS", "environment already prepared")
    if a.get("environment_ref"):
        prof = env_registry.resolve_ref(a["environment_ref"], S.consumer_root or core.ROOT)
        if prof:
            prof = env_registry.materialize_secrets(prof,
                os.path.join(S.consumer_root or core.ROOT, "env", "secrets"))
            for k in ("env_class", "base_url", "db", "tables", "proxy_upstream"):
                if k in prof and k not in a:
                    a[k] = prof[k]
            a.setdefault("env_class", prof.get("env_class"))
    if not a.get("env_class") and (a.get("example") or a.get("sut")):
        a.setdefault("env_class", "local")
    ok, reason = env_policy.check_prepare(
        a.get("env_class"), a.get("base_url"),
        destructive=bool(a.get("destructive")), fault=False, concurrency=False)
    if not ok and a.get("example") != "red-packet":
        return envelope("BLOCKED", reason, next_actions=["set env_class to local|test|staging"])
    if a.get("example") == "red-packet":
        a.setdefault("env_class", "test")
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
    S.env_class = env_policy.classify_env(a.get("env_class"), a.get("base_url"))
    if a.get("kv"):
        from testmind import kv as _kv
        try:
            S.kv = _kv.open_kv(a["kv"])
        except Exception as e:
            return envelope("BLOCKED", f"kv connection failed: {e!r}", next_actions=["prepare_verification"])
    if S.db is not None:
        from testmind.ledger import SeedLedger
        run_id = (S.ev.run_id if S.ev else core.now())
        S.ledger = SeedLedger(run_id, root=S.consumer_root or core.ROOT)
    S.engine = core.Engine(a["base_url"], S.ev or core.Evidence(), db=S.db, proxy=S.proxy,
                           ledger=S.ledger, kv=S.kv)
    S.ev = S.engine.ev
    S.openapi_url = a.get("openapi_url") or (a["base_url"] + "/openapi.json")
    if S.sut is not None and S.proxy is not None:
        S.sut.dependency_url = S.proxy.url() + "/risk"
    return envelope("PASS", f"engine ready base={a['base_url']} db={'on' if S.db else 'API-only'}",
                    next_actions=["run_verification"])


def _do_seed_database(a):
    if not S.db:
        return envelope("BLOCKED", "no db connection")
    # V10 §13.4/§34：写库收紧 env_class ∈ {local,test}（staging 不再允许 destructive），
    # red-packet example 豁免保留；所有写操作登记 Seed Ledger（§16）。
    cls = S.env_class or env_policy.classify_env(a.get("env_class"))
    if cls not in ("local", "test") and a.get("example") != "red-packet":
        return envelope("BLOCKED", f"seed_database blocked for env_class={cls} (local|test only, §13.4)")
    if S.ledger is None:
        from testmind.ledger import SeedLedger
        S.ledger = SeedLedger(core.now(), root=S.consumer_root or core.ROOT)
    n = 0
    for sql in a.get("statements", []):
        S.ledger.record_exec(S.db, sql, case_id=a.get("case_id", "seed"),
                             dataset_id=a.get("dataset_id", ""), reason=a.get("reason", "prepare_verification.seed"))
        n += 1
    return envelope("PASS", f"{n} fixtures applied (ledgered)", ledger=[S.ledger.path])


def _triage_failures(base):
    """§29 失败归因：对 base 之后新产生的非 PASS 结果跑三分法。
    TEST_DEFECT 且缺参在 plan 内有出处 → 自动补参重跑一次（替换原结果）；
    无出处 → 标记待修测试，不许直接报 BUG。结果累积进 S.triage。"""
    S.triage = S.triage or []
    for i in range(base, len(S.results)):
        r = S.results[i]
        if r.get("status") == "PASS":
            continue
        case = S.executed_cases[i]
        info = triage.classify(r, case, S.facts)
        entry = {"id": r["id"], **info}
        if info.get("attribution") == "TEST_DEFECT" and info.get("auto_fixable"):
            nc, fixed = triage.autofix(case, info, S.plan + S.executed_cases, S.facts)
            if nc:
                new_r = S.engine.run(nc)
                entry["fixed"] = True
                entry["autofix"] = fixed
                entry["rerun_status"] = new_r.get("status")
                S.executed_cases[i] = nc
                S.results[i] = new_r
                if new_r.get("status") != "PASS":
                    entry["post_fix"] = triage.classify(new_r, nc, S.facts)
            else:
                entry["fix_hint"] += "；plan 内无该参数出处 → 走 plan_verification 的 ask 补事实后重跑"
        S.triage.append(entry)
    if S.triage and S.ev:
        S.ev.write("triage.json", S.triage)


def _do_run_exec(mode, a):
    """内部执行：mode ∈ {single, suite, concurrent, fault, regression}。"""
    if not S.engine:
        return envelope("BLOCKED", "environment not prepared", next_actions=["prepare_verification"])
    if mode == "regression":
        reg_cases = core.load_registry(consumer_root=a.get("consumer_root") or S.consumer_root)
        base = len(S.results)
        S.executed_cases += reg_cases
        S.results += [S.engine.run(c) for c in reg_cases]
        _triage_failures(base)
    else:
        if mode == "single":
            cases = [a["case"]]
        elif mode == "suite":
            cases = [c for c in S.plan if c["id"] in set(a["case_ids"])] if a.get("case_ids") else S.plan
        elif mode == "concurrent":
            cases = [{"id": a.get("id", "CONC-x"), "category": "CONCURRENCY", "priority": "P0",
                      "source": a.get("source", "user-defined"), "setup": a.get("setup", []),
                      "action": {"kind": "concurrent", "path": a["path"], "body": a.get("body"),
                                 "count": a.get("count", 10), "method": a.get("method", "POST")},
                      "expected": {k: a[k] for k in ("success_count", "max_success", "db_count", "db_rows") if k in a}}]
        else:
            if not S.proxy:
                return envelope("BLOCKED", "no fault proxy — prepare_verification 带 proxy_upstream",
                                next_actions=["prepare_verification"])
            fmode = a.get("mode", "status")
            if isinstance(fmode, str) and fmode == "status":
                fmode = {"kind": "status", "code": a.get("code", 500)}
            exp = {k: a[k] for k in ("http", "db_count", "db_rows") if k in a}
            cases = [{"id": a.get("id", f"DEP-{a.get('mode', 'status')}"), "category": "FAILURE_INJECTION",
                      "priority": "P0", "source": a.get("source", "user-defined"), "setup": a.get("setup", []),
                      "action": {"kind": "fault", "mode": fmode, "path": a["path"], "body": a.get("body"),
                                 "method": a.get("method", "POST")},
                      "expected": exp or {"http": a.get("expect_http", 502)}}]
        base = len(S.results)
        S.executed_cases += cases
        S.results += [S.engine.run(c) for c in cases]
        _triage_failures(base)
    S.ev.write("results.json", S.results)
    fails = [r["id"] for r in S.results if r["status"] == "FAIL"]
    test_defects = [t["id"] for t in (S.triage or []) if t["attribution"] == "TEST_DEFECT" and not t.get("fixed")]
    return envelope("FAIL" if fails else "PASS",
                    f"{len(S.results)} results, {len(fails)} fail"
                    + (f", {len(test_defects)} TEST_DEFECT(先修测试再报 BUG)" if test_defects else ""),
                    failures=fails[:30], triage=S.triage, evidence=[S.ev.dir],
                    next_actions=["final_gate"])


def _do_run_schema_tests(a):
    S.require_schema = a.get("required", S.require_schema)
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


def _do_run_scenario_tests(a):
    S.ev = S.ev or core.Evidence()
    r = core.run_karate(a["feature"], S.ev)
    if r["status"] != "SKIPPED_WITH_REASON":
        S.results.append({"id": "KARATE-" + os.path.basename(a["feature"]), "priority": "P0",
                          "status": r["status"], "evidence": r.get("evidence", "")})
    return envelope(r["status"], f"karate: {r.get('reason', r.get('exit_code'))}",
                    evidence=[r.get("evidence", "")])


def _do_provision_environment(a):
    if a.get("release"):
        core.docker_release(a["release"])
        return envelope("PASS", f"container {a['release']} destroyed")
    r = core.docker_provision(a["image"], a["name"], int(a["host_port"]),
                              a.get("container_port"), a.get("env"))
    return envelope(r["status"], json.dumps(r, ensure_ascii=False),
                    next_actions=["prepare_verification"] if r["status"] == "AVAILABLE" else [])


def _do_add_regression_case(a):
    root = a.get("consumer_root") or S.consumer_root
    return envelope("PASS", f"registry size {core.add_regression_case(a['case'], a.get('reason',''), root)}")


def _coverage_matrix():
    dims = {}
    for c in S.plan:
        dims[c.get("category", "?")] = dims.get(c.get("category", "?"), 0) + 1
    return dims


def _write_cases_without_evidence():
    """§29：写入类（POST/PUT/PATCH/DELETE）case 标了 PASS 却没有任何 DB/KV 副作用断言
    （且非负例 db_no_write）→ 无前后态证据，不得算验证过。返回 case id 清单。"""
    out = []
    for r, c in zip(S.results, S.executed_cases):
        if r.get("status") != "PASS":
            continue
        ep = coverage.case_endpoint(c)
        if ep.split(" ", 1)[0] not in ("POST", "PUT", "PATCH", "DELETE"):
            continue
        exp = (c or {}).get("expected") or {}
        if any(k in exp for k in core.DB_ASSERTIONS + core.KV_ASSERTIONS):
            continue
        out.append(r["id"])
    return out


def _endpoint_ledger():
    """§29 接口覆盖台账：声明端点全集 − 已执行 case 触达端点。
    无声明端点全集（未给 openapi/facts/impact）→ status=UNKNOWN，final_gate 不许据此 PASS。"""
    declared = coverage.declared_endpoints(facts=S.facts, impact_report=S.impact,
                                           openapi_spec=S.openapi_spec)
    if not declared:
        return {"status": "UNKNOWN", "declared": 0, "covered": 0,
                "not_tested": [], "not_tested_writes": [], "coverage_pct": None,
                "note": "无端点全集事实（openapi/schema_sql/analyze_impact 三者至少其一）"}
    res = coverage.ledger(declared, list(zip(S.results, S.executed_cases)))
    res["status"] = "OK"
    res["declared_list"] = declared
    return res


def _failure_list():
    fails = [r for r in S.results if r["status"] == "FAIL"]
    return [{"id": f["id"], "detail": f.get("detail")} for f in fails][:50]


# ───────────────────────── 门面 dispatch（V10 §29：对外仅此 7 个）─────────────────────────

def dispatch(name, a):
    a = normalize_args(a or {})
    if name == "analyze_impact":
        root = a.get("path") or core.ROOT
        if a.get("java_graph", True):
            S.impact_graph = impact.build_graph(root, max_files=int(a.get("max_files", 800)))
        reg_reasons = [c.get("regression_reason", "") for c in core.load_registry(
            consumer_root=S.consumer_root) if c.get("regression_reason")]
        changed = a.get("changed")
        rep = impact.analyze(root, changed=changed, base_sha=a.get("base_sha"),
                             head_sha=a.get("head_sha"), regression_reasons=reg_reasons,
                             graph=S.impact_graph, patterns=a.get("patterns"))
        S.impact = rep
        if S.ev:
            S.ev.write("impact.json", rep)
        pf = rep.get("pattern_findings") or []
        inspected = _do_inspect_project({"path": root}) if a.get("inspect") else None
        return envelope("PASS", f"impact: {len(rep['changed_symbols'])} changed, "
                        f"{len(rep['affected_endpoints'])} affected endpoints, risk={rep['risk_level']}"
                        + (f", {len(pf)} high-risk call sites" if pf else ""),
                        impact=rep, inspect=inspected,
                        pattern_findings=pf,
                        next_actions=["plan_verification", "run_verification"])
    if name == "plan_verification":
        steps = []
        if a.get("task_bundle"):
            r = _do_intake_task({"task_bundle": a["task_bundle"], "consumer_root": a.get("consumer_root")})
            steps.append(("intake", r["status"]))
            if r["status"] == "BLOCKED":
                return r
        # DevTest Hub：handoff_id → 只吃 contract.sections（hints 禁入 facts，AC-2/AC-3）
        if a.get("handoff_id"):
            try:
                hf = _load_handoff_contract(a["handoff_id"])
            except Exception as exc:
                return envelope("BLOCKED", f"handoff {a['handoff_id']} 取不到契约: {exc!r}",
                                next_actions=["确认 Hub 在跑、handoff_id 与 project_id 正确"])
            if not hf:
                return envelope("BLOCKED", f"handoff {a['handoff_id']} contract.sections 全空 — 无契约不建 plan",
                                next_actions=["让开发 AI PATCH contract §0–§9 后再 submit"])
            for item in hf:
                S.facts.add_raw(item)
            steps.append(("handoff_facts", len(hf)))
        fact_args = {k: a[k] for k in ("schema_sql", "openapi") if a.get(k)}
        if fact_args:
            r = _do_collect_facts(fact_args)
            steps.append(("facts", r["status"]))
            if r["status"] == "BLOCKED":
                return r
        if a.get("facts"):
            r = _do_add_facts({"facts": a["facts"]})
            steps.append(("facts+", r["status"]))
            if r["status"] == "BLOCKED":
                return r
        if a.get("ask"):
            r = _do_ask_user(a["ask"])
            steps.append(("ask", r["status"]))
        if a.get("answer"):
            r = _do_answer_question(a["answer"])
            steps.append(("answer", r["status"]))
        if a.get("contract"):
            r = _do_build_contract(a["contract"])
            steps.append(("contract", r["status"]))
            if r["status"] == "BLOCKED":
                return r
        if a.get("plan"):
            r = _do_plan_tests(a["plan"])
            steps.append(("plan", r["status"]))
            if r["status"] == "BLOCKED":
                return r
        if a.get("cases"):
            r = _do_generate_cases({"cases": a["cases"]})
            steps.append(("cases", r["status"]))
        # V10 §5-10 RuleMind + ScenarioMind：规则→场景推导（证据驱动，缺轴跳过）
        if a.get("rules") or a.get("scenario"):
            from testmind import rules as _r, scenario as _sc
            rule_objs = _r.load_rules(a["rules"]) if a.get("rules") else (S.rules or [])
            S.rules = rule_objs
            sc = a.get("scenario") or {}
            roles = sc.get("roles")
            # §29 增强：角色轴自动取证——宿主 AI 声明 actor_evidence（label→SELECT DISTINCT SQL），
            # 引擎执行拿真实值，不靠"自觉"手填账号矩阵；没给且没 roles → 记 UNKNOWN 提醒补。
            if not roles and sc.get("actor_evidence") and S.db:
                roles = []
                for label, sql in sc["actor_evidence"].items():
                    if not re.match(r"^\s*SELECT\b", sql, re.I):
                        continue                     # 只读取值，写语句拒绝
                    vals = sorted({str(v) for row in S.db.rows(sql) for v in row.values()})
                    roles += [f"{label}:{v}" for v in vals[:20]]
                    S.facts.add_raw({"topic": f"actor:{label}",
                                     "statement": f"actor values {label}: {vals[:20]}",
                                     "source": f"DB DISTINCT: {sql[:80]}"})
                steps.append(("actor_evidence", len(roles)))
            if not roles and (sc.get("states") or sc.get("times") or rule_objs):
                S.facts.ask("测试账号矩阵（roles）从哪取证？",
                            "缺 Actor 轴则跨角色/跨公司场景不会生成（真实整改案例：矩阵太窄漏越权类 bug）",
                            "ScenarioMind 的 actor 轴与时间×角色组合", sc.get("options", []))
            derived = _sc.derive(impact_report=getattr(S, "impact", None),
                                 rules=rule_objs, roles=roles,
                                 states=sc.get("states"), times=sc.get("times"),
                                 entry_points=sc.get("entry_points"),
                                 operations=sc.get("operations"),
                                 history_cases=sc.get("history_cases"))
            ok = [c for c in derived if _sc.validate_case_provenance(c)[0]]
            S.scenarios = (S.scenarios or []) + ok
            executable = [c for c in ok if c.get("action")]   # 已物化的才进 plan
            S.plan += executable
            S.ev = S.ev or core.Evidence()
            S.ev.write("scenario.json", {"derived": len(derived), "provenance_ok": len(ok),
                                         "rejected": len(derived) - len(ok), "cases": ok})
            steps.append(("scenario_derive", f"{len(ok)}/{len(derived)}"))
        # V10 §11/§12 Cross-Endpoint Oracle + Metamorphic（需要 oracle spec）
        if a.get("oracle"):
            from testmind import oracle as _o, scenario as _sc2
            o = a["oracle"]
            eps = o.get("endpoints") or sorted((getattr(S, "impact", None) or {}).get("affected_endpoints", {}))
            ocases = _o.plan(eps, o.get("spec", {}), actors=o.get("actors"), rules=o.get("rules"))
            ok_o = [c for c in ocases if _sc2.validate_case_provenance(c)[0]]
            S.plan += ok_o
            S.ev = S.ev or core.Evidence()
            S.ev.write("oracle.json", {"endpoints": eps, "cases": ok_o})
            steps.append(("oracle_plan", len(ok_o)))
        pend = [q for q in S.facts.questions if q.get("state", "USER_REQUIRED") == "USER_REQUIRED"]
        return envelope("PASS", " | ".join(f"{t}:{s}" for t, s in steps) or "no-op",
                        steps=steps, plan=len(S.plan), scenarios=len(S.scenarios or []),
                        unknowns=[q["id"] + ": " + q["question"] for q in pend],
                        next_actions=["prepare_verification"] if S.plan else ["plan_verification"])
    if name == "prepare_verification":
        if a.get("provision"):
            r = _do_provision_environment(a["provision"])
            if r["status"] != "PASS" and r["status"] != "AVAILABLE":
                return r
        env = dict(a.get("env") or {})
        # 仅 seed 且已有 db 连接（如 data_plan 后续补种）时，不重复走 env 准备
        if env or a.get("provision") or a.get("data_plan") or not (S.engine or S.db):
            r = _do_prepare_environment(env)
            if r["status"] != "PASS":
                return r
        else:
            r = envelope("PASS", "environment already prepared")
        # V10 §13-15 DataTruth：数据前置条件由 inventory→REUSE→REPAIR→CREATE 供给，
        # 全部写操作走 Seed Ledger，供给后重查询证明 VERIFIED（§17），不是假设。
        if a.get("data_plan"):
            dp = a["data_plan"]
            if not S.db:
                return envelope("BLOCKED", "data_plan requires a prepared db connection")
            cls = S.env_class or env_policy.classify_env(a.get("env_class"))
            if cls not in ("local", "test") and a.get("example") != "red-packet":
                return envelope("BLOCKED", f"data_plan blocked for env_class={cls} (local|test only, §13.4)")
            from testmind import datatruth as _dt
            from testmind.ledger import SeedLedger
            if S.ledger is None:
                S.ledger = SeedLedger(core.now(), root=S.consumer_root or core.ROOT)
            reqs = dp.get("requirements") or [
                c["data_requirement"] for c in S.plan if c.get("data_requirement")]
            plans = _dt.plan(S.db, reqs, ownership=dp.get("ownership"))
            S.data_plan = plans
            res = _dt.provision(S.db, plans, S.ledger, dp.get("template", {}),
                                case_id=dp.get("case_id", "data_plan"),
                                dataset_id=dp.get("dataset_id", ""))
            S.ev = S.ev or core.Evidence()
            S.ev.write("data_plan.json", {"plans": plans, "provision": res})
            if res["status"] != "VERIFIED":
                return envelope("BLOCKED", "DATA_TRUTH_NOT_VERIFIED: precondition rows missing",
                                data_plan=res["results"], next_actions=["prepare_verification"])
        if a.get("seed"):
            r2 = _do_seed_database({"statements": a["seed"], "reason": "prepare_verification.seed"})
            if r2["status"] != "PASS":
                return r2
        return r
    if name == "run_verification":
        explicit = any(a.get(k) is not None for k in
                       ("case", "case_ids", "concurrency", "failure", "schema", "scenario",
                        "add_regression", "regression"))
        adv = None
        if a.get("precheck"):
            adv = _do_static_precheck(a["precheck"])
            if adv["status"] == "FAIL":
                return adv
        if a.get("perf"):
            adv = _do_sql_perf_check(a["perf"])   # advisory，不阻断
        # 纯 advisory（无显式执行参数）：返回巡检结果本身，绝不偷跑 suite
        if adv is not None and not explicit:
            return adv
        if not explicit and not S.plan:
            return envelope("BLOCKED", "nothing to run — plan first", next_actions=["plan_verification"])
        if a.get("case"):
            return _do_run_exec("single", {"case": a["case"]})
        if a.get("concurrency"):
            return _do_run_exec("concurrent", a["concurrency"])
        if a.get("failure"):
            return _do_run_exec("fault", a["failure"])
        if a.get("schema"):
            return _do_run_schema_tests(a["schema"])
        if a.get("scenario"):
            return _do_run_scenario_tests(a["scenario"])
        if a.get("add_regression"):
            return _do_add_regression_case(a["add_regression"])
        if a.get("regression"):
            _do_run_exec("regression", {})
        return _do_run_exec("suite", {"case_ids": a.get("case_ids")})
    if name == "replay_failure":
        cid = a.get("case_id")
        case = next((c for c in S.plan if c.get("id") == cid), None)
        if not case:
            return envelope("BLOCKED", f"case {cid} not in plan", next_actions=["plan_verification"])
        return _do_run_exec("single", {"case": case})
    if name == "final_gate":
        if not S.results:
            return envelope("NOT_TESTED", "nothing executed — No Execution → No PASS")
        runners = {}
        if S.schema:
            runners["schemathesis"] = S.schema.get("status", "NOT_RUN")
        req = ["schemathesis"] if (S.require_schema or a.get("require_schema")) else []
        st, why = core.Gate.evaluate(S.results, S.facts.questions, S.facts.conflicts(),
                                      floor=core.risk_floor(S.facts, S.hooks),
                                      runner_states=runners, required_runners=req,
                                      plan=S.plan)
        if st == "PASS" and S.schema and S.schema["status"] == "FAIL":
            st, why = "FAIL", "schemathesis findings unexplained（见 schema-tests-summary.txt）"   # §25 失败必须回分析
        # §29 接口覆盖台账：未覆盖写端点 / 无端点全集 → PASS 降 HOLD，杜绝"漏 8 个还报全 PASS"
        ep_ledger = _endpoint_ledger()
        no_ev_writes = _write_cases_without_evidence()
        if S.ev:
            S.ev.write("endpoint_ledger.json", {**ep_ledger, "writes_without_side_effect_assertion": no_ev_writes})
        if st == "PASS":
            if ep_ledger["status"] == "UNKNOWN":
                st, why = "HOLD", "endpoint universe unknown（未给 openapi/schema_sql/analyze_impact）— 覆盖无法证明，不得 PASS"
            elif ep_ledger["not_tested_writes"]:
                st, why = "HOLD", (f"{len(ep_ledger['not_tested_writes'])} write endpoints NOT_TESTED: "
                                   f"{ep_ledger['not_tested_writes'][:10]} — 写端点未全覆盖，不得 PASS")
            elif no_ev_writes and a.get("require_db_evidence"):
                st, why = "HOLD", (f"{len(no_ev_writes)} write cases PASS without DB/KV diff: "
                                   f"{no_ev_writes[:10]} — 写入类必须带前后态断言")
        meta = {}
        # V10 §27/§28：Quality Score 与 Gate 并行产出（仅实测计分，缺数据=NOT_MEASURED）
        q = score.compute(results=S.results, impact=getattr(S, "impact", None),
                          rules=getattr(S, "rules", None), mutation=getattr(S, "mutation", None),
                          evidence_dir=S.ev.dir if S.ev else None)
        if S.ev:
            S.ev.write("quality_score.json", score.asdict(q))
        if st == "PASS" and q.verdict == "BLOCK_MERGE":
            st, why = "FAIL", f"quality blocking FAIL: {q.blocking}"
        if S.ev:
            from testmind.gate_meta import bind_pass_metadata, gate_is_stale
            meta = bind_pass_metadata(S.ev.dir)
            if st == "PASS" and gate_is_stale(meta):
                st, why = "STALE", "git_head or pipeline_contract_hash changed since evidence bind"
            S.ev.write("gate.json", {"status": st, "why": why, "runner_states": runners, **meta})
            if S.task_id:
                session_store.save_state(S.consumer_root or core.ROOT, S.task_id,
                                         {"run_id": S.ev.run_id, "gate": st, "why": why, **meta})
        # §29：export_handoff 并入 final_gate——终判后自动导出四件套
        exported = None
        if S.task_id and st in ("PASS", "FAIL", "HOLD", "BLOCKED", "NOT_TESTED", "STALE"):
            try:
                exported = _do_export_handoff({})
            except Exception:
                pass
        handoff = None
        if st in ("PASS", "FAIL", "HOLD", "BLOCKED"):
            try:
                handoff = _submit_handoff_run(st, ep_ledger)
            except Exception as exc:
                handoff = {"status": "ERROR", "error": repr(exc)}
        return envelope(st, why, evidence=[S.ev.dir] if S.ev else [],
                        quality={"total": q.total, "verdict": q.verdict, "blocking": q.blocking},
                        coverage=_coverage_matrix(), endpoint_coverage=ep_ledger,
                        writes_without_side_effect_assertion=no_ev_writes,
                        failures=_failure_list(), triage=S.triage,
                        artifacts=(exported or {}).get("artifacts", []),
                        evidence_manifest_hash=meta.get("evidence_manifest_hash", ""),
                        handoff=handoff)
    if name == "cleanup":
        # V10 §17 数据生命周期：PASS → Ledger 逆序回滚 + 重查询证明 DB==before；
        # FAIL → 冻结现场 incident.json，保留数据不清理（供排查/重放）。
        from testmind import isolation
        report = {}
        fails = [r for r in S.results if r.get("status") == "FAIL"]
        if S.ledger is not None and S.db is not None:
            if fails and not a.get("force_rollback"):
                for r in fails:
                    S.ledger.freeze(S.ev.dir if S.ev else core.ROOT, r["id"],
                                    request=r.get("detail", {}), response=r.get("detail", {}))
                report["frozen"] = [r["id"] for r in fails]
            else:
                report["rollback"] = S.ledger.rollback(S.db)
                if report["rollback"].get("unresolved"):
                    return envelope("BLOCKED", f"UNRESOLVED_LEDGER_ENTRIES: {report['rollback']['unresolved']}",
                                    cleanup=report)
        isolation.reset_runtime(S)
        iso = isolation.reset_task(S)
        report["task_reset"] = iso["cleared"]
        return envelope("PASS", f"closed: {report}", cleanup=report)
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
                "serverInfo": {"name": "testmind", "version": "2.0.0"}}}
        elif m == "tools/list":
            tools = [{"name": n, "description": d["desc"], "inputSchema": tool_schemas.input_schema(n)}
                     for n, d in FACADES.items()]
            resp = {"jsonrpc": "2.0", "id": req.get("id"), "result": {"tools": tools}}
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

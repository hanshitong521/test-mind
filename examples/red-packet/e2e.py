# examples/red-packet/e2e.py — 红包复杂业务闭环（全部使用 testmind 通用引擎，无本地执行逻辑）
import json, os, sys

TM = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, TM)
sys.path.insert(0, os.path.join(TM, "examples", "red-packet"))

from testmind import core
from testmind.faultproxy import FaultProxy
from sut import serve, OPENAPI, SCHEMA_SQL

BASE = "http://127.0.0.1:18101"


def canonical_input():
    return {"name": "开工红包", "amount_cents": 100, "quantity": 10,
            "influencer_id": 1, "created_by": 1, "unlimited": False}


def collect_facts():
    facts = core.FactResolver.from_schema_sql(SCHEMA_SQL, "examples/red-packet/sut.py:SCHEMA_SQL")
    for x in core.FactResolver.from_openapi(OPENAPI, "examples/red-packet/sut.py:OPENAPI").f:
        facts.f.append(x)
    facts.add("idempotency:create", "idem_key 提供时重复创建返回已有 id (duplicate=true)", "sut.py:create()")
    facts.add("state:grant", "grant 仅在 status=RUNNING 且未过期且 remaining>0 时成功", "sut.py:grant()")
    facts.add("dependency:grant", "grant 前调用外部风控 HTTP，失败/超时=502 且必须无写库", "sut.py:grant()")
    facts.add("relation:influencer", "失效/删除/不存在达人创建返回 404", "sut.py:create()")
    facts.add("time:expiry", "expiry_ts 空=无期限；now>expiry_ts 拒绝；now==expiry_ts 允许", "sut.py:grant()")
    facts.add("state:transitions", "合法迁移 CREATED->{start,delete} RUNNING->{pause,finish,delete} PAUSED->{start,delete}，其余 409", "sut.py:VALID_TRANSITIONS")
    facts.add("delete:soft", "delete 为逻辑删除（deleted=1），删除后接口 404", "sut.py:transition()")
    facts.add("scheduler:tick", "RUNNING 且 scheduled_at<=now 且未派发 → tick 恰好派发一次并写 grant_log；重复 tick 不重派", "sut.py:tick()")
    facts.add("seed:influencers", "种子数据 1=alice有效 2=bob禁用 3=carol他公司有效 4=ghost已删", "sut.py:SUT.__init__()")
    facts.derive("p0:money", "金额与数量均为 P0 金额类风险字段",
                 facts.confirmed("range:amount_cents")[0]["id"], facts.confirmed("range:quantity")[0]["id"])
    # §44 自问自答：先问 → 用代码事实自答；答不了才留 USER_REQUIRED
    facts.ask("运行中的红包是否允许修改面额？", "决定是否存在 modify 类测试", "影响范围=面额类字段")
    facts.answer("Q1", "SUT 未暴露任何面额修改端点（见 OPENAPI paths），该政策不在本系统范围 → 无测试义务")
    return facts


def _http(inp, exp, cid, cat, prio, src, path="/red-packets"):
    e = {"http": exp}
    if exp >= 400:
        e["db_no_write"] = True
    return {"id": cid, "category": cat, "priority": prio, "source": src,
            "action": {"kind": "http", "method": "POST", "path": path, "body": inp}, "expected": e}


def seed_pkt(pk, status, remaining, expiry=None, sched=None):
    i = str(pk)
    return ["DELETE FROM grant_log WHERE packet_id=" + i, "DELETE FROM red_packet WHERE id=" + i,
            "INSERT INTO red_packet(id,name,amount_cents,quantity,influencer_id,unlimited,expiry_ts,"
            "scheduled_at,status,remaining,created_by,created_at,updated_at,deleted) VALUES(" +
            f"{i},'ANCHOR-{i}',100,{remaining},1,0,{expiry or 'NULL'},{sched or 'NULL'},{status},{remaining},1,0,0,0)"]


def special_cases(rid):
    base = canonical_input()
    return [
        _http({**base, "influencer_id": 3}, 201, "P0-REL-carol", "RELATION", "P0", "F:seed:influencers"),
        _http({**base, "influencer_id": 999}, 404, "P0-REL-missing", "RELATION", "P0", "F:relation:influencer"),
        _http({**base, "influencer_id": 4}, 404, "P0-REL-deleted", "RELATION", "P0", "F:relation:influencer"),
        _http({**base, "influencer_id": 2}, 404, "P0-REL-disabled", "RELATION", "P0", "F:relation:influencer"),
        # ── §20 定时任务矩阵（fake-clock 驱动，零等待）──
        {"id": "P0-SCH-notdue", "category": "SCHEDULER", "priority": "P1", "source": "F:scheduler:tick",
         "setup": seed_pkt(9030, 1, 5, sched=3000000000),
         "action": {"kind": "http", "method": "POST", "path": "/scheduler/tick",
                    "headers": {"X-Test-Now": "2999999999"}},
         "expected": {"http": 200, "json": {"dispatched": 0},
                      "db_count": [{"sql": "SELECT COUNT(*) FROM grant_log WHERE packet_id=9030", "expect": 0}]}},
        {"id": "P0-SCH-due", "category": "SCHEDULER", "priority": "P0", "source": "F:scheduler:tick",
         "setup": seed_pkt(9031, 1, 5, sched=3000000000),
         "action": {"kind": "http", "method": "POST", "path": "/scheduler/tick",
                    "headers": {"X-Test-Now": "3000000000"}},
         "expected": {"http": 200, "db_count": [
             {"sql": "SELECT COUNT(*) FROM grant_log WHERE packet_id=9031", "expect": 1},
             {"sql": "SELECT dispatched FROM red_packet WHERE id=9031", "expect": 1}]}},
        {"id": "P0-SCH-double-tick", "category": "SCHEDULER", "priority": "P0", "source": "F:scheduler:tick",
         "setup": seed_pkt(9032, 1, 5, sched=3000000000),
         "action": {"kind": "sequence", "steps": [
             {"path": "/scheduler/tick", "expect_status": 200, "headers": {"X-Test-Now": "3000000000"}},
             {"path": "/scheduler/tick", "expect_status": 200, "headers": {"X-Test-Now": "3000000000"}}]},
         "expected": {"db_count": [{"sql": "SELECT COUNT(*) FROM grant_log WHERE packet_id=9032", "expect": 1}]}},
        {"id": "P0-SCH-paused-nothing", "category": "SCHEDULER", "priority": "P1", "source": "F:scheduler:tick",
         "setup": seed_pkt(9033, 2, 5, sched=3000000000),          # PAUSED 不派发
         "action": {"kind": "http", "method": "POST", "path": "/scheduler/tick",
                    "headers": {"X-Test-Now": "3000000001"}},
         "expected": {"http": 200, "db_count": [{"sql": "SELECT COUNT(*) FROM grant_log WHERE packet_id=9033", "expect": 0}]}},
        # ── 闰日/跨年边界（2024-02-29T23:59:59Z=1709251199，次日=1709251200）──
        {"id": "P0-TIME-leapday-at", "category": "TIME", "priority": "P1", "source": "F:time:expiry",
         "setup": seed_pkt(9040, 1, 5, expiry=1709251199),
         "action": {"kind": "http", "method": "POST", "path": "/red-packets/9040/grant",
                    "headers": {"X-Test-Now": "1709251199"}},
         "expected": {"http": 200, "json": {"granted": True}}},
        {"id": "P0-TIME-leapday-next", "category": "TIME", "priority": "P1", "source": "F:time:expiry",
         "setup": seed_pkt(9041, 1, 5, expiry=1709251199),
         "action": {"kind": "http", "method": "POST", "path": "/red-packets/9041/grant",
                    "headers": {"X-Test-Now": "1709251200"}},
         "expected": {"http": 409}},
        {"id": "P0-IDEM-replay", "category": "IDEMPOTENCY", "priority": "P0", "source": "F:idempotency:create",
         "action": {"kind": "sequence", "steps": [
             {"path": "/red-packets", "body": {**base, "idem_key": f"idem-{rid}"}, "expect_status": 201},
             {"path": "/red-packets", "body": {**base, "idem_key": f"idem-{rid}"}, "expect_status": 200}]},
         "expected": {"db_count": [{"sql": f"SELECT COUNT(*) FROM red_packet WHERE idem_key='idem-{rid}'", "expect": 1}]}},
        {"id": "P0-CONC-create", "category": "CONCURRENCY", "priority": "P0", "source": "F:idempotency:create",
         "action": {"kind": "concurrent", "count": 8, "path": "/red-packets", "body": {**base, "idem_key": f"conc-{rid}"}},
         "expected": {"success_count": 8,
                      "db_count": [{"sql": f"SELECT COUNT(*) FROM red_packet WHERE idem_key='conc-{rid}'", "expect": 1}]}},
        {"id": "P0-CONC-grant", "category": "CONCURRENCY", "priority": "P0", "source": "F:state:grant",
         "setup": seed_pkt(9001, 1, 5),
         "action": {"kind": "concurrent", "count": 16, "path": "/red-packets/9001/grant"},
         "expected": {"success_count": 5,
                      "db_count": [{"sql": "SELECT COUNT(*) FROM grant_log WHERE packet_id=9001", "expect": 5},
                                   {"sql": "SELECT remaining FROM red_packet WHERE id=9001", "expect": 0}]}},
        {"id": "P0-DEP-status503", "category": "FAILURE_INJECTION", "priority": "P0", "source": "F:dependency:grant",
         "setup": seed_pkt(9002, 1, 5), "action": {"kind": "fault", "mode": {"kind": "status", "code": 503},
                                                   "path": "/red-packets/9002/grant"},
         "expected": {"http": 502, "db_count": [{"sql": "SELECT COUNT(*) FROM grant_log WHERE packet_id=9002", "expect": 0}]}},
        {"id": "P0-DEP-timeout", "category": "FAILURE_INJECTION", "priority": "P0", "source": "F:dependency:grant",
         "setup": seed_pkt(9003, 1, 5), "action": {"kind": "fault", "mode": {"kind": "delay", "seconds": 5},
                                                   "path": "/red-packets/9003/grant"},
         "expected": {"http": 502, "db_count": [{"sql": "SELECT COUNT(*) FROM grant_log WHERE packet_id=9003", "expect": 0}]}},
        {"id": "P0-DEP-refuse", "category": "FAILURE_INJECTION", "priority": "P0", "source": "F:dependency:grant",
         "setup": seed_pkt(9004, 1, 5), "action": {"kind": "fault", "mode": "refuse", "path": "/red-packets/9004/grant"},
         "expected": {"http": 502, "db_count": [{"sql": "SELECT COUNT(*) FROM grant_log WHERE packet_id=9004", "expect": 0}]}},
        {"id": "P0-DEP-badjson", "category": "FAILURE_INJECTION", "priority": "P0", "source": "F:dependency:grant",
         "setup": seed_pkt(9005, 1, 5), "action": {"kind": "fault", "mode": "bad_json", "path": "/red-packets/9005/grant"},
         "expected": {"http": 502, "db_count": [{"sql": "SELECT COUNT(*) FROM grant_log WHERE packet_id=9005", "expect": 0}]}},
        {"id": "P0-TIME-before", "category": "TIME", "priority": "P0", "source": "F:time:expiry",
         "setup": seed_pkt(9010, 1, 5, expiry=2000000000),
         "action": {"kind": "http", "method": "POST", "path": "/red-packets/9010/grant",
                    "headers": {"X-Test-Now": "1999999999"}}, "expected": {"http": 200, "json": {"granted": True}}},
        {"id": "P0-TIME-at", "category": "TIME", "priority": "P0", "source": "F:time:expiry",
         "setup": seed_pkt(9011, 1, 5, expiry=2000000000),
         "action": {"kind": "http", "method": "POST", "path": "/red-packets/9011/grant",
                    "headers": {"X-Test-Now": "2000000000"}}, "expected": {"http": 200, "json": {"granted": True}}},
        {"id": "P0-TIME-after", "category": "TIME", "priority": "P0", "source": "F:time:expiry",
         "setup": seed_pkt(9012, 1, 5, expiry=2000000000),
         "action": {"kind": "http", "method": "POST", "path": "/red-packets/9012/grant",
                    "headers": {"X-Test-Now": "2000000001"}}, "expected": {"http": 409}},
        {"id": "P0-ST-leg-chain", "category": "STATE_MACHINE", "priority": "P0", "source": "F:state:transitions",
         "action": {"kind": "sequence", "steps": [
             {"path": "/red-packets", "body": base, "expect_status": 201, "save": {"pid": "id"}},
             {"path": "/red-packets/{{pid}}/start", "expect_status": 200},
             {"path": "/red-packets/{{pid}}/pause", "expect_status": 200},
             {"path": "/red-packets/{{pid}}/start", "expect_status": 200},
             {"path": "/red-packets/{{pid}}/finish", "expect_status": 200}]},
         "expected": {"db_rows": [{"table": "red_packet", "key": {"id": "{{pid}}"}, "subset": {"status": 3}}]}},
        {"id": "P0-ST-illegal-finish-start", "category": "STATE_MACHINE", "priority": "P0", "source": "F:state:transitions",
         "setup": seed_pkt(9020, 3, 5), "action": {"kind": "http", "method": "POST", "path": "/red-packets/9020/start"},
         "expected": {"http": 409}},
        {"id": "P0-ST-illegal-paused-finish", "category": "STATE_MACHINE", "priority": "P0", "source": "F:state:transitions",
         "setup": seed_pkt(9021, 2, 5), "action": {"kind": "http", "method": "POST", "path": "/red-packets/9021/finish"},
         "expected": {"http": 409}},
        {"id": "P0-ST-created-grant-blocked", "category": "STATE_MACHINE", "priority": "P0", "source": "F:state:grant",
         "setup": seed_pkt(9022, 0, 5), "action": {"kind": "http", "method": "POST", "path": "/red-packets/9022/grant"},
         "expected": {"http": 409}},
        {"id": "P0-ST-delete-then-read", "category": "STATE_MACHINE", "priority": "P0", "source": "F:delete:soft",
         "action": {"kind": "sequence", "steps": [
             {"path": "/red-packets", "body": base, "expect_status": 201, "save": {"pid": "id"}},
             {"path": "/red-packets/{{pid}}/delete", "expect_status": 200},
             {"path": "/red-packets/{{pid}}/grant", "expect_status": 404}]},
         "expected": {"db_rows": [{"table": "red_packet", "key": {"id": "{{pid}}"}, "subset": {"deleted": 1}}]}},
    ]


REGRESSION_SEEDS = [
    ({"id": "REG-001-oversell", "category": "REGRESSION", "priority": "P0", "source": "缺陷:超发修复回归锚",
      "setup": seed_pkt(9501, 1, 5),
      "action": {"kind": "concurrent", "count": 12, "path": "/red-packets/9501/grant"},
      "expected": {"success_count": 5,
                   "db_count": [{"sql": "SELECT COUNT(*) FROM grant_log WHERE packet_id=9501", "expect": 5},
                                {"sql": "SELECT remaining FROM red_packet WHERE id=9501", "expect": 0}]}},
     "grant 卖光仍写 grant_log 的缺陷永久回归"),
    ({"id": "REG-002-insert-align", "category": "REGRESSION", "priority": "P0", "source": "缺陷:INSERT列错位修复回归锚",
      "action": {"kind": "http", "method": "POST", "path": "/red-packets",
                 "body": {**canonical_input(), "quantity": 7}, "save": {"pid": "id"}},
      "expected": {"http": 201,
                   "db_rows": [{"table": "red_packet", "key": {"id": "{{pid}}"},
                                "subset": {"remaining": 7, "quantity": 7, "status": 0}}]}},
     "INSERT 列错位导致 remaining 恒 0 的缺陷永久回归"),
    ({"id": "REG-003-sched-cas", "category": "REGRESSION", "priority": "P0", "source": "调度CAS守卫回归锚",
      "setup": seed_pkt(9503, 1, 5, sched=3000000000),
      "action": {"kind": "sequence", "steps": [
          {"path": "/scheduler/tick", "expect_status": 200, "headers": {"X-Test-Now": "3000000000"}},
          {"path": "/scheduler/tick", "expect_status": 200, "headers": {"X-Test-Now": "3000000000"}},
          {"path": "/scheduler/tick", "expect_status": 200, "headers": {"X-Test-Now": "3000000000"}}]},
      "expected": {"db_count": [{"sql": "SELECT COUNT(*) FROM grant_log WHERE packet_id=9503", "expect": 1}]}},
     "重复/多实例 tick 重派防护永久回归（§20）"),
]


def run():
    ev = core.Evidence()
    facts = collect_facts()
    conflicts = facts.conflicts()
    if conflicts:
        ev.write("contract.json", {"blocked": "conflicts", "conflicts": conflicts})
        return finish(ev, facts, conflicts, [], "BLOCKED", "conflicts block contract")
    contract = core.build_contract("RED_PACKET_CREATE", {"endpoint": "POST /red-packets", "table": "red_packet"},
                                   facts, canonical_input(), {"http": 201})
    ev.write("contract.json", contract)
    ev.write("questions.json", facts.snapshot())

    dbp = os.path.join(TM, "examples", "red-packet", "sut.db")
    if os.path.exists(dbp):
        os.remove(dbp)
    srv, sut = serve(db_path=dbp, port=18101)
    proxy = FaultProxy(BASE, port=18199)
    sut.dependency_url = proxy.url() + "/risk"
    db = core.DBCheck(core.open_db({"kind": "sqlite", "path": dbp}), tables=("red_packet", "grant_log"))
    engine = core.Engine(BASE, ev, db=db, proxy=proxy)

    overrides = {   # overrides 挂 F:seed:influencers / API range 事实，不算脑补
        "influencer_id": lambda v: 400 if v == 0 or v > 2147483647 else (201 if v == 1 else 404),
        "created_by": lambda v: 201 if 1 <= v <= 2147483647 else 400,
    }
    cases = core.plan_cases(facts, canonical_input(), method="POST", path="/red-packets",
                            overrides=overrides, ok_status=201, bad_status=400)
    cases += special_cases(ev.run_id)
    ev.write("plan.json", {"plan_id": "RP-E2E-" + ev.run_id, "cases": cases,
                           "tools": ["testmind.core.Engine", "schemathesis"]})

    cov = None
    try:                                   # SUT 进程内启动 → 真行覆盖（JaCoCo 不可用时的 Python 侧替代）
        import coverage
        cov = coverage.Coverage(source=[os.path.join(TM, "examples", "red-packet")], omit=["*e2e.py*"],
                                data_file=os.path.join(ev.dir, ".coverage"))
        cov.start()
    except ImportError:
        pass
    results = [engine.run(c) for c in cases]

    for case, reason in REGRESSION_SEEDS:
        core.add_regression_case(case, reason)
    results += core.run_regression(engine)
    ev.write("results.json", results)

    coverage_pct = None
    if cov:
        import io
        cov.stop()
        cov.save()
        buf = io.StringIO()
        cov.report(file=buf, skip_empty=True)
        ev.text("coverage.txt", buf.getvalue())
        try:
            coverage_pct = float(buf.getvalue().splitlines()[-1].split()[-1].rstrip("%"))
        except Exception:
            coverage_pct = None

    status, why = core.Gate.evaluate(results, facts.questions, facts.conflicts())
    schema = core.run_schema_tests(BASE + "/openapi.json", ev)
    if status == "PASS" and schema["status"] == "FAIL":
        status, why = "FAIL", "schemathesis findings unexplained（见 schema-tests-summary.txt）"   # §25
    srv.shutdown()
    proxy.shutdown()
    return finish(ev, facts, conflicts, results, status, why, schema, coverage_pct)


def finish(ev, facts, conflicts, results, status, why="", schema=None, coverage_pct=None):
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    report = {
        "run_id": ev.run_id, "final": status, "why": why, "utc": core.utc(),
        "facts_confirmed": sum(1 for f in facts.f if f["status"] == "CONFIRMED"),
        "facts_derived": sum(1 for f in facts.f if f["status"] == "DERIVED"),
        "unknowns": [q for q in facts.questions if q.get("state") == "USER_REQUIRED"],
        "questions_selfanswered": [q for q in facts.questions if q.get("state") != "USER_REQUIRED"],
        "conflicts": conflicts, "counts": counts,
        "categories": sorted({r["id"].split("-")[0] for r in results}),
        "coverage_pct": coverage_pct,
        "schema_tests": schema or {"status": "SKIPPED_WITH_REASON", "reason": "not run"},
        "runner_availability": core.scan_runners(), "evidence_dir": ev.dir,
    }
    ev.write("run.json", report)
    from testmind.report import write_final_report
    write_final_report(ev, report, results)
    print(json.dumps({k: report[k] for k in ("run_id", "final", "why", "counts", "schema_tests")},
                     ensure_ascii=False, indent=1))
    return status


if __name__ == "__main__":
    sys.exit(0 if run() == "PASS" else 1)

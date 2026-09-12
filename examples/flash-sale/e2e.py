# examples/flash-sale/e2e.py — 秒杀下单复杂业务闭环（全部走 testmind 通用引擎，无本地执行逻辑）
# 事实 → 契约 → 计划(自动边界/负例) + 手写风险(超发/幂等/关系/状态机/时间/依赖/守恒) → 执行+DB对拍 → Gate
import json, os, sys

TM = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, TM)
sys.path.insert(0, os.path.join(TM, "examples", "flash-sale"))

from testmind import core
from testmind.faultproxy import FaultProxy
from sut import serve, OPENAPI, SCHEMA_SQL, seed_order_sql

BASE = "http://127.0.0.1:18201"
TABLES = ("sale_order", "stock_log", "product", "buyer")


def canonical_input():
    return {"product_id": 1, "buyer_id": 1, "qty": 10}


def collect_facts():
    """CONFIRMED 事实优先来自 DDL/OpenAPI 自动抽取；行为规则（幂等/超发/状态机/时间/依赖/守恒）来自代码出处。"""
    facts = core.FactResolver.from_schema_sql(SCHEMA_SQL, "examples/flash-sale/sut.py:SCHEMA_SQL")
    for x in core.FactResolver.from_openapi(OPENAPI, "examples/flash-sale/sut.py:OPENAPI").f:
        facts.f.append(x)
    A = "examples/flash-sale/sut.py"
    facts.add("idempotency:create", "idem_key 提供时重复创建返回已有 id (duplicate=true)，全库仅一行", A + ":create()")
    facts.add("oversell:stock", "并发下单总成功数不得超过初始库存，product.stock 恒 >=0（CAS+CHECK），售罄返回 409 且不写单", A + ":create()")
    facts.add("money:total", "total_cents = unit_price_cents * qty（下单时快照单价）", A + ":create()")
    facts.add("state:transitions", "合法迁移 PENDING0->{pay1,cancel4} PAID1->{ship2,cancel4} SHIPPED2->{complete3}；其余动作 409", A + ":VALID_TRANSITIONS")
    facts.add("state:pay", "pay 仅在 PENDING 且未过期且支付网关 ok 时成功；网关故障=502 且必须零写库", A + ":transition()")
    facts.add("dependency:pay", "pay 前真 HTTP 调支付网关，超时/非200/拒连/坏JSON 一律 502 且不落库", A + ":transition()")
    facts.add("stock:restore", "cancel/超时自动取消把库存回补（+qty 流水）；重复取消/重复 tick 不重复回补（守恒不变）", A + ":transition()+tick()")
    facts.add("relation:party", "买家缺失/已删=404，买家冻结=403，商品缺失/已删=404，商品下架=409", A + ":create()")
    facts.add("relation:tenant", "买家与商品必须同租户，否则 403 cross_tenant（下单前拒，零写库）", A + ":create()")
    facts.add("time:expiry", "expires_at 空=不过期；now>expiry 拒付 409；now==expiry 仍允许（含边界）", A + ":transition()")
    facts.add("scheduler:tick", "PENDING 且 expires_at<=now 的超时单被自动取消并回补，恰好一次；重复 tick 不重复取消", A + ":tick()")
    facts.add("seed:parties", "买家 1=alice有效 2=bob冻结 3=carol他租户有效 4=ghost已删；商品 1=上架库存足 2=下架 3=已删 4=上架库存3 5=上架库存5", A + ":seed_data_sql()")
    facts.derive("p0:money", "金额与库存均为 P0 风险字段",
                 facts.confirmed("range:qty")[0]["id"], facts.confirmed("oversell:stock")[0]["id"])
    # §44 自问自答：先问，用代码事实自答，答不了才留 USER_REQUIRED
    facts.ask("是否允许部分发货（split shipment）？", "决定是否测 ship 子数量", "影响=发货语义")
    facts.answer("Q1", "SUT 未暴露拆单/数量型 ship 端点（见 OPENAPI），该政策不在本系统范围 → 无测试义务")
    return facts


def _http(inp, exp, cid, cat, prio, src, path="/orders"):
    e = {"http": exp}
    if exp >= 400:
        e["db_no_write"] = True
    return {"id": cid, "category": cat, "priority": prio, "source": src,
            "action": {"kind": "http", "method": "POST", "path": path, "body": inp}, "expected": e}


def reset_product(pid, stock):
    """超发/守恒靶子复位：清该商品的历史单与流水并复位库存。业务数据仍确定，无随机。"""
    i = str(pid)
    return ["DELETE FROM stock_log WHERE product_id=" + i,
            "DELETE FROM sale_order WHERE product_id=" + i,
            "UPDATE product SET stock=" + str(stock) + " WHERE id=" + i]


def special_cases(rid):
    base = canonical_input()
    return [
        # ── 关系/权限（造数据里 4 个买家 5 个商品就是为这些确定性码）──
        _http({**base, "buyer_id": 3}, 403, "P0-REL-carol", "RELATION", "P0", "F:relation:tenant"),
        _http({**base, "buyer_id": 999}, 404, "P0-REL-buyer-missing", "RELATION", "P0", "F:relation:party"),
        _http({**base, "buyer_id": 4}, 404, "P0-REL-buyer-deleted", "RELATION", "P0", "F:relation:party"),
        _http({**base, "buyer_id": 2}, 403, "P0-REL-buyer-frozen", "RELATION", "P0", "F:relation:party"),
        _http({**base, "product_id": 999}, 404, "P0-REL-prod-missing", "RELATION", "P0", "F:relation:party"),
        _http({**base, "product_id": 3}, 404, "P0-REL-prod-deleted", "RELATION", "P0", "F:relation:party"),
        _http({**base, "product_id": 2}, 409, "P0-REL-prod-offshelf", "RELATION", "P0", "F:relation:party"),
        # ── 幂等 ──
        {"id": "P0-IDEM-replay", "category": "IDEMPOTENCY", "priority": "P0", "source": "F:idempotency:create",
         "action": {"kind": "sequence", "steps": [
             {"path": "/orders", "body": {**base, "idem_key": f"idem-{rid}"}, "expect_status": 201},
             {"path": "/orders", "body": {**base, "idem_key": f"idem-{rid}"}, "expect_status": 200}]},
         "expected": {"db_count": [{"sql": f"SELECT COUNT(*) FROM sale_order WHERE idem_key='idem-{rid}'", "expect": 1}]}},
        {"id": "P0-CONC-create", "category": "CONCURRENCY", "priority": "P0", "source": "F:idempotency:create",
         "action": {"kind": "concurrent", "count": 8, "path": "/orders", "body": {**base, "idem_key": f"conc-{rid}"}},
         "expected": {"success_count": 8,
                      "db_count": [{"sql": f"SELECT COUNT(*) FROM sale_order WHERE idem_key='conc-{rid}'", "expect": 1}]}},
        # ── 超发（P0 王牌：库存3 并发8 → 恰好3 成功，库存归零不转负）──
        {"id": "P0-CONC-oversell", "category": "CONCURRENCY", "priority": "P0", "source": "F:oversell:stock",
         "setup": reset_product(4, 3),
         "action": {"kind": "concurrent", "count": 8, "path": "/orders", "body": {**base, "product_id": 4, "qty": 1}},
         "expected": {"success_count": 3, "db_count": [
             {"sql": "SELECT stock FROM product WHERE id=4", "expect": 0},
             {"sql": "SELECT COUNT(*) FROM sale_order WHERE product_id=4", "expect": 3}]}},
        {"id": "P0-STOCK-exact", "category": "BOUNDARY", "priority": "P0", "source": "F:oversell:stock",
         "setup": reset_product(5, 5),
         "action": {"kind": "http", "method": "POST", "path": "/orders", "body": {**base, "product_id": 5, "qty": 5}},
         "expected": {"http": 201, "db_count": [{"sql": "SELECT stock FROM product WHERE id=5", "expect": 0}]}},
        # ── 金额守恒/口径（列错位或对角线错算的红队靶）──
        {"id": "P0-MONEY-total", "category": "DATA_INTEGRITY", "priority": "P0", "source": "F:money:total",
         "action": {"kind": "http", "method": "POST", "path": "/orders",
                    "body": {**base, "qty": 7}, "save": {"oid": "id"}},
         "expected": {"http": 201, "db_rows": [{"table": "sale_order", "key": {"id": "{{oid}}"},
                                                "subset": {"qty": 7, "unit_price_cents": 100, "total_cents": 700}}]}},
        # ── 库存回补守恒（取消回补，一次）──
        {"id": "P0-STOCK-cancel-restore", "category": "DATA_INTEGRITY", "priority": "P0", "source": "F:stock:restore",
         "setup": reset_product(5, 5),
         "action": {"kind": "sequence", "steps": [
             {"path": "/orders", "body": {**base, "product_id": 5, "qty": 2}, "expect_status": 201, "save": {"oid": "id"}},
             {"path": "/orders/{{oid}}/cancel", "expect_status": 200},
             {"path": "/orders/{{oid}}/cancel", "expect_status": 409}]},        # 二次取消非法，不得再回补
         "expected": {"http": 409, "db_count": [
             {"sql": "SELECT stock FROM product WHERE id=5", "expect": 5},
             {"sql": "SELECT COUNT(*) FROM stock_log WHERE product_id=5", "expect": 2}]}},   # -2,+2
        # ── 状态机 ──
        {"id": "P0-ST-leg-chain", "category": "STATE_MACHINE", "priority": "P0", "source": "F:state:transitions",
         "setup": reset_product(5, 5),
         "action": {"kind": "sequence", "steps": [
             {"path": "/orders", "body": {**base, "product_id": 5, "qty": 1}, "expect_status": 201, "save": {"oid": "id"}},
             {"path": "/orders/{{oid}}/pay", "expect_status": 200},
             {"path": "/orders/{{oid}}/ship", "expect_status": 200},
             {"path": "/orders/{{oid}}/complete", "expect_status": 200}]},
         "expected": {"db_rows": [{"table": "sale_order", "key": {"id": "{{oid}}"}, "subset": {"status": 3}}]}},
        {"id": "P0-ST-illegal-done-pay", "category": "STATE_MACHINE", "priority": "P0", "source": "F:state:transitions",
         "setup": seed_order_sql(9100, 5, 1, 1, 3, stock_deduct=False),
         "action": {"kind": "http", "method": "POST", "path": "/orders/9100/pay"}, "expected": {"http": 409}},
        {"id": "P0-ST-illegal-paid-pay", "category": "STATE_MACHINE", "priority": "P0", "source": "F:state:transitions",
         "setup": seed_order_sql(9101, 5, 1, 1, 1, stock_deduct=False),
         "action": {"kind": "http", "method": "POST", "path": "/orders/9101/pay"}, "expected": {"http": 409}},
        {"id": "P0-ST-cancel-then-pay", "category": "STATE_MACHINE", "priority": "P0", "source": "F:state:transitions",
         "setup": seed_order_sql(9102, 5, 1, 1, 4, stock_deduct=False),
         "action": {"kind": "http", "method": "POST", "path": "/orders/9102/pay"}, "expected": {"http": 409}},
        # ── 时间边界（可控时钟 X-Test-Now，含到期临界）──
        {"id": "P0-TIME-before", "category": "TIME", "priority": "P0", "source": "F:time:expiry",
         "setup": seed_order_sql(9110, 5, 1, 1, 0, expiry=4000000000, stock_deduct=False),
         "action": {"kind": "http", "method": "POST", "path": "/orders/9110/pay", "headers": {"X-Test-Now": "3999999999"}},
         "expected": {"http": 200, "db_rows": [{"table": "sale_order", "key": {"id": "9110"}, "subset": {"status": 1}}]}},
        {"id": "P0-TIME-at", "category": "TIME", "priority": "P0", "source": "F:time:expiry",
         "setup": seed_order_sql(9111, 5, 1, 1, 0, expiry=4000000000, stock_deduct=False),
         "action": {"kind": "http", "method": "POST", "path": "/orders/9111/pay", "headers": {"X-Test-Now": "4000000000"}},
         "expected": {"http": 200}},
        {"id": "P0-TIME-after", "category": "TIME", "priority": "P0", "source": "F:time:expiry",
         "setup": seed_order_sql(9112, 5, 1, 1, 0, expiry=4000000000, stock_deduct=False),
         "action": {"kind": "http", "method": "POST", "path": "/orders/9112/pay", "headers": {"X-Test-Now": "4000000001"}},
         "expected": {"http": 409, "db_rows": [{"table": "sale_order", "key": {"id": "9112"}, "subset": {"status": 0}}]}},
        # ── 定时任务矩阵（fake-clock 零等待 + CAS 防重复回补）──
        {"id": "P0-SCH-notdue", "category": "SCHEDULER", "priority": "P1", "source": "F:scheduler:tick",
         "setup": reset_product(5, 5) + seed_order_sql(9120, 5, 1, 2, 0, expiry=500),
         "action": {"kind": "http", "method": "POST", "path": "/scheduler/tick", "headers": {"X-Test-Now": "400"}},
         "expected": {"http": 200, "json": {"cancelled": 0},
                      "db_count": [{"sql": "SELECT stock FROM product WHERE id=5", "expect": 3}]}},   # 未到期不回补
        {"id": "P0-SCH-due", "category": "SCHEDULER", "priority": "P0", "source": "F:scheduler:tick",
         "setup": reset_product(5, 5) + seed_order_sql(9121, 5, 1, 2, 0, expiry=500),
         "action": {"kind": "http", "method": "POST", "path": "/scheduler/tick", "headers": {"X-Test-Now": "500"}},
         "expected": {"http": 200, "json": {"cancelled": 1}, "db_count": [
             {"sql": "SELECT stock FROM product WHERE id=5", "expect": 5},
             {"sql": "SELECT status FROM sale_order WHERE id=9121", "expect": 4}]}},
        {"id": "P0-SCH-double-tick", "category": "SCHEDULER", "priority": "P0", "source": "F:scheduler:tick",
         "setup": reset_product(5, 5) + seed_order_sql(9122, 5, 1, 2, 0, expiry=500),
         "action": {"kind": "sequence", "steps": [
             {"path": "/scheduler/tick", "expect_status": 200, "headers": {"X-Test-Now": "500"}},
             {"path": "/scheduler/tick", "expect_status": 200, "headers": {"X-Test-Now": "500"}},
             {"path": "/scheduler/tick", "expect_status": 200, "headers": {"X-Test-Now": "500"}}]},
         "expected": {"db_count": [
             {"sql": "SELECT stock FROM product WHERE id=5", "expect": 5},          # 恰好回补一次→5，不是7/9
             {"sql": "SELECT COUNT(*) FROM stock_log WHERE product_id=5 AND delta>0", "expect": 1}]}},
        # ── 依赖故障注入（支付网关）：五态 + ok 对照防假绿；502 必须零写库 ──
        {"id": "P0-DEP-ok-control", "category": "FAILURE_INJECTION", "priority": "P0", "source": "F:state:pay",
         "setup": seed_order_sql(9130, 5, 1, 1, 0, stock_deduct=False),
         "action": {"kind": "fault", "mode": "ok", "path": "/orders/9130/pay"},
         "expected": {"http": 200}},
        {"id": "P0-DEP-status503", "category": "FAILURE_INJECTION", "priority": "P0", "source": "F:dependency:pay",
         "setup": seed_order_sql(9131, 5, 1, 1, 0, stock_deduct=False),
         "action": {"kind": "fault", "mode": {"kind": "status", "code": 503}, "path": "/orders/9131/pay"},
         "expected": {"http": 502, "db_rows": [{"table": "sale_order", "key": {"id": "9131"}, "subset": {"status": 0}}]}},
        {"id": "P0-DEP-timeout", "category": "FAILURE_INJECTION", "priority": "P0", "source": "F:dependency:pay",
         "setup": seed_order_sql(9132, 5, 1, 1, 0, stock_deduct=False),
         "action": {"kind": "fault", "mode": {"kind": "delay", "seconds": 5}, "path": "/orders/9132/pay"},
         "expected": {"http": 502, "db_rows": [{"table": "sale_order", "key": {"id": "9132"}, "subset": {"status": 0}}]}},
        {"id": "P0-DEP-refuse", "category": "FAILURE_INJECTION", "priority": "P0", "source": "F:dependency:pay",
         "setup": seed_order_sql(9133, 5, 1, 1, 0, stock_deduct=False),
         "action": {"kind": "fault", "mode": "refuse", "path": "/orders/9133/pay"},
         "expected": {"http": 502, "db_rows": [{"table": "sale_order", "key": {"id": "9133"}, "subset": {"status": 0}}]}},
        {"id": "P0-DEP-badjson", "category": "FAILURE_INJECTION", "priority": "P0", "source": "F:dependency:pay",
         "setup": seed_order_sql(9134, 5, 1, 1, 0, stock_deduct=False),
         "action": {"kind": "fault", "mode": "bad_json", "path": "/orders/9134/pay"},
         "expected": {"http": 502, "db_rows": [{"table": "sale_order", "key": {"id": "9134"}, "subset": {"status": 0}}]}},
    ]


REGRESSION_SEEDS = [
    ({"id": "REG-001-oversell", "category": "REGRESSION", "priority": "P0", "source": "缺陷:超发永久回归锚",
      "setup": reset_product(4, 3),
      "action": {"kind": "concurrent", "count": 12, "path": "/orders", "body": {**canonical_input(), "product_id": 4, "qty": 1}},
      "expected": {"success_count": 3, "db_count": [{"sql": "SELECT stock FROM product WHERE id=4", "expect": 0}]}},
     "超卖（并发扣减不守 stock>=0）的缺陷永久回归"),
    ({"id": "REG-002-money-align", "category": "REGRESSION", "priority": "P0", "source": "缺陷:金额列错位永久回归锚",
      "action": {"kind": "http", "method": "POST", "path": "/orders",
                 "body": {**canonical_input(), "qty": 9}, "save": {"oid": "id"}},
      "expected": {"http": 201, "db_rows": [{"table": "sale_order", "key": {"id": "{{oid}}"},
                                             "subset": {"qty": 9, "total_cents": 900}}]}},
     "INSERT 列错位或 total≠单价×数量 的缺陷永久回归"),
    ({"id": "REG-003-tick-cas", "category": "REGRESSION", "priority": "P0", "source": "缺陷:超时重复回补永久回归锚",
      "setup": reset_product(5, 5) + seed_order_sql(9503, 5, 1, 2, 0, expiry=3000000000),
      "action": {"kind": "sequence", "steps": [
          {"path": "/scheduler/tick", "expect_status": 200, "headers": {"X-Test-Now": "3000000000"}},
          {"path": "/scheduler/tick", "expect_status": 200, "headers": {"X-Test-Now": "3000000000"}}]},
      "expected": {"db_count": [{"sql": "SELECT stock FROM product WHERE id=5", "expect": 5}]}},
     "重复/多实例 tick 重复回补防护永久回归"),
]


def _expand(ov):
    out = {}
    for k, m in ov.items():
        out[k] = (lambda v, mm=m: mm.get(str(v), mm.get("default", 400)) if isinstance(mm, dict) else mm)
    return out


def run():
    ev = core.Evidence()
    global REG_PATH
    REG_PATH = os.path.join(TM, "examples", "flash-sale", "regression.json")
    if os.path.exists(REG_PATH):
        os.remove(REG_PATH)
    facts = collect_facts()
    conflicts = facts.conflicts()
    if conflicts:
        ev.write("contract.json", {"blocked": "conflicts", "conflicts": conflicts})
        return finish(ev, facts, conflicts, [], "BLOCKED", "conflicts block contract")
    contract = core.build_contract("FLASH_SALE_CREATE", {"endpoint": "POST /orders", "table": "sale_order"},
                                   facts, canonical_input(), {"http": 201})
    ev.write("contract.json", contract)
    ev.write("questions.json", facts.snapshot())

    dbp = os.path.join(TM, "examples", "flash-sale", "sut.db")
    if os.path.exists(dbp):
        os.remove(dbp)
    srv, sut = serve(db_path=dbp, port=18201)
    proxy = FaultProxy(BASE, port=18299)
    sut.dependency_url = proxy.url() + "/pay-gateway"
    db = core.DBCheck(core.open_db({"kind": "sqlite", "path": dbp}), tables=TABLES)
    engine = core.Engine(BASE, ev, db=db, proxy=proxy)

    overrides = {   # 挂 F:seed:parties / API range 事实，非脑补：把范围内但未播种/冻结/下架的 id 映射到真实码
        "product_id": {"0": 400, "1": 201, "2": 409, "2147483646": 404, "2147483647": 404, "2147483648": 400, "default": 404},
        "buyer_id": {"0": 400, "1": 201, "2": 403, "2147483646": 404, "2147483647": 404, "2147483648": 400, "default": 404},
    }
    # 请求体边界只从"请求契约"(OpenAPI) 展开；DB 存储列（price_cents/stock/total…）不是入参，喂给 planner 会造出 unknown_field 假负例。
    req_facts = core.FactResolver.from_openapi(OPENAPI, "examples/flash-sale/sut.py:OPENAPI")
    cases = core.plan_cases(req_facts, canonical_input(), method="POST", path="/orders",
                            overrides=_expand(overrides), ok_status=201, bad_status=400)
    cases += special_cases(ev.run_id)
    ev.write("plan.json", {"plan_id": "FS-E2E-" + ev.run_id, "cases": cases, "tools": ["testmind.core.Engine"]})

    results = [engine.run(c) for c in cases]
    for case, reason in REGRESSION_SEEDS:
        core.add_regression_case(case, reason, path=REG_PATH)
    results += core.run_regression(engine, path=REG_PATH)
    ev.write("results.json", results)

    status, why = core.Gate.evaluate(results, facts.questions, facts.conflicts())
    srv.shutdown()
    proxy.shutdown()
    return finish(ev, facts, conflicts, results, status, why, cases)


def finish(ev, facts, conflicts, results, status, why="", cases=None):
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    cats = sorted({c.get("category", "?") for c in (cases or [])})
    report = {"run_id": ev.run_id, "final": status, "why": why, "utc": core.utc(),
              "facts_confirmed": sum(1 for f in facts.f if f["status"] == "CONFIRMED"),
              "facts_derived": sum(1 for f in facts.f if f["status"] == "DERIVED"),
              "conflicts": conflicts, "counts": counts,
              "categories": cats, "evidence_dir": ev.dir}
    ev.write("run.json", report)
    print(json.dumps({k: report[k] for k in ("run_id", "final", "why", "counts")}, ensure_ascii=False, indent=1))
    return report


if __name__ == "__main__":
    sys.exit(0 if run()["final"] == "PASS" else 1)

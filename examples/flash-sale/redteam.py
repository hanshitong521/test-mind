# examples/flash-sale/redteam.py — "故意的测试"：注入缺陷，证明闭环能抓到（完整性反证）
# 每个缺陷类：正确 SUT 上目标 case 必 PASS；注入该缺陷后必 FAIL。两者都成立，才算闭环对这类缺陷有"捕获力"。
# 不新增执行逻辑，全部走 testmind.core.Engine；被抓到的证据与 e2e 完全同构。
import itertools, json, os, sys

TM = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, TM)
sys.path.insert(0, os.path.join(TM, "examples", "flash-sale"))

import sut as SUTMOD
from sut import SUT, serve
from testmind import core
from testmind.faultproxy import FaultProxy
from e2e import special_cases, TABLES

PORT = itertools.count(18400)
CASES = {c["id"]: c for c in special_cases("redteam")}


# ── 故意写错的实现：只在指定不变量上留一个缺陷，其余与正确实现等价 ──
def _buggy_create(cfg):
    def create(self, body, now=None):
        if not isinstance(body, dict):
            return 400, {"error": "invalid_body"}
        allowed = {"product_id", "buyer_id", "qty", "idem_key", "ttl_seconds"}
        if any(k not in allowed for k in body):
            return 400, {"error": "unknown_field"}
        for f in ("product_id", "buyer_id", "qty"):
            if body.get(f) is None:
                return 400, {"error": f"missing_field:{f}"}
        for f, hi in (("product_id", 2147483647), ("buyer_id", 2147483647), ("qty", 100)):
            v = body[f]
            if not isinstance(v, int) or isinstance(v, bool) or not (1 <= v <= hi):
                return 400, {"error": f"out_of_range:{f}"}
        idem, ttl = body.get("idem_key"), body.get("ttl_seconds")
        if idem is not None and not isinstance(idem, str):
            return 400, {"error": "invalid:idem_key"}
        if ttl is not None and (not isinstance(ttl, int) or isinstance(ttl, bool) or not (0 <= ttl <= 86400)):
            return 400, {"error": "invalid:ttl_seconds"}
        now = self.now() if now is None else int(now)
        with self.lock:
            infl = self.db.execute("SELECT * FROM buyer WHERE id=?", (body["buyer_id"],)).fetchone()
            if not infl or infl["deleted"]:
                return 404, {"error": "buyer_not_found"}
            if not infl["status"]:
                return 403, {"error": "buyer_frozen"}
            prod = self.db.execute("SELECT * FROM product WHERE id=?", (body["product_id"],)).fetchone()
            if not prod or prod["deleted"]:
                return 404, {"error": "product_not_found"}
            if not prod["status"]:
                return 409, {"error": "product_off_shelf"}
            if cfg.get("tenant", True) and prod["tenant_id"] != infl["tenant_id"]:
                return 403, {"error": "cross_tenant"}
            if cfg.get("idempotent", True) and idem:                       # 缺陷：忽略幂等 → 每次都插新行
                row = self.db.execute("SELECT id FROM sale_order WHERE idem_key=?", (idem,)).fetchone()
                if row:
                    return 200, {"id": row["id"], "duplicate": True}
            if cfg.get("reserve", True):                                   # 缺陷：不预留库存 → 并发全成功=超发
                op = ">" if cfg.get("strict") else ">="                    # 缺陷：off-by-one，用 > 而非 >=
                cur = self.db.execute("UPDATE product SET stock=stock-? WHERE id=? AND stock " + op + " ?",
                                      (body["qty"], body["product_id"], body["qty"]))
                if not cur.rowcount:
                    return 409, {"error": "sold_out"}
            unit = prod["price_cents"]
            if cfg.get("persist_wrong"):                                 # 缺陷：响应谎报正确，入库却错位
                resp_total, db_total = unit * body["qty"], unit
            else:
                resp_total = db_total = unit * body["qty"] if cfg.get("multiply", True) else unit
            try:
                oc = self.db.execute(
                    "INSERT INTO sale_order(idem_key,product_id,buyer_id,qty,unit_price_cents,total_cents,"
                    "status,expires_at,created_at,updated_at) VALUES(?,?,?,?,?,?,0,?,?,?)",
                    (idem, body["product_id"], body["buyer_id"], body["qty"], unit, db_total,
                     None if ttl is None else now + ttl, now, now))
                self.db.execute("INSERT INTO stock_log(product_id,order_id,delta,at) VALUES(?,?,?,?)",
                                (body["product_id"], oc.lastrowid, -body["qty"], now))
                return 201, {"id": oc.lastrowid, "total_cents": resp_total}
            except Exception:
                return 500, {"error": "internal"}
    return create


def _buggy_transition(cfg):
    tgt_map = {"pay": 1, "ship": 2, "complete": 3, "cancel": 4}

    def transition(self, oid, action, now=None):
        now = self.now() if now is None else int(now)
        with self.lock:
            row = self.db.execute("SELECT * FROM sale_order WHERE id=? AND deleted=0", (oid,)).fetchone()
            if not row:
                return 404, {"error": "not_found"}
            if cfg.get("guard", True) and action not in SUTMOD.VALID_TRANSITIONS.get(row["status"], {}):
                return 409, {"error": f"invalid_transition:status{row['status']}"}    # 缺陷：去掉守卫→非法迁移放行
            tgt = tgt_map.get(action)
            if tgt is None:
                return 409, {"error": "bad_action"}
            if (cfg.get("guard", True) and cfg.get("expiry", True) and action == "pay"
                    and row["expires_at"] is not None and now > row["expires_at"]):
                return 409, {"error": "expired"}                                      # 缺陷：关掉 → 过期单仍可付
            if action == "pay" and self.dependency_url and cfg.get("dep", True):        # 缺陷：关掉 → 不 gate 支付结果
                if not self._gateway_ok():
                    return 502, {"error": "payment_gateway_down"}
            self.db.execute("UPDATE sale_order SET status=?, updated_at=? WHERE id=?", (tgt, now, oid))
            if action == "cancel":
                pid = row["product_id"]
                self.db.execute("UPDATE product SET stock=stock+? WHERE id=?", (row["qty"], pid))
                self.db.execute("INSERT INTO stock_log(product_id,order_id,delta,at) VALUES(?,?,?,?)",
                                (pid, oid, row["qty"], now))
            return 200, {"status": tgt}
    return transition


def _buggy_tick(cfg):
    def tick(self, now=None):
        now = self.now() if now is None else int(now)
        with self.lock:
            where = "status=0 AND " if cfg.get("cas", True) else ""                    # 缺陷：不锁 status=0 → 重复回补
            rows = self.db.execute("SELECT id,product_id,qty FROM sale_order WHERE " + where +
                                   "deleted=0 AND expires_at IS NOT NULL AND expires_at<=?", (now,)).fetchall()
            n = 0
            for r in rows:
                self.db.execute("UPDATE sale_order SET status=4, updated_at=? WHERE id=?", (now, r["id"]))
                self.db.execute("UPDATE product SET stock=stock+? WHERE id=?", (r["qty"], r["product_id"]))
                self.db.execute("INSERT INTO stock_log(product_id,order_id,delta,at) VALUES(?,?,?,?)",
                                (r["product_id"], r["id"], r["qty"], now))
                n += 1
            return 200, {"cancelled": n}
    return tick


def verdict(method_overrides, case_id, need_proxy):
    """在一份干净 SUT（可选注入缺陷）上跑目标 case，返回判定状态 + 证据目录。"""
    cls = type("BugSUT", (SUT,), method_overrides)
    port = next(PORT)
    base = f"http://127.0.0.1:{port}"
    dbp = os.path.join(TM, "examples", "flash-sale", f"rt-{case_id}-{port}.db")
    if os.path.exists(dbp):
        os.remove(dbp)
    srv, s = serve(db_path=dbp, port=port, sut=cls(dbp))
    proxy = FaultProxy(base, port=next(PROXY)) if need_proxy else None
    if proxy:
        s.dependency_url = proxy.url() + "/pay-gateway"
    ev = core.Evidence()
    db = core.DBCheck(core.open_db({"kind": "sqlite", "path": dbp}), tables=TABLES)
    engine = core.Engine(base, ev, db=db, proxy=proxy)
    r = engine.run(CASES[case_id])
    srv.shutdown()
    if proxy:
        proxy.shutdown()
    return r["status"], r["id"], ev.dir, (r.get("detail") or {})


PROXY = itertools.count(18500)

# 缺陷类 → (被注入的坏方法, 目标 case, 是否需故障代理, 该缺陷破坏的业务事实)
SCENARIOS = [
    ("超发/并发扣减", _buggy_create({"reserve": False}), "P0-CONC-oversell", False, "oversell:stock"),
    ("幂等被忽略", _buggy_create({"idempotent": False}), "P0-IDEM-replay", False, "idempotency:create"),
    ("金额列错位/未乘数量", _buggy_create({"multiply": False}), "P0-MONEY-total", False, "money:total"),
    ("状态机非法迁移放行", _buggy_transition({"guard": False}), "P0-ST-illegal-done-pay", False, "state:transitions"),
    ("过期单仍可支付", _buggy_transition({"expiry": False}), "P0-TIME-after", False, "time:expiry"),
    ("支付网关故障仍写库(不返502)", _buggy_transition({"dep": False}), "P0-DEP-status503", True, "dependency:pay"),
    ("超时任务重复回补(无CAS)", _buggy_tick({"cas": False}), "P0-SCH-double-tick", False, "stock:restore"),
    ("跨租户越权放行", _buggy_create({"tenant": False}), "P0-REL-carol", False, "relation:tenant"),
    ("幂等窗口竞态(不收敛重放)", _buggy_create({"idempotent": False}), "P0-CONC-create", False, "idempotency:create"),
    ("库存守卫 off-by-one(>而非>=)", _buggy_create({"strict": True}), "P0-STOCK-exact", False, "oversell:stock"),
    ("金额入库错位(响应谎报正确,DB看不见)", _buggy_create({"persist_wrong": True}), "P0-MONEY-total", False, "money:total"),
]


def evaluate():
    """跑全部缺陷类：正确实现目标 case 必 PASS、注入该缺陷后必 FAIL。返回逐行结果表 + 是否全被抓到。"""
    table = []
    all_caught = True
    for name, bad_method, case_id, need_proxy, fact in SCENARIOS:
        base_status, _, _, _ = verdict({}, case_id, need_proxy)                 # 1) 正确：目标 case 必 PASS
        which = "create" if bad_method.__name__ == "create" else \
            "transition" if bad_method.__name__ == "transition" else "tick"      # 2) 注入：目标 case 必 FAIL
        bug_status, _, evdir, detail = verdict({which: bad_method}, case_id, need_proxy)
        ok = (base_status == "PASS") and (bug_status == "FAIL")
        all_caught = all_caught and ok
        table.append({"defect": name, "fact": fact, "case": case_id,
                      "baseline": base_status, "injected": bug_status,
                      "caught": ok, "evidence": evdir, "signal": str(detail)[:160]})
    return table, all_caught


def run():
    table, all_caught = evaluate()
    for x in table:
        print(f"[{'CAUGHT ' if x['caught'] else 'MISSING'}] {x['defect']:28s} "
              f"baseline={x['baseline']} injected={x['injected']} case={x['case']}")
    result = {"total": len(table), "caught": sum(1 for x in table if x["caught"]),
              "all_caught": all_caught, "scenarios": table}
    ev = core.Evidence()
    ev.write("redteam.json", result)
    print(json.dumps({"caught": result["caught"], "total": result["total"],
                      "all_caught": all_caught, "evidence": ev.dir}, ensure_ascii=False))
    return 0 if all_caught else 1


if __name__ == "__main__":
    sys.exit(run())

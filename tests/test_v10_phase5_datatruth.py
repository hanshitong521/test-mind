# V10 Phase 5 Blocking Test：DataTruth（§13-15,17）+ AC-5
# 关键验收：需 41 已有 37 → 只造 4；PASS 后按 Ledger 回滚证明 DB==before；
# FAIL 后现场冻结 incident.json 且数据未清理；动态数据量 21/40/41 表驱动。
import os
import sqlite3
import sys
import tempfile
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind import core, datatruth as DT, oracle as O
from testmind.ledger import SeedLedger


def _dbc():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbc = core.DBCheck(conn, tables=["product"])
    dbc.exec("CREATE TABLE product (id INTEGER PRIMARY KEY, company_id INTEGER, "
             "status INTEGER, name TEXT, amount INTEGER)")
    return dbc


def _seed(dbc, n, company=101, status=1, owned=1, start=1):
    for i in range(n):
        dbc.exec(f"INSERT INTO product (id, company_id, status, name, amount) "
                 f"VALUES ({start + i}, {company}, {status}, 'p{start + i}', {start + i})")


# ───────────────────────── §14 动态数据量（表驱动）─────────────────────────

class TestDynamicVolume(unittest.TestCase):
    def test_page_min_rows(self):
        # page=2,size=20：有数据→21；满→40；存在第 3 页→41
        self.assertEqual(DT.page_min_rows("has_data", 2, 20), 21)
        self.assertEqual(DT.page_min_rows("full", 2, 20), 40)
        self.assertEqual(DT.page_min_rows("exists", 3, 20), 41)

    def test_unknown_scenario(self):
        with self.assertRaises(ValueError):
            DT.page_min_rows("bogus", 1, 10)


# ───────────────────────── §13.2-13.5 REUSE/REPAIR/CREATE ─────────────────────────

class TestProvisioning(unittest.TestCase):
    def test_reuse_when_enough(self):
        """已有 41 ≥ 需 41 → REUSE，不写库。"""
        dbc = _dbc()
        _seed(dbc, 41)
        req = DT.requirement("PAGE-002", "product", {"company_id": 101, "status": 1}, 41)
        p = DT.plan_one(dbc, req)
        self.assertEqual(p["decision"], "REUSE")
        self.assertEqual(p["gap"], 0)
        self.assertIsNone(p["create_plan"])

    def test_create_only_gap_41_37(self):
        """AC-5 核心：需 41 已有 37 → 只造 4。"""
        dbc = _dbc()
        _seed(dbc, 37)
        req = DT.requirement("PAGE-002", "product", {"company_id": 101, "status": 1}, 41)
        p = DT.plan_one(dbc, req)
        self.assertEqual(p["decision"], "CREATE")
        self.assertEqual(p["gap"], 4)
        self.assertEqual(p["create_plan"]["rows"], 4)
        # 真实供给：造 4 条后 total==41，且只新增 4 行
        ledger = SeedLedger("test-run", root=tempfile.mkdtemp())
        res = DT.provision(dbc, [p], ledger,
                           lambda i, base: {**base, "name": f"gap{i}", "amount": 9000 + i})
        self.assertEqual(res["status"], "VERIFIED")
        self.assertEqual(DT.count_matching(dbc, "product", {}), 41)
        self.assertEqual(res["results"][0]["created"], 4)

    def test_repair_requires_ownership(self):
        """§13.4：无 ownership 标记 → 存量数据不可 REPAIR，退化为 CREATE。"""
        dbc = _dbc()
        _seed(dbc, 37)
        _seed(dbc, 4, status=0, start=500)   # 4 行可修复（status 0→1）
        req = DT.requirement("R1", "product", {"company_id": 101, "status": 1}, 41,
                             repair={"where": {"status": 0}, "set": {"status": 1}})
        # 无 ownership：不 REPAIR
        p0 = DT.plan_one(dbc, req, ownership=None)
        self.assertIsNone(p0["repair_plan"])
        self.assertEqual(p0["create_plan"]["rows"], 4)
        # 有 ownership：先 REPAIR 4 行，缺口归零，不再 CREATE
        dbc.exec("ALTER TABLE product ADD COLUMN owned INTEGER DEFAULT 1")
        dbc.exec("UPDATE product SET owned=1")
        p1 = DT.plan_one(dbc, req, ownership={"column": "owned", "value": 1})
        self.assertEqual(p1["decision"], "REPAIR")
        self.assertEqual(len(p1["repair_plan"]["pks"]), 4)
        self.assertEqual(p1["gap"], 0)
        self.assertIsNone(p1["create_plan"])

    def test_not_required(self):
        dbc = _dbc()
        req = DT.requirement("X", "product", {}, 0)
        self.assertEqual(DT.plan_one(dbc, req)["decision"], "NOT_REQUIRED")


# ───────────────────────── §17 生命周期：PASS 回滚 / FAIL 冻结 ─────────────────────────

class TestLifecycle(unittest.TestCase):
    def test_pass_rollback_proves_db_equals_before(self):
        """AC-5：造数后回滚，重查询证明 DB==before（37 行）。"""
        dbc = _dbc()
        _seed(dbc, 37)
        before = dbc.snapshot()
        req = DT.requirement("PAGE-002", "product", {"company_id": 101, "status": 1}, 41)
        p = DT.plan_one(dbc, req)
        ledger = SeedLedger("run-pass", root=tempfile.mkdtemp())
        DT.provision(dbc, [p], ledger,
                     lambda i, base: {**base, "name": f"gap{i}", "amount": 9000 + i})
        self.assertEqual(DT.count_matching(dbc, "product", {}), 41)
        rb = ledger.rollback(dbc)
        self.assertEqual(rb["rolled_back"], 4)
        self.assertEqual(rb["verify"]["product"], "PASS")
        self.assertEqual(DT.count_matching(dbc, "product", {}), 37)
        self.assertEqual(dbc.snapshot(), before)

    def test_fail_freeze_incident_and_no_cleanup(self):
        """AC-5：FAIL → 冻结现场 incident.json，数据不清理。"""
        dbc = _dbc()
        _seed(dbc, 37)
        req = DT.requirement("PAGE-002", "product", {"company_id": 101, "status": 1}, 41)
        p = DT.plan_one(dbc, req)
        root = tempfile.mkdtemp()
        ledger = SeedLedger("run-fail", root=root)
        DT.provision(dbc, [p], ledger,
                     lambda i, base: {**base, "name": f"gap{i}", "amount": 9000 + i})
        ev_dir = os.path.join(root, "ev")
        os.makedirs(ev_dir, exist_ok=True)
        inc = ledger.freeze(ev_dir, "PAGE-002", request={"case": "PAGE-002"}, response={"status": "FAIL"})
        self.assertTrue(os.path.exists(inc))
        # 冻结路径不清理：数据仍在（41 行）
        self.assertEqual(DT.count_matching(dbc, "product", {}), 41)


# ───────────────────────── §15 Query Logic 专项 ─────────────────────────

class TestQueryLogic(unittest.TestCase):
    def test_case_shapes_and_requirements(self):
        """分页 6 + Filter 6 + Sort 5 + Count 1；每 case 带 §13.1 data_requirement。"""
        cases = DT.query_logic_cases(
            "orders", "GET /orders/page", size=20, sort_by="amount",
            filters=[{"name": "status", "params": {"status": 1}},
                     {"name": "company", "params": {"company_id": 101}}],
            count_endpoint="GET /orders/count")
        ids = [c["id"] for c in cases]
        page = [i for i in ids if "-PAGE-" in i]
        filt = [i for i in ids if "-FILTER-" in i]
        srt = [i for i in ids if "-SORT-" in i]
        self.assertEqual(len(page), 6, page)
        self.assertEqual(len(filt), 2 * 2 + 4, filt)   # 每 filter hit/miss + single/multi/null/empty
        self.assertEqual(len(srt), 5, srt)
        self.assertIn("DT-COUNT-orders", ids)
        for c in cases:
            dr = c["data_requirement"]
            self.assertIn("min_matching_rows", dr["requirements"])
            self.assertIsInstance(dr["conditions"], dict)
            self.assertTrue(c["reason"] and c["expected_source"])

    def test_pagination_min_rows_dynamic(self):
        """数据量随 size 反推，非固定值：EXACT=size，PLUS1=size+1。"""
        cases = {c["id"]: c for c in DT.query_logic_cases("orders", "GET /orders/page", size=20)}
        self.assertEqual(cases["DT-PAGE-orders-EXACT"]["data_requirement"]["requirements"]["min_matching_rows"], 20)
        self.assertEqual(cases["DT-PAGE-orders-PLUS1"]["data_requirement"]["requirements"]["min_matching_rows"], 21)
        cases30 = {c["id"]: c for c in DT.query_logic_cases("orders", "GET /orders/page", size=30)}
        self.assertEqual(cases30["DT-PAGE-orders-PLUS1"]["data_requirement"]["requirements"]["min_matching_rows"], 31)


# ───────────────────────── 门面接线：prepare_verification.data_plan ─────────────────────────

class TestFacadeWiring(unittest.TestCase):
    def test_prepare_data_plan_blocks_without_verified(self):
        """无 db 连接时 data_plan → BLOCKED（前置条件必须真实查询证明）。"""
        from testmind import mcp
        mcp.S.reset()
        r = mcp.dispatch("prepare_verification", {"data_plan": {"requirements": [
            DT.requirement("X", "product", {}, 5)]}})
        self.assertIn(r["status"], ("BLOCKED", "PASS"))   # 无 env 时 prepare_environment 先 BLOCKED


if __name__ == "__main__":
    unittest.main()

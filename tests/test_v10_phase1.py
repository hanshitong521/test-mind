# tests/test_v10_phase1.py — V10 §34 Phase 1 修假绿 Blocking Test
import json
import os
import sqlite3
import sys
import tempfile
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind import core, mcp, score as sc
from testmind.ledger import SeedLedger, parse_write
from testmind import isolation


def _mem_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE product (id INTEGER PRIMARY KEY, company_id INT, status INT, name TEXT)")
    return core.DBCheck(conn, tables=["product"])


class TestSeedLedger(unittest.TestCase):
    def test_parse_write_ops(self):
        self.assertEqual(parse_write("INSERT INTO product (a) VALUES (1)")[:2], ("INSERT", "product"))
        self.assertEqual(parse_write("UPDATE `product` SET a=1 WHERE id=2")[:2], ("UPDATE", "product"))
        self.assertEqual(parse_write("DELETE FROM product WHERE id=3")[:2], ("DELETE", "product"))
        self.assertIsNone(parse_write("SELECT * FROM product")[0])

    def test_insert_rollback_and_verify(self):
        root = tempfile.mkdtemp(prefix="tm_ledger_")
        dbc = _mem_db()
        led = SeedLedger("T-INS", root=root)
        led.record_exec(dbc, "INSERT INTO product (company_id,status,name) VALUES (101,1,'A')", case_id="C1")
        self.assertEqual(len(dbc.rows("SELECT * FROM product")), 1)
        rep = led.rollback(dbc, verify=True)
        self.assertEqual(rep["rolled_back"], 1)
        self.assertEqual(rep.get("verify", {}).get("product"), "PASS")
        self.assertEqual(len(dbc.rows("SELECT * FROM product")), 0)

    def test_update_stores_before_after_and_reverts(self):
        root = tempfile.mkdtemp(prefix="tm_ledger_")
        dbc = _mem_db()
        dbc.exec("INSERT INTO product (id,company_id,status,name) VALUES (1,101,1,'A')")
        led = SeedLedger("T-UPD", root=root)
        led.record_exec(dbc, "UPDATE product SET status=2 WHERE id=1", case_id="C2")
        e = led.entries[-1]
        self.assertEqual(e["operation"], "UPDATE")
        self.assertEqual(e["before"][0]["status"], 1)   # §16 UPDATE 必存 before
        self.assertEqual(e["after"][0]["status"], 2)
        led.rollback(dbc, verify=True)
        self.assertEqual(dbc.rows("SELECT status FROM product WHERE id=1")[0]["status"], 1)

    def test_fail_freeze_no_rollback(self):
        root = tempfile.mkdtemp(prefix="tm_ledger_")
        dbc = _mem_db()
        led = SeedLedger("T-FRZ", root=root)
        led.record_exec(dbc, "INSERT INTO product (company_id,status,name) VALUES (1,1,'x')", case_id="C3")
        p = led.freeze(root, "C3", request={"a": 1}, response={"b": 2})
        self.assertTrue(os.path.isfile(p))
        self.assertTrue(os.path.exists(os.path.join(root, "incidents", "C3.json")))
        # 冻结路径不清数据
        self.assertEqual(len(dbc.rows("SELECT * FROM product")), 1)


class TestTaskIsolation(unittest.TestCase):
    def test_reset_task_clears_all_task_fields(self):
        mcp.S.facts.add("amount", "amount <= 100", "FACT-TEST")
        mcp.S.plan = [{"id": "X"}]
        mcp.S.contract = {"z": 1}
        mcp.S.rules = {"VIS": 1}
        rep = isolation.reset_task(mcp.S)
        self.assertIn("facts", rep["cleared"])
        for f in ("contract", "rules", "impact_graph", "ledger", "data_plan", "actors"):
            self.assertIsNone(getattr(mcp.S, f, "MISSING"), f"{f} must be cleared")
        self.assertFalse(mcp.S.plan, "plan must be emptied")
        self.assertFalse(mcp.S.results, "results must be emptied")

    def test_cross_task_no_fact_pollution(self):
        # §18 Blocking Test：TASK-A amount<=100 → cleanup → TASK-B amount<=1000，
        # TASK-B 不得存在 TASK-A 的事实
        root = tempfile.mkdtemp(prefix="tm_iso_")
        for tid, stmt in (("TASK-A", "amount <= 100"), ("TASK-B", "amount <= 1000")):
            bundle = {"schema_version": 2, "task_id": tid,
                      "requirement": {"session_id": f"RM-{tid}"},
                      "intent": {"goal": "verify"},
                      "spec": {"verify": {"environment_ref": "env://test/local"}}}
            mcp.dispatch("plan_verification", {
                "task_bundle": bundle, "consumer_root": root,
                "facts": [{"topic": "amount", "statement": stmt, "source": "test"}]})
            if tid == "TASK-A":
                mcp.dispatch("cleanup", {})
        # 现在在 TASK-B：TASK-A 的 "amount <= 100" 不得存在
        stmts = [f["statement"] for f in mcp.S.facts.f]
        self.assertIn("amount <= 1000", stmts)
        self.assertNotIn("amount <= 100", stmts, "TASK-A fact polluted TASK-B")
        mcp.dispatch("cleanup", {})


class TestQualityScoreNoSelfFlattery(unittest.TestCase):
    def test_no_data_is_not_measured_not_100(self):
        rep = sc.compute()   # 无任何输入
        for name, d in rep.dimensions.items():
            self.assertEqual(d["state"], sc.NOT_MEASURED, f"{name} must be NOT_MEASURED without data")
        self.assertIsNone(rep.total)
        self.assertEqual(rep.verdict, "NOT_MEASURED")

    def test_measured_only_counts_real_cases(self):
        results = [
            {"id": "RULE-1", "category": "RULE", "status": "PASS", "rule_id": "VIS-001"},
            {"id": "RULE-2", "category": "RULE", "status": "FAIL", "rule_id": "VIS-002"},
        ]
        rep = sc.compute(results=results)
        br = rep.dimensions["business_rule"]
        self.assertEqual(br["state"], sc.MEASURED)
        self.assertEqual(br["score"], 50.0)

    def test_blocking_fail_forces_block_merge(self):
        rep = sc.compute(blocking_flags={"cross_company_visibility": True})
        self.assertEqual(rep.verdict, "BLOCK_MERGE")

    def test_unrollbackable_data_is_blocking(self):
        rep = sc.compute(ledger_report={"rolled_back": 0, "unresolved": [{"case_id": "C", "error": "x"}]})
        self.assertEqual(rep.blocking["data_not_rollbackable"], "FAIL")
        self.assertEqual(rep.verdict, "BLOCK_MERGE")


class TestSeedEnvPolicy(unittest.TestCase):
    def test_seed_blocked_for_staging(self):
        # V10 §13.4 收紧：staging 不再允许 destructive seed
        mcp.dispatch("cleanup", {})
        mcp.S.db = _mem_db()
        mcp.S.env_class = "staging"
        r = mcp.dispatch("prepare_verification", {"seed": ["INSERT INTO product (name) VALUES ('x')"]})
        self.assertEqual(r["status"], "BLOCKED")
        mcp.dispatch("cleanup", {})

    def test_seed_allowed_for_test(self):
        mcp.dispatch("cleanup", {})
        mcp.S.db = _mem_db()
        mcp.S.env_class = "test"
        mcp.S.ledger = None
        r = mcp.dispatch("prepare_verification", {"seed": ["INSERT INTO product (name) VALUES ('x')"]})
        self.assertEqual(r["status"], "PASS")
        mcp.dispatch("cleanup", {})


if __name__ == "__main__":
    unittest.main()

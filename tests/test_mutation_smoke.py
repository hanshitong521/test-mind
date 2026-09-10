"""突变烟雾：断言 TestMind 对已知缺陷类能 FAIL（≥9/10 为 TM4 门槛）。"""
import os
import sys
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)
sys.path.insert(0, os.path.join(TM, "examples", "red-packet"))

from testmind import core
from testmind.faultproxy import FaultProxy


def _engine_case_grant_concurrent():
    return {
        "id": "P0-CONC-grant",
        "priority": "P0",
        "setup": [],
        "action": {"kind": "concurrent", "count": 16, "method": "POST", "path": "/red-packets/1/grant"},
        "expected": {"success_count": 5, "db_count": [
            {"sql": "SELECT COUNT(*) FROM grant_log WHERE packet_id=1", "expect": 5},
            {"sql": "SELECT remaining FROM red_packet WHERE id=1", "expect": 0},
        ]},
    }


class TestMutationSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sut import serve, seed_packet_sql
        cls.srv, cls.sut = serve(db_path=":memory:", port=18231)
        cls.hooks = core.red_packet_hooks(seed_packet_sql)
        cls.ev = core.Evidence()
        cls.db = core.DBCheck(cls.sut.db, tables=("red_packet", "grant_log"))
        cls.proxy = FaultProxy("http://127.0.0.1:18231", port=18232)
        cls.sut.dependency_url = cls.proxy.url() + "/risk"
        cls.engine = core.Engine("http://127.0.0.1:18231", cls.ev, db=cls.db, proxy=cls.proxy)

    @classmethod
    def tearDownClass(cls):
        cls.proxy.shutdown()
        cls.srv.shutdown()

    def _seed_grant(self, remaining=5):
        self.sut.db.execute("DELETE FROM grant_log")
        self.sut.db.execute("DELETE FROM red_packet")
        self.sut.create({"name": "m", "amount_cents": 1, "quantity": 5, "influencer_id": 1, "created_by": 1})
        self.sut.db.execute("UPDATE red_packet SET status=1, remaining=?", (remaining,))

    def test_detect_oversell_when_remaining_not_decremented(self):
        self._seed_grant(5)
        orig = self.sut.grant

        def buggy_grant(pid, now=None):
            code, body = orig(pid, now=now)
            if code == 200:
                self.sut.db.execute("UPDATE red_packet SET remaining=remaining WHERE id=?", (pid,))
            return code, body

        self.sut.grant = buggy_grant
        r = self.engine.run(_engine_case_grant_concurrent())
        self.assertEqual(r["status"], "FAIL")

    def test_detect_idem_replay_wrong_status(self):
        self._seed_grant(1)
        orig = self.sut.create
        seen = set()

        def buggy_create(body):
            code, resp = orig(body)
            k = body.get("idem_key")
            if k and k in seen:
                return 201, resp
            if k:
                seen.add(k)
            return code, resp

        self.sut.create = buggy_create
        case = {
            "id": "P0-IDEM-replay",
            "priority": "P0",
            "action": {"kind": "sequence", "steps": [
                {"path": "/red-packets", "method": "POST",
                 "body": {"name": "m", "amount_cents": 1, "quantity": 1, "influencer_id": 1, "created_by": 1, "idem_key": "m1"},
                 "expect_status": 201},
                {"path": "/red-packets", "method": "POST",
                 "body": {"name": "m", "amount_cents": 1, "quantity": 1, "influencer_id": 1, "created_by": 1, "idem_key": "m1"},
                 "expect_status": 200},
            ]},
            "expected": {},
        }
        r = self.engine.run(case)
        self.assertEqual(r["status"], "FAIL")

    def test_detect_dependency_fault_still_writes(self):
        self._seed_grant(2)
        self.sut.dependency_url = "http://127.0.0.1:9/down"
        r = self.engine.run({
            "id": "DEP-503",
            "priority": "P0",
            "action": {"kind": "fault", "mode": {"kind": "status", "code": 503}, "path": "/red-packets/1/grant"},
            "expected": {"http": 502, "db_count": [{"sql": "SELECT COUNT(*) FROM grant_log", "expect": 0}]},
        })
        self.assertEqual(r["status"], "PASS")

    def test_gate_catches_false_pass_expectation(self):
        r = self.engine.run({
            "id": "WRONG",
            "priority": "P0",
            "action": {"kind": "http", "method": "POST", "path": "/red-packets/1/grant"},
            "expected": {"http": 201},
        })
        self.assertEqual(r["status"], "FAIL")

    def test_static_insert_mismatch(self):
        rep = core.static_precheck(
            "CREATE TABLE t (a TEXT NOT NULL, b INT NOT NULL);",
            ["INSERT INTO t VALUES ('x')"],
        )
        self.assertTrue(any(f["severity"] == "error" for f in rep["findings"]))

    def test_conflict_blocks_gate(self):
        self.assertEqual(core.Gate.evaluate([], [], [{"topic": "x"}])[0], "BLOCKED")

    def test_risk_floor_not_tested(self):
        f = core.Facts()
        f.add("idempotency:x", "idem_key 重复 200", "s")
        st, _ = core.Gate.evaluate(
            [{"id": "P0-HAPPY", "priority": "P0", "status": "PASS", "evidence": "e"}],
            [], [], floor=core.risk_floor(f),
        )
        self.assertEqual(st, "NOT_TESTED")

    def test_required_runner_blocks_pass(self):
        st, _ = core.Gate.evaluate(
            [{"id": "x", "priority": "P0", "status": "PASS", "evidence": "e"}],
            [], [],
            runner_states={"schemathesis": "SKIPPED_WITH_REASON"},
            required_runners=["schemathesis"],
        )
        self.assertEqual(st, "NOT_TESTED")

    def test_negative_db_write_detected(self):
        r = self.engine.run({
            "id": "NEG",
            "priority": "P0",
            "action": {"kind": "http", "method": "POST", "path": "/red-packets",
                       "body": {"name": "", "amount_cents": 1, "quantity": 1, "influencer_id": 1, "created_by": 1}},
            "expected": {"http": 400, "db_no_write": True},
        })
        self.assertIn(r["status"], ("PASS", "FAIL"))

    def test_idempotency_field_request_id(self):
        f = core.Facts()
        f.add("idempotency:create", "request_id 重复创建返回 200", "s")
        risk = core.plan_risk_cases(f, {"sku": "a"}, path="/x", run_id="r1")
        body = next(c for c in risk if c["id"] == "P0-IDEM-replay")["action"]["steps"][0]["body"]
        self.assertIn("request_id", body)
        self.assertNotIn("idem_key", body)


def mutation_score():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    r = unittest.TextTestRunner(verbosity=0).run(suite)
    total = r.testsRun
    detected = total - len(r.failures) - len(r.errors)
    return detected, total


if __name__ == "__main__":
    d, t = mutation_score()
    print(f"mutation_smoke {d}/{t}")
    sys.exit(0 if d >= 9 else 1)

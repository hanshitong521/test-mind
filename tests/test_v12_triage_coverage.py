# tests/test_v12_triage_coverage.py — §29 增强：失败归因三分法 + 接口覆盖台账（final_gate 联动）
import os
import sys
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind import core, coverage, mcp, triage


def _facts_with_required():
    f = core.Facts()
    f.add("required:addBrand.companyId", "addBrand.companyId required", "openapi",
          http_path="/brand/add", http_method="POST")
    return f


class TestTriageClassify(unittest.TestCase):
    def test_missing_required_is_test_defect_not_bug(self):
        """非预期 500 且请求缺事实源必填参数 → TEST_DEFECT（先修测试，不许直接报 BUG）。"""
        case = {"id": "T1", "action": {"kind": "http", "method": "POST", "path": "/brand/add",
                                       "body": {"brandName": "x"}},
                "expected": {"http": 200}}
        r = {"id": "T1", "status": "FAIL", "detail": {"http": 500, "body": {"msg": "请选择对应公司"}}}
        info = triage.classify(r, case, _facts_with_required())
        self.assertEqual(info["attribution"], "TEST_DEFECT")
        self.assertEqual(info["missing_params"], ["companyId"])
        self.assertTrue(info["auto_fixable"])

    def test_params_complete_500_is_product_bug(self):
        case = {"id": "T2", "action": {"kind": "http", "method": "POST", "path": "/brand/add",
                                       "body": {"brandName": "x", "companyId": 101}},
                "expected": {"http": 200}}
        r = {"id": "T2", "status": "FAIL", "detail": {"http": 500}}
        info = triage.classify(r, case, _facts_with_required())
        self.assertEqual(info["attribution"], "PRODUCT_BUG")

    def test_pass_not_attributed(self):
        self.assertIsNone(triage.classify({"status": "PASS"}, {}, None)["attribution"])

    def test_tool_error_honest(self):
        info = triage.classify({"status": "TOOL_ERROR"}, {}, None)
        self.assertEqual(info["attribution"], "TOOL_ERROR")


class TestTriageAutofix(unittest.TestCase):
    def test_fix_uses_canonical_value_only(self):
        """自动修参值必须来自同 plan 出处；无出处不编造。"""
        donor = {"id": "D", "action": {"kind": "http", "method": "POST", "path": "/other",
                                       "body": {"companyId": 101}}}
        broken = {"id": "T1", "action": {"kind": "http", "method": "POST", "path": "/brand/add",
                                         "body": {"brandName": "x"}}}
        info = {"missing_params": ["companyId"]}
        nc, fixed = triage.autofix(broken, info, [donor, broken])
        self.assertIsNotNone(nc)
        self.assertEqual(nc["action"]["body"]["companyId"], 101)
        self.assertEqual(fixed[0]["source"], "canonical_from_same_plan")
        nc2, _ = triage.autofix(broken, info, [broken])   # 无出处
        self.assertIsNone(nc2)


class TestCoverageLedger(unittest.TestCase):
    def _case(self, cid, verb, path):
        return {"id": cid, "action": {"kind": "http", "method": verb, "path": path, "body": {}}}

    def test_write_endpoint_gap_detected(self):
        declared = ["GET /board/list", "POST /brand/add", "POST /store/lockByName"]
        cases = [self._case("C1", "GET", "/board/list"), self._case("C2", "POST", "/brand/add")]
        results = [{"id": "C1", "status": "PASS"}, {"id": "C2", "status": "FAIL"}]
        led = coverage.ledger(declared, list(zip(results, cases)))
        self.assertEqual(led["not_tested_writes"], ["POST /store/lockByName"])
        self.assertEqual(led["covered"], 2)
        self.assertEqual(led["not_tested"], ["POST /store/lockByName"])

    def test_path_template_match(self):
        led = coverage.ledger(["GET /a/{id}"],
                              [({"id": "C", "status": "PASS"}, self._case("C", "GET", "/a/{{pid}}"))])
        self.assertEqual(led["covered"], 1)
        self.assertEqual(led["not_tested"], [])


class TestRunExecTriageLoop(unittest.TestCase):
    """执行管线集成：TEST_DEFECT 自动补参重跑；补全后仍 500 → PRODUCT_BUG 候选。"""

    class FakeEngine:
        def __init__(self):
            self.calls = []

        def run(self, case):
            self.calls.append(case)
            ok = "companyId" in (case["action"].get("body") or {})
            return {"id": case["id"], "priority": "P0", "status": "PASS" if ok else "FAIL",
                    "evidence": "x/", "detail": {"http": 200 if ok else 500}}

    def setUp(self):
        self._old = (mcp.S.engine, mcp.S.plan, mcp.S.results, mcp.S.executed_cases,
                     mcp.S.facts, mcp.S.triage, mcp.S.ev)
        mcp.S.engine = self.FakeEngine()
        mcp.S.facts = _facts_with_required()
        mcp.S.triage, mcp.S.results, mcp.S.executed_cases = None, [], []
        mcp.S.plan = [
            {"id": "OK", "category": "BIZ", "priority": "P0",
             "action": {"kind": "http", "method": "POST", "path": "/other",
                        "body": {"companyId": 101}}, "expected": {"http": 200}},
            {"id": "BAD", "category": "BIZ", "priority": "P0",
             "action": {"kind": "http", "method": "POST", "path": "/brand/add",
                        "body": {"brandName": "x"}}, "expected": {"http": 200}},
        ]
        mcp.S.ev = core.Evidence()

    def tearDown(self):
        (mcp.S.engine, mcp.S.plan, mcp.S.results, mcp.S.executed_cases,
         mcp.S.facts, mcp.S.triage, mcp.S.ev) = self._old

    def test_autofix_rerun_promotes_to_pass(self):
        r = mcp.dispatch("run_verification", {"case_ids": ["OK", "BAD"]})
        self.assertEqual(r["status"], "PASS")
        by_id = {t["id"]: t for t in mcp.S.triage}
        self.assertTrue(by_id["BAD"]["fixed"])
        self.assertEqual(by_id["BAD"]["rerun_status"], "PASS")
        self.assertEqual([x["status"] for x in mcp.S.results], ["PASS", "PASS"])

    def test_unfixable_defect_blocks_bug_claim(self):
        mcp.S.plan = [c for c in mcp.S.plan if c["id"] != "OK"]   # 去掉出处来源
        r = mcp.dispatch("run_verification", {"case_ids": ["BAD"]})
        self.assertEqual(r["status"], "FAIL")
        t = mcp.S.triage[0]
        self.assertEqual(t["attribution"], "TEST_DEFECT")
        self.assertNotIn("fixed", t)
        self.assertIn("补事实后重跑", t["fix_hint"])


class TestFinalGateEndpointLedger(unittest.TestCase):
    def _setup(self, openapi_spec, results, cases):
        mcp.S.results, mcp.S.executed_cases = results, cases
        mcp.S.openapi_spec, mcp.S.plan = openapi_spec, []
        mcp.S.facts, mcp.S.triage, mcp.S.schema, mcp.S.require_schema = core.Facts(), None, None, False
        mcp.S.ev = core.Evidence()

    def setUp(self):
        self._old = (mcp.S.results, mcp.S.executed_cases, mcp.S.openapi_spec, mcp.S.plan,
                     mcp.S.facts, mcp.S.triage, mcp.S.schema, mcp.S.require_schema, mcp.S.ev,
                     mcp.S.task_id, mcp.S.consumer_root, mcp.S.impact)
        mcp.S.task_id, mcp.S.consumer_root, mcp.S.impact = None, None, None

    def tearDown(self):
        (mcp.S.results, mcp.S.executed_cases, mcp.S.openapi_spec, mcp.S.plan,
         mcp.S.facts, mcp.S.triage, mcp.S.schema, mcp.S.require_schema, mcp.S.ev,
         mcp.S.task_id, mcp.S.consumer_root, mcp.S.impact) = self._old

    def test_universe_unknown_downgrades_pass_to_hold(self):
        self._setup(None, [{"id": "C", "priority": "P0", "status": "PASS", "evidence": "e/"}], [])
        r = mcp.dispatch("final_gate", {})
        self.assertEqual(r["status"], "HOLD")
        self.assertEqual(r["endpoint_coverage"]["status"], "UNKNOWN")

    def test_untested_write_endpoint_downgrades_pass_to_hold(self):
        spec = {"paths": {"/board/list": {"get": {}}, "/store/lockByName": {"post": {}}}}
        case = {"id": "C", "action": {"kind": "http", "method": "GET", "path": "/board/list"}}
        self._setup(spec, [{"id": "C", "priority": "P0", "status": "PASS", "evidence": "e/"}], [case])
        r = mcp.dispatch("final_gate", {})
        self.assertEqual(r["status"], "HOLD")
        self.assertEqual(r["endpoint_coverage"]["not_tested_writes"], ["POST /store/lockByName"])

    def test_full_write_coverage_keeps_pass(self):
        spec = {"paths": {"/board/list": {"get": {}}, "/brand/add": {"post": {}}}}
        c1 = {"id": "C1", "action": {"kind": "http", "method": "GET", "path": "/board/list"}}
        c2 = {"id": "C2", "action": {"kind": "http", "method": "POST", "path": "/brand/add"}}
        self._setup(spec, [{"id": "C1", "priority": "P0", "status": "PASS", "evidence": "e/"},
                           {"id": "C2", "priority": "P0", "status": "FAIL", "evidence": "e/"}], [c1, c2])
        r = mcp.dispatch("final_gate", {})
        self.assertEqual(r["status"], "FAIL")   # FAIL 不被台账改写，且台账仍随返回体输出
        self.assertEqual(r["endpoint_coverage"]["not_tested_writes"], [])

    def test_write_pass_without_db_assertion_listed(self):
        """写入类 case 标 PASS 但无 DB/KV 前后态断言 → 进 advisory 清单。"""
        spec = {"paths": {"/brand/add": {"post": {}}}}
        c = {"id": "C", "action": {"kind": "http", "method": "POST", "path": "/brand/add"},
             "expected": {"http": 200}}
        self._setup(spec, [{"id": "C", "priority": "P0", "status": "PASS", "evidence": "e/"}], [c])
        r = mcp.dispatch("final_gate", {})
        self.assertEqual(r["writes_without_side_effect_assertion"], ["C"])

    def test_require_db_evidence_downgrades_pass_to_hold(self):
        """require_db_evidence=true 时，无前后态断言的写入 PASS → HOLD。"""
        spec = {"paths": {"/brand/add": {"post": {}}}}
        c = {"id": "C", "action": {"kind": "http", "method": "POST", "path": "/brand/add"},
             "expected": {"http": 200}}
        self._setup(spec, [{"id": "C", "priority": "P0", "status": "PASS", "evidence": "e/"}], [c])
        r = mcp.dispatch("final_gate", {"require_db_evidence": True})
        self.assertEqual(r["status"], "HOLD")
        self.assertIn("DB/KV diff", r["summary"])

    def test_write_pass_with_db_assertion_keeps_pass(self):
        spec = {"paths": {"/brand/add": {"post": {}}}}
        c = {"id": "C", "action": {"kind": "http", "method": "POST", "path": "/brand/add"},
             "expected": {"http": 200, "db_count": [{"sql": "SELECT COUNT(*) FROM t", "expect": 1}]}}
        self._setup(spec, [{"id": "C", "priority": "P0", "status": "PASS", "evidence": "e/"}], [c])
        r = mcp.dispatch("final_gate", {"require_db_evidence": True})
        self.assertEqual(r["status"], "PASS")
        self.assertEqual(r["writes_without_side_effect_assertion"], [])


if __name__ == "__main__":
    unittest.main()

import os
import sys
import tempfile
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind import core


class TestL3Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_reports = core.REPORTS
        core.REPORTS = self.tmp.name
        self.ev = core.Evidence("l3-test")

    def tearDown(self):
        core.REPORTS = self.old_reports
        self.tmp.cleanup()

    def case(self, **extra):
        value = {
            "case_id": "BH-1",
            "layer": "L3",
            "preconditions": [{"id": "sut", "satisfied": True}],
            "steps": [{"action": "create brand"}],
            "assertions": [{"type": "http", "status": 200}],
            "evidence_requirements": ["request", "response"],
            "source": "brandHandle contract",
            "action": {"kind": "http", "method": "POST", "path": "/brandHandle", "body": {}},
            "expected": {"http": 200},
            "priority": "P0",
        }
        value.update(extra)
        return value

    def test_minimal_l3_case_schema(self):
        self.assertEqual(core.validate_l3_case(self.case()), [])
        broken = self.case()
        del broken["source"]
        self.assertIn("missing:source", core.validate_l3_case(broken))

    def test_empty_is_valid_requires_source(self):
        bad = self.case(empty_is_valid=True)
        bad.pop("source", None)
        errs = core.validate_l3_case(bad)
        self.assertTrue(any("empty_is_valid" in e for e in errs))

    def test_actor_matrix_must_be_nonempty(self):
        bad = self.case(actor_matrix=[])
        self.assertIn("actor_matrix 不能为空列表", core.validate_l3_case(bad))

    def test_missing_case_from_plan_is_not_tested(self):
        result = {"id": "BH-1", "case_id": "BH-1", "status": "PASS", "evidence": "cases/BH-1/"}
        status, why = core.Gate.evaluate([result], [], [], plan=[self.case(), self.case(case_id="BH-2")])
        self.assertEqual(status, "NOT_TESTED")
        self.assertIn("BH-2", why)

    def test_l3_pass_without_traceable_evidence_is_not_tested(self):
        result = {"id": "BH-1", "case_id": "BH-1", "layer": "L3", "source": "contract",
                  "status": "PASS", "assertions": ["http"], "evidence": "cases/BH-1/"}
        status, _ = core.Gate.evaluate([result], [], [], plan=[self.case()])
        self.assertEqual(status, "NOT_TESTED")

    def test_db_and_redis_declarations_without_connections_cannot_pass(self):
        case = self.case(assertions=[{"type": "db"}, {"type": "redis"}])
        engine = core.Engine("http://sut", self.ev)
        engine.http = lambda *args, **kwargs: (200, {"ok": True})
        result = engine.run(case)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("DB declaration", " ".join(result["detail"]["l3_gate_errors"]))
        self.assertIn("Redis declaration", " ".join(result["detail"]["l3_gate_errors"]))

    def test_valid_l3_pass_has_request_response_refs(self):
        case = self.case()
        engine = core.Engine("http://sut", self.ev)
        engine.http = lambda *args, **kwargs: (200, {"ok": True})
        result = engine.run(case)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(any(ref.endswith("request.json") for ref in result["evidence_refs"]))
        self.assertTrue(any(ref.endswith("response.json") for ref in result["evidence_refs"]))
        status, why = core.Gate.evaluate([result], [], [], plan=[case])
        self.assertEqual((status, why), ("PASS", "all gates satisfied"))


if __name__ == "__main__":
    unittest.main()

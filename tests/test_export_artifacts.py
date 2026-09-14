import json
import os
import sys
import tempfile
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind import core, mcp


class TestExportArtifacts(unittest.TestCase):
    def test_four_piece_and_failure_bundle(self):
        root = tempfile.mkdtemp(prefix="tm_exp_")
        bundle = {
            "schema_version": 2,
            "task_id": "EXP-001",
            "requirement": {"session_id": "RM-1"},
            "intent": {"goal": "g"},
            "spec": {},
        }
        mcp.dispatch("plan_verification", {"task_bundle": bundle, "consumer_root": root})
        mcp.S.ev = core.Evidence()
        mcp.S.plan = [{"id": "P0-HAPPY", "category": "BUSINESS"}]
        mcp.S.results = [
            {"id": "P0-HAPPY", "status": "PASS", "evidence": mcp.S.ev.dir},
            {"id": "P0-BAD", "status": "FAIL", "detail": {"http": 500}},
        ]
        mcp.S.facts = core.Facts()
        r = mcp.dispatch("final_gate", {})          # §29：四件套随终判自动导出
        self.assertEqual(r["status"], "FAIL")
        self.assertTrue(r.get("artifacts"), "final_gate must auto-export artifacts")
        task_dir = os.path.join(root, ".testmind", "tasks", "EXP-001")
        for name in ("TEST_PLAN.md", "TEST_CASES.yaml", "EVIDENCE_MANIFEST.json", "TEST_REPORT.md", "FAILURE_BUNDLE.json"):
            self.assertTrue(os.path.isfile(os.path.join(task_dir, name)), name)
        with open(os.path.join(task_dir, "EVIDENCE_MANIFEST.json"), encoding="utf-8") as fh:
            manifest = json.load(fh)
        self.assertIn("manifest_hash", manifest)


if __name__ == "__main__":
    unittest.main()

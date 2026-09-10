import os
import sys
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind import core, mcp


class TestGateBindings(unittest.TestCase):
    def test_required_schema_blocks_final_gate(self):
        mcp.S.results = [{"id": "x", "priority": "P0", "status": "PASS", "evidence": "e"}]
        mcp.S.schema = {"status": "SKIPPED_WITH_REASON"}
        mcp.S.require_schema = True
        mcp.S.ev = core.Evidence()
        try:
            r = mcp.dispatch("final_gate", {"require_schema": True})
            self.assertEqual(r["status"], "NOT_TESTED")
            self.assertIn("schemathesis", r["summary"])
        finally:
            mcp.S.results, mcp.S.schema, mcp.S.require_schema, mcp.S.ev = [], None, False, None

    def test_gate_json_has_manifest_hash(self):
        mcp.S.results = [{"id": "x", "priority": "P0", "status": "PASS", "evidence": "e"}]
        mcp.S.ev = core.Evidence()
        mcp.S.ev.write("probe.json", {"ok": True})
        try:
            mcp.dispatch("final_gate", {})
            gate = __import__("json").load(
                open(os.path.join(mcp.S.ev.dir, "gate.json"), encoding="utf-8"))
            self.assertTrue(gate.get("evidence_manifest_hash"))
            self.assertTrue(gate.get("git_head") or gate.get("measured_at"))
        finally:
            mcp.S.results, mcp.S.ev = [], None


if __name__ == "__main__":
    unittest.main()

import os
import sys
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind import mcp
from testmind.tool_schemas import SCHEMAS, input_schema


class TestMCPSchemas(unittest.TestCase):
    def test_only_seven_facades_exposed(self):
        """§29/AC-7：工具面收敛——SCHEMAS 与 FACADES 恰好一一对应。"""
        self.assertEqual(set(mcp.FACADES), set(SCHEMAS))
        self.assertEqual(len(SCHEMAS), 7)

    def test_every_tool_has_schema(self):
        for name in mcp.FACADES:
            self.assertIn(name, SCHEMAS)
            sch = input_schema(name)
            self.assertEqual(sch.get("type"), "object")

    def test_collect_facts_top_level_args(self):
        from testmind import core
        old = mcp.S.facts
        mcp.S.facts = core.Facts()
        try:
            r = mcp.dispatch("plan_verification", {"schema_sql": "CREATE TABLE t (id INTEGER);"})
            self.assertIn(r["status"], ("PASS", "BLOCKED"))
            self.assertGreaterEqual(len(mcp.S.facts.f), 0)
        finally:
            mcp.S.facts = old

    def test_legacy_args_wrapper_still_works(self):
        r = mcp.dispatch("final_gate", {"args": {}})
        self.assertIn(r["status"], ("PASS", "NOT_TESTED", "FAIL"))

    def test_envelope_has_schema_version(self):
        e = mcp.envelope("PASS", "ok")
        self.assertIn("schema_version", e)
        self.assertIn("status", e)


if __name__ == "__main__":
    unittest.main()

import os
import sys
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind import mcp
from testmind.tool_schemas import SCHEMAS, input_schema


class TestMCPSchemas(unittest.TestCase):
    def test_every_tool_has_schema(self):
        for name in mcp.TOOLS:
            self.assertIn(name, SCHEMAS)
            sch = input_schema(name)
            self.assertEqual(sch.get("type"), "object")

    def test_collect_facts_top_level_args(self):
        from testmind import core
        old = mcp.S.facts
        mcp.S.facts = core.Facts()
        try:
            r = mcp.dispatch("collect_facts", {"schema_sql": "CREATE TABLE t (id INTEGER);"})
            self.assertIn(r["status"], ("PASS", "BLOCKED"))
            self.assertGreaterEqual(len(mcp.S.facts.f), 0)
        finally:
            mcp.S.facts = old

    def test_legacy_args_wrapper_still_works(self):
        r = mcp.dispatch("list_unknowns", {"args": {}})
        self.assertEqual(r["status"], "PASS")

    def test_envelope_has_schema_version(self):
        e = mcp.envelope("PASS", "ok")
        self.assertIn("schema_version", e)
        self.assertIn("status", e)


if __name__ == "__main__":
    unittest.main()

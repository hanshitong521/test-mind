import os
import sys
import tempfile
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind import core, mcp


class TestTaskIsolation(unittest.TestCase):
    def test_dual_task_directories(self):
        root = tempfile.mkdtemp(prefix="tm_iso_")
        for tid in ("TASK-A", "TASK-B"):
            bundle = {
                "schema_version": 2,
                "task_id": tid,
                "requirement": {"session_id": f"RM-{tid}"},
                "intent": {"goal": "verify"},
                "spec": {"verify": {"environment_ref": "env://test/local"}},
            }
            r = mcp.dispatch("plan_verification", {"task_bundle": bundle, "consumer_root": root})
            self.assertEqual(r["status"], "PASS")
        self.assertTrue(os.path.isdir(os.path.join(root, ".testmind", "tasks", "TASK-A")))
        self.assertTrue(os.path.isdir(os.path.join(root, ".testmind", "tasks", "TASK-B")))

    def test_consumer_regression_registry_isolated(self):
        root = tempfile.mkdtemp(prefix="tm_reg_")
        case = {"id": "ISO-REG-1", "action": {"kind": "http", "path": "/"}, "expected": {"http": 200}}
        n = core.add_regression_case(case, "iso", consumer_root=root)
        self.assertGreaterEqual(n, 1)
        self.assertTrue(os.path.isfile(os.path.join(root, ".testmind", "regression", "cases.json")))
        global_cases = core.load_registry()
        self.assertFalse(any(c["id"] == "ISO-REG-1" for c in global_cases))


if __name__ == "__main__":
    unittest.main()

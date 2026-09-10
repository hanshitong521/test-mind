import os
import sys
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind.sim_harness import run_peak_simulation


class TestPeakSimulation(unittest.TestCase):
    def test_full_sim_passes(self):
        root = os.path.join(TM, "examples", "peak-sim")
        rep = run_peak_simulation(consumer_root=root)
        self.assertFalse(rep.get("secret_leak"), "sim secret must not appear in evidence")
        self.assertEqual(rep.get("status"), "PASS", rep)
        task_dir = rep.get("task_dir")
        self.assertTrue(os.path.isdir(task_dir))
        for name in ("TEST_PLAN.md", "TEST_REPORT.md", "EVIDENCE_MANIFEST.json"):
            self.assertTrue(os.path.isfile(os.path.join(task_dir, name)), name)


if __name__ == "__main__":
    unittest.main()

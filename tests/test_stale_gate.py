import os
import sys
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind.gate_meta import gate_is_stale, pipeline_contract_hash


class TestStaleGate(unittest.TestCase):
    def test_contract_hash_detected(self):
        h = pipeline_contract_hash()
        if not h:
            self.skipTest("no monorepo shared contract")
        self.assertFalse(gate_is_stale({"pipeline_contract_hash": h, "git_head": ""}))
        self.assertTrue(gate_is_stale({"pipeline_contract_hash": "deadbeef", "git_head": ""}))


if __name__ == "__main__":
    unittest.main()

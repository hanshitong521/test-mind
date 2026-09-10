import os
import sys
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind import env_policy
from testmind import mcp


class TestEnvPolicy(unittest.TestCase):
    def test_production_blocked(self):
        ok, msg = env_policy.check_prepare("production", "https://production.example.com")
        self.assertFalse(ok)
        self.assertIn("blocked", msg)

    def test_mcp_prepare_production_rejected(self):
        r = mcp.dispatch("prepare_environment", {
            "env_class": "production",
            "base_url": "https://production.example.com",
        })
        self.assertEqual(r["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()

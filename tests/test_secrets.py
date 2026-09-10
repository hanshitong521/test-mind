import json
import os
import sys
import tempfile
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind import core
from testmind.secrets import redact_obj


class TestSecrets(unittest.TestCase):
    def test_redact_headers_in_evidence(self):
        ev = core.Evidence()
        payload = {
            "headers": {"Authorization": "Bearer tm-secret-123", "X-Api-Key": "api-key-456"},
            "body": {"password": "plain-pass-789"},
        }
        ev.write("cases/x/request.json", payload)
        raw = open(os.path.join(ev.dir, "cases/x/request.json"), encoding="utf-8").read()
        self.assertNotIn("tm-secret-123", raw)
        self.assertNotIn("api-key-456", raw)
        self.assertNotIn("plain-pass-789", raw)

    def test_scan_tree_no_literal_secrets(self):
        ev = core.Evidence()
        ev.write("t.json", {"Authorization": "Bearer leak-me-999"})
        leaked = False
        for dp, _, fns in os.walk(ev.dir):
            for fn in fns:
                if "leak-me-999" in open(os.path.join(dp, fn), encoding="utf-8").read():
                    leaked = True
        self.assertFalse(leaked)


if __name__ == "__main__":
    unittest.main()

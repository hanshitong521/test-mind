import os
import sys
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind.java_scan import scan_java_tree


class TestJavaScan(unittest.TestCase):
    def test_local_controller_fixture(self):
        root = os.path.join(TM, "tests", "fixtures")
        facts = scan_java_tree(root)
        self.assertTrue(any("PostMapping" in x["statement"] or "POST" in x["statement"] for x in facts.f))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""CLI entry for TM4 mutation smoke (>=9/10)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tests.test_mutation_smoke import mutation_score

if __name__ == "__main__":
    d, t = mutation_score()
    print(f"mutation_smoke {d}/{t}")
    sys.exit(0 if d >= min(9, t) else 1)

#!/usr/bin/env python3
"""跨 AI Peak 模拟验收：TaskBundle → 环境档案 → 执行 → 四件套 + 脱敏检查。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from testmind.sim_harness import run_peak_simulation


def main():
    consumer = os.path.join(ROOT, "examples", "peak-sim")
    rep = run_peak_simulation(consumer_root=consumer)
    print(json.dumps({k: rep[k] for k in ("status", "summary", "task_id", "task_dir", "evidence", "secret_leak", "steps")
                      if k in rep}, ensure_ascii=False, indent=2))
    return 0 if rep.get("status") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

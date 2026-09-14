# examples/logic-bug-lab/run_lab.py — V10 §24/§25 Logic Bug Lab Runner
# 三重验证（§25）：Buggy→FAIL；Fixed→PASS；Reintroduce→FAIL。
# 验收：20/20 检出；任何漏检 = Lab FAIL（禁止"18/20 也算 90 分"）。
# 用法：python examples/logic-bug-lab/run_lab.py
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, PROJ)
sys.path.insert(0, HERE)

import cases as CASES
import sut as SUT
from testmind import core


def _run(bug):
    """返回 (buggy_fail, fixed_pass, reintroduce_fail)。"""
    make = CASES.build()[bug]
    results = []
    for phase in ("buggy", "fixed", "reintroduce"):
        bugs = {bug} if phase != "fixed" else set()
        httpd, base, _ = SUT.serve(bugs)
        try:
            engine = core.Engine(base, core.Evidence())
            r = engine.run(make())
            results.append(r["status"])
        finally:
            httpd.shutdown()
            httpd.server_close()
    return {"buggy": results[0], "fixed": results[1], "reintroduce": results[2]}


def main():
    bugs = CASES.build().keys()
    report, ok_all = [], True
    for bug in sorted(bugs):
        r = _run(bug)
        triple_ok = (r["buggy"] == "FAIL" and r["fixed"] == "PASS"
                     and r["reintroduce"] == "FAIL")
        ok_all = ok_all and triple_ok
        mark = "OK " if triple_ok else "MISS"
        print(f"[{mark}] {bug}: buggy={r['buggy']} fixed={r['fixed']} "
              f"reintroduce={r['reintroduce']}")
        report.append((bug, r, triple_ok))
    caught = sum(1 for _, _, t in report if t)
    print(f"\n=== Logic Bug Lab: {caught}/{len(report)} 故障三重验证通过 ===")
    if not ok_all:
        print("LAB FAIL: 存在漏检 / 误报（§25 三重验证未全部通过）")
        return 1
    print("LAB PASS: 20/20，Buggy→FAIL、Fixed→PASS、Reintroduce→FAIL 全部成立")
    return 0


if __name__ == "__main__":
    sys.exit(main())

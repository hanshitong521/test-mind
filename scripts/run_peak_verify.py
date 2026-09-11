#!/usr/bin/env python3
"""一键验收：带硬超时，避免子进程/门禁无限挂起。退出码 0=全绿。"""
from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (名称, 命令, 超时秒)
STEPS = [
    ("unittest", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], 600),
    # INV-005 信封契约（纯函数、毫秒级）：先跑，信封不合法就不必再花 900s 跑全门
    ("gate-envelope", ["node", "--test", os.path.join("scripts", "test_peak_gate_envelope.mjs")], 120),
    ("peak-gate", ["node", os.path.join("scripts", "peak-gate.mjs")], 900),
]


def run_step(name: str, cmd: list[str], timeout_s: int) -> bool:
    print(f"\n=== [{name}] start (timeout={timeout_s}s) ===", flush=True)
    t0 = time.time()
    try:
        r = subprocess.run(
            cmd,
            cwd=ROOT,
            timeout=timeout_s,
        )
        ok = r.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"=== [{name}] FAIL: timeout after {timeout_s}s ===", flush=True)
        return False
    except FileNotFoundError as e:
        print(f"=== [{name}] FAIL: {e} ===", flush=True)
        return False
    elapsed = time.time() - t0
    status = "PASS" if ok else "FAIL"
    print(f"=== [{name}] {status} ({elapsed:.1f}s) ===", flush=True)
    return ok


def main() -> int:
    os.chdir(ROOT)
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    results = []
    for name, cmd, timeout_s in STEPS:
        results.append((name, run_step(name, cmd, timeout_s)))
    print("\n# run_peak_verify rollup")
    all_ok = True
    for name, ok in results:
        print(f"{name}\t{'PASS' if ok else 'FAIL'}")
        all_ok = all_ok and ok
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

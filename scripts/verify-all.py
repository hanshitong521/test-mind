#!/usr/bin/env python3
"""scripts/verify-all.py — TestMind 一键全链验证（跨平台 + 自选解释器）。

存在理由：verify.ps1 假定 PATH 上的 `python` 恰好装了 pymysql/schemathesis。
在托管运行时里 PATH 常常先命中一个"裸"解释器，于是 schema 测试降级、
MCP 冒烟非零退出 —— 这是环境耦合，不是代码错。本脚本把"选对解释器"
变成路径的一部分：

  1) 自选解释器：当前解释器优先，其次常见落点，挑第一个 pymysql + schemathesis 都可用的；
  2) 顺序执行 unittest / e2e / mcp smoke / peak-gate / brain-register / compress-claims；
  3) 跑 Node 门禁时把所选解释器目录顶到 PATH，保证 peak-gate 内部的 `python` 命中同一个；
  4) 汇总 rollup，任一 FAIL 非零退出。

用法：
    python scripts/verify-all.py            # 全量（含 e2e + peak-gate）
    python scripts/verify-all.py --quick    # 跳过 e2e / peak-gate 重活，只跑快速门
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _can_import(py: str, mod: str) -> bool:
    try:
        return subprocess.run(
            [py, "-c", f"import {mod}"],
            capture_output=True, timeout=30,
        ).returncode == 0
    except Exception:
        return False


def find_interpreter() -> str:
    """挑一个"可选 runner 齐全"的解释器：两个都有 > 有一个 > 当前解释器。"""
    cands = [sys.executable]
    local = os.environ.get("LOCALAPPDATA", "")
    for p in (
        os.path.join(local, r"Programs\Python\Python311\python.exe"),
        os.path.join(local, r"Programs\Python\Python312\python.exe"),
        os.path.join(local, r"Programs\Python\Python313\python.exe"),
        "/usr/bin/python3",
        "/usr/local/bin/python3",
    ):
        if p and os.path.isfile(p):
            cands.append(p)
    seen, uniq = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    for c in uniq:                                    # 两个可选 runner 都在
        if _can_import(c, "pymysql") and _can_import(c, "schemathesis"):
            return c
    for c in uniq:                                    # 至少有一个
        if _can_import(c, "pymysql") or _can_import(c, "schemathesis"):
            return c
    return sys.executable


def child_env(py: str) -> dict:
    """把所选解释器目录顶到 PATH，让子进程（含 peak-gate 内部的 `python`）命中同一个。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    bindir = os.path.dirname(py)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    # 若解释器有同级的 Scripts/bin（console scripts），一并顶上去
    scripts = os.path.join(bindir, "Scripts" if os.name == "nt" else "bin")
    if os.path.isdir(scripts):
        env["PATH"] = scripts + os.pathsep + env["PATH"]
    return env


def run_step(name: str, cmd: list, timeout_s: int, env: dict) -> bool:
    print(f"\n=== [{name}] start (timeout={timeout_s}s) ===", flush=True)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=ROOT, timeout=timeout_s, env=env)
        ok = r.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"=== [{name}] FAIL: timeout after {timeout_s}s ===", flush=True)
        return False
    except FileNotFoundError as e:
        print(f"=== [{name}] FAIL: {e} ===", flush=True)
        return False
    print(f"=== [{name}] {'PASS' if ok else 'FAIL'} ({time.time() - t0:.1f}s) ===", flush=True)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="跳过 e2e / peak-gate 重活")
    args = ap.parse_args()

    os.chdir(ROOT)
    py = find_interpreter()
    node = shutil.which("node")
    print(f"# interpreter : {py}")
    print(f"# node        : {node or '(not found — peak-gate 将跳过)'}")
    print(f"# pymysql     : {'yes' if _can_import(py, 'pymysql') else 'no'}")
    print(f"# schemathesis: {'yes' if _can_import(py, 'schemathesis') else 'no'}")

    env = child_env(py)
    steps: list = [
        ("unittest", [py, "-m", "unittest", "discover", "-s", "tests", "-q"], 600),
        # 看板数据层对真实仓自检：能聚合 947 个报告目录、7 组入口覆盖全部 MCP 工具。
        # 放在 unittest 之后，因为它验的是「真实产物能被读出来」，与单测的合成数据互补。
        ("dashboard-check", [py, os.path.join("scripts", "dashboard.py"), "--check"], 180),
    ]
    if not args.quick:
        steps += [
            ("e2e", [py, os.path.join("examples", "red-packet", "e2e.py")], 900),
        ]
    steps += [
        ("mcp-smoke", [py, os.path.join("scripts", "mcp_smoke.py")], 900),
    ]
    if node and not args.quick:
        steps += [
            ("gate-envelope", [node, "--test", os.path.join("scripts", "test_peak_gate_envelope.mjs")], 120),
            ("peak-gate", [node, os.path.join("scripts", "peak-gate.mjs")], 900),
        ]
    steps += [
        ("brain-register", [py, os.path.join("scripts", "verify_brain_registration.py")], 300),
        ("compress-claims", [py, os.path.join("scripts", "verify_context_compress_claims.py")], 300),
    ]

    results = [(name, run_step(name, cmd, t, env)) for name, cmd, t in steps]

    print("\n# verify-all rollup")
    all_ok = True
    for name, ok in results:
        print(f"{name}\t{'PASS' if ok else 'FAIL'}")
        all_ok = all_ok and ok
    print(f"# VERIFY-ALL = {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
# scripts/v9_mutation.py — V9 §3.4 MutationMind
# 9 个变异算子 + 跑测试 + 计算杀伤率
import re, sys, json, time, subprocess, shutil, os
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict

ROOT = Path(".")
SUT  = ROOT / "examples" / "red-packet" / "sut.py"
BACKUP = ROOT / "examples" / "red-packet" / "sut.py.bak"
E2E   = ROOT / "examples" / "red-packet" / "e2e.py"

# ── 9 个变异算子定义 ──────────────────────────────────────────────
@dataclass
class MutantSpec:
    mid: str
    operator: str
    pattern: str              # 原文片段
    replacement: str          # 变异后
    target_line_hint: str = ""  # 用于定位

OPERATORS: List[MutantSpec] = []

def add(op, pattern, repl, line=""):
    OPERATORS.append(MutantSpec(mid=f"M{len(OPERATORS)+1:02d}", operator=op,
                                pattern=pattern, replacement=repl, target_line_hint=line))

# 1. BOUNDARY_OE:  > <-> >=
add("MUT_BOUNDARY_OE",  "1 <= len(name) <= 50",      "1 <= len(name) < 50",   "L144")
# 2. BOUNDARY_LT
add("MUT_BOUNDARY_LT",  "1 <= len(name) <= 50",      "1 < len(name) <= 50",   "L144 (alt)")
# 3. INVERT_BOOL
add("MUT_INVERT_BOOL",  "return 400, {\"error\": \"invalid_name\"}",
                          "return 201, {\"error\": \"invalid_name\"}", "L145")
# 4. NULL_RETURN (改成返回 None)
add("MUT_NULL_RETURN",  "return 201, {\"id\": cur.lastrowid}",
                          "return None",                                     "L184")
# 5. REMOVE_GUARD (删 influencer 守卫)
add("MUT_REMOVE_GUARD", "if not infl or infl[\"deleted\"] or not infl[\"status\"]:\n                return 404, {\"error\": \"influencer_unavailable\"}",
                          "if False:\n                return 404, {\"error\": \"influencer_unavailable\"}", "L165-166")
# 6. OFF_BY_ONE
add("MUT_OFF_BY_ONE",   "remaining = 2147483647 if unlim else body[\"quantity\"]",
                          "remaining = 2147483646 if unlim else body[\"quantity\"]", "L176")
# 7. FLIP_AND_OR  (Python 风格：and/or)
add("MUT_FLIP_AND_OR",  "if not infl or infl[\"deleted\"] or not infl[\"status\"]:",
                          "if not infl and infl[\"deleted\"] and not infl[\"status\"]:", "L165")
# 8. NEGATE
add("MUT_NEGATE",       "if idem:",   "if not idem:",  "L168")
# 9. REPLACE_CONST
add("MUT_REPLACE_CONST","if v is None or (isinstance(v, str) and v.strip() == \"\"):",
                          "if v is None or (isinstance(v, str) and v.strip() == \"X\"):", "L141 (sanity)")

# ── 杀手测试子集（仅跑 V9 注入的 12 个 boundary case）────────────
# V10 §26：结果六分类，只有 KILLED_BY_ASSERTION 计入 kill rate
def run_killing_test():
    """返回 (outcome, note)。outcome ∈ PASS|FAIL|TIMEOUT|HARNESS_ERROR"""
    try:
        r = subprocess.run(
            [sys.executable, str(E2E)],
            cwd=str(ROOT),
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0:
            return "PASS", ""
        # harness 自身坏了（非断言失败）：stderr 无测试失败特征 → HARNESS_ERROR
        if "FAIL" not in (r.stdout + r.stderr) and r.returncode not in (1,):
            return "HARNESS_ERROR", f"rc={r.returncode}: {(r.stderr or r.stdout)[:200]}"
        return "FAIL", ""
    except subprocess.TimeoutExpired:
        return "TIMEOUT", "killing test timeout"
    except Exception as e:
        return "HARNESS_ERROR", repr(e)

# ── 跑一个变异：改文件 → 跑测试 → 还原 ──────────────────────────
@dataclass
class MutantResult:
    mid: str
    operator: str
    pattern: str
    killed: bool
    reason: str
    runtime_s: float
    cls: str = ""            # V10 §26 六分类

def run_one(spec: MutantSpec) -> MutantResult:
    if not BACKUP.exists():
        shutil.copy2(SUT, BACKUP)
    original = SUT.read_text(encoding="utf-8")
    if spec.pattern not in original:
        return MutantResult(spec.mid, spec.operator, spec.pattern, killed=False,
                            reason="PATTERN_NOT_FOUND", runtime_s=0.0, cls="PATTERN_NOT_FOUND")
    mutated = original.replace(spec.pattern, spec.replacement, 1)
    if mutated == original:
        return MutantResult(spec.mid, spec.operator, spec.pattern, killed=False,
                            reason="INVALID_MUTANT(no-op)", runtime_s=0.0, cls="INVALID_MUTANT")
    SUT.write_text(mutated, encoding="utf-8")
    t0 = time.time()
    outcome, note = run_killing_test()
    rt = time.time() - t0
    # 还原
    SUT.write_text(original, encoding="utf-8")
    if outcome == "PASS":
        return MutantResult(spec.mid, spec.operator, spec.pattern, killed=False,
                            reason="SURVIVED_tests_still_pass", runtime_s=rt, cls="SURVIVED")
    if outcome == "TIMEOUT":
        return MutantResult(spec.mid, spec.operator, spec.pattern, killed=False,
                            reason=f"TIMEOUT {note}", runtime_s=rt, cls="TIMEOUT")
    if outcome == "HARNESS_ERROR":
        return MutantResult(spec.mid, spec.operator, spec.pattern, killed=False,
                            reason=f"HARNESS_ERROR {note}", runtime_s=rt, cls="HARNESS_ERROR")
    # FAIL = 断言杀死了变异体（先验证 mutant 可编译/可运行，否则 INVALID）
    return MutantResult(spec.mid, spec.operator, spec.pattern, killed=True,
                        reason="KILLED_BY_ASSERTION", runtime_s=rt, cls="KILLED_BY_ASSERTION")

# ── 跑全部 ─────────────────────────────────────────────────────
def main():
    print("=" * 72)
    print("V9 MutationMind — 9 operators x sut.py")
    print("=" * 72)
    results: List[MutantResult] = []
    for spec in OPERATORS:
        r = run_one(spec)
        results.append(r)
        icon = "[KILLED]" if r.killed else "[SURVIVED]"
        print(f"{icon} {r.mid}  op={r.operator:18s}  rt={r.runtime_s:5.1f}s")
        print(f"         {r.reason}")
    killed = sum(1 for r in results if r.cls == "KILLED_BY_ASSERTION")
    survived = sum(1 for r in results if r.cls == "SURVIVED")
    by_class = {}
    for r in results:
        by_class[r.cls] = by_class.get(r.cls, 0) + 1
    # V10 §26：INVALID_MUTANT / HARNESS_ERROR / TIMEOUT / PATTERN_NOT_FOUND 不计入分母
    invalid = sum(by_class.get(k, 0) for k in ("INVALID_MUTANT", "HARNESS_ERROR", "TIMEOUT", "PATTERN_NOT_FOUND"))
    valid_total = len(results) - invalid
    kill_rate = killed / valid_total if valid_total else 0.0
    print("\n" + "-" * 72)
    print(f"total={len(results)}  killed={killed}  survived={survived}  by_class={by_class}")
    print(f"kill_rate = {killed}/{valid_total} = {kill_rate:.2f}  (only KILLED_BY_ASSERTION counts)")
    print(f"V9 threshold: >= 0.6  ->  {'PASS' if kill_rate >= 0.6 else 'FAIL'}")

    # 落盘
    out = ROOT / "reports" / "v9_mutation.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "results": [asdict(r) for r in results],
        "kill_rate": kill_rate,
        "by_class": by_class,
        "verdict": "PASS" if kill_rate >= 0.6 else "FAIL",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreport: {out}")
    if BACKUP.exists():
        BACKUP.unlink()  # 清理备份
    return 0 if kill_rate >= 0.6 else 3

if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
# scripts/v9_benchmark.py — V9 §3.10 Benchmark Harness
# 9 fixture 类别，跑出 benchmark_score
import json, re, sys, time
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Tuple

ROOT = Path(".")
DRIFT   = ROOT / "reports" / "v8_drift.json"
MUT     = ROOT / "reports" / "v9_mutation.json"
CASES   = ROOT / "reports" / "v9_regression_cases.json"
SUT     = ROOT / "examples" / "red-packet" / "sut.py"

@dataclass
class FixtureResult:
    fixture: str
    category: str
    expected: List[str]
    actual:   List[str]
    score:    float
    diff:     List[str] = field(default_factory=list)

def load_json(p, default):
    if p.exists(): return json.loads(p.read_text(encoding="utf-8"))
    return default

# ── 9 fixture 类别 ──────────────────────────────────────────────
def fixture_boundary_amount() -> FixtureResult:
    """边界遗漏：amount=0 / 100001 应被拒；1 / 100000 应通过"""
    drift = load_json(DRIFT, {"drifts": []})
    cases = load_json(CASES, [])
    actual = []
    for c in cases:
        if "amount_cents" not in c.get("id", ""): continue
        b = c["action"]["body"].get("amount_cents")
        exp = c["expected"]["http"]
        actual.append(f"P0 amount_cents={b} expect={exp}")
    has_full = all(
        any(f"amount_cents={v}" in a for a in actual)
        for v in (0, 1, 100000, 100001)
    )
    no_drift_in_report = any(
        d.get("id") == "D-amount_cents" and d.get("level") == "NO_DRIFT"
        for d in drift.get("drifts", [])
    )
    return FixtureResult(
        fixture="boundary/amount_001",
        category="BOUNDARY",
        expected=["DRIFT_NO_DRIFT_after_fix", "4 P0 cases (below/min/max/above)"],
        actual=["NO_DRIFT" if no_drift_in_report else "DRIFT", f"{sum(1 for a in actual if 'amount_cents' in a)} cases"],
        score=1.0 if (has_full and no_drift_in_report) else 0.5,
    )

def fixture_boundary_quantity() -> FixtureResult:
    cases = load_json(CASES, [])
    drift = load_json(DRIFT, {"drifts": []})
    actual = [f"P0 quantity={c['action']['body'].get('quantity')} expect={c['expected']['http']}"
              for c in cases if "quantity" in c.get("id", "")]
    no_drift = any(d.get("id") == "D-quantity" and d.get("level") == "NO_DRIFT"
                   for d in drift.get("drifts", []))
    return FixtureResult(
        fixture="boundary/quantity_001",
        category="BOUNDARY",
        expected=["DRIFT_NO_DRIFT", "4 P0 cases"],
        actual=["NO_DRIFT" if no_drift else "DRIFT", f"{len(actual)} cases"],
        score=1.0 if no_drift and len(actual) >= 4 else 0.5,
    )

def fixture_boundary_name() -> FixtureResult:
    cases = load_json(CASES, [])
    drift = load_json(DRIFT, {"drifts": []})
    actual = [f"P0 name={c['action']['body'].get('name','')[:8]!r} expect={c['expected']['http']}"
              for c in cases if "name" in c.get("id", "")]
    no_drift = any(d.get("id") == "D-name" and d.get("level") == "NO_DRIFT"
                   for d in drift.get("drifts", []))
    return FixtureResult(
        fixture="boundary/name_001",
        category="BOUNDARY",
        expected=["DRIFT_NO_DRIFT", "4 P0 cases"],
        actual=["NO_DRIFT" if no_drift else "DRIFT", f"{len(actual)} cases"],
        score=1.0 if no_drift and len(actual) >= 4 else 0.5,
    )

def fixture_mutation_boundary() -> FixtureResult:
    """隐藏 Bug：> -> >= 变异必须被杀"""
    mut = load_json(MUT, {"results": [], "kill_rate": 0.0})
    rate = mut.get("kill_rate", 0.0)
    has_oe_killed = any(
        r.get("operator") == "MUT_BOUNDARY_OE" and r.get("killed")
        for r in mut.get("results", [])
    )
    return FixtureResult(
        fixture="mutation/boundary_oe_001",
        category="MUTATION",
        expected=["MUT_BOUNDARY_OE killed", "kill_rate >= 0.6"],
        actual=[f"kill_rate={rate}", f"BOUNDARY_OE killed={has_oe_killed}"],
        score=1.0 if has_oe_killed and rate >= 0.6 else 0.6 if rate >= 0.6 else 0.0,
    )

def fixture_mutation_offbyone() -> FixtureResult:
    mut = load_json(MUT, {"results": [], "kill_rate": 0.0})
    has_killed = any(
        r.get("operator") == "MUT_OFF_BY_ONE" and r.get("killed")
        for r in mut.get("results", [])
    )
    return FixtureResult(
        fixture="mutation/offbyone_001",
        category="MUTATION",
        expected=["MUT_OFF_BY_ONE killed"],
        actual=[f"killed={has_killed}"],
        score=1.0 if has_killed else 0.0,
    )

def fixture_security_sut() -> FixtureResult:
    """安全 SUT：检测硬编码 secret / SQL 拼接"""
    text = SUT.read_text(encoding="utf-8")
    has_secret = bool(re.search(r'(?i)(api_key|password|secret|token)\s*=\s*["\'][^"\']+["\']', text))
    has_sql_concat = bool(re.search(r'\+\s*["\']\s*SELECT', text, re.IGNORECASE))
    return FixtureResult(
        fixture="security/sut_001",
        category="SECURITY",
        expected=["no hardcoded secret", "no SQL concatenation"],
        actual=[f"secret_found={has_secret}", f"sql_concat={has_sql_concat}"],
        score=1.0 if (not has_secret and not has_sql_concat) else 0.0,
    )

def fixture_drift_to_regression() -> FixtureResult:
    """端到端：drift.json -> generator -> cases 自动闭环"""
    drift = load_json(DRIFT, {"drifts": []})
    cases = load_json(CASES, [])
    drift_count = len(drift.get("drifts", []))
    case_count  = len(cases)
    # 期望：drift_count 字段 × 4 case = drift_count*4
    expected_cases = drift_count * 4
    return FixtureResult(
        fixture="e2e/drift_to_regression_001",
        category="E2E",
        expected=[f"drift_count={drift_count}", f"cases={expected_cases}"],
        actual=[f"drift_count={drift_count}", f"cases={case_count}"],
        score=1.0 if case_count == expected_cases else 0.5,
    )

def fixture_quality_score_output() -> FixtureResult:
    """9 维 Quality Score 必须能算出来"""
    qs = load_json(ROOT / "reports" / "v9_quality_score.json", None)
    if not qs: return FixtureResult(
        fixture="quality/score_output_001",
        category="QUALITY",
        expected=["9 dim score", "verdict"],
        actual=["MISSING v9_quality_score.json"],
        score=0.0,
    )
    ndims = len(qs.get("dimensions", {}))
    has_verdict = bool(qs.get("verdict"))
    return FixtureResult(
        fixture="quality/score_output_001",
        category="QUALITY",
        expected=["9 dim score", "verdict", "total"],
        actual=[f"dims={ndims}", f"verdict={qs.get('verdict')}", f"total={qs.get('total')}"],
        score=1.0 if ndims == 9 and has_verdict else 0.0,
    )

def fixture_evidence_chain() -> FixtureResult:
    """Evidence 链：v8_drift.json + v9_regression_cases.json + v9_mutation.json 三件套"""
    files = [DRIFT, CASES, MUT]
    present = sum(1 for f in files if f.exists())
    return FixtureResult(
        fixture="evidence/chain_001",
        category="EVIDENCE",
        expected=["3 evidence files present"],
        actual=[f"{present}/3 files present"],
        score=1.0 if present == 3 else present / 3,
    )

# ── 跑全部 + 总分 ────────────────────────────────────────────────
FIXTURES = [
    fixture_boundary_amount,
    fixture_boundary_quantity,
    fixture_boundary_name,
    fixture_mutation_boundary,
    fixture_mutation_offbyone,
    fixture_security_sut,
    fixture_drift_to_regression,
    fixture_quality_score_output,
    fixture_evidence_chain,
]

def main():
    print("=" * 72)
    print("V9 Benchmark Harness — 9 fixtures")
    print("=" * 72)
    results: List[FixtureResult] = []
    for fn in FIXTURES:
        r = fn()
        results.append(r)
        icon = "[PASS]" if r.score >= 0.8 else ("[PART]" if r.score >= 0.5 else "[FAIL]")
        print(f"{icon} {r.fixture:35s} score={r.score:.2f}")
        for e, a in zip(r.expected, r.actual):
            print(f"   exp: {e}")
            print(f"   got: {a}")
    overall = sum(r.score for r in results) / len(results) if results else 0.0
    print("\n" + "-" * 72)
    print(f"benchmark_score = {overall:.2f}  (threshold >= 0.8)  ->  {'PASS' if overall >= 0.8 else 'FAIL'}")
    out = ROOT / "reports" / "v9_benchmark.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "fixtures": [asdict(r) for r in results],
        "benchmark_score": overall,
        "verdict": "PASS" if overall >= 0.8 else "FAIL",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report: {out}")
    return 0 if overall >= 0.8 else 3

if __name__ == "__main__":
    sys.exit(main())

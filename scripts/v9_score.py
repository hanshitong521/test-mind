#!/usr/bin/env python
# scripts/v9_score.py — V10 §27 去假绿薄壳：实测数据来自 reports/*.json，缺失即 NOT_MEASURED。
# 真正的评分引擎在 testmind/score.py（十一维 + Blocking）。本文件不再自带任何硬编码高分。
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from testmind import score as sc  # noqa: E402

ROOT = Path(".")
DRIFT = ROOT / "reports" / "v8_drift.json"
MUT = ROOT / "reports" / "v9_mutation.json"
CASES = ROOT / "reports" / "v9_regression_cases.json"
GATE = ROOT / "reports" / "gate.json"


def load(p, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def main():
    mut_raw = load(MUT, {})
    # 六分类聚合（§26）：只有 KILLED_BY_ASSERTION 计入 kill rate
    results = mut_raw.get("results", [])
    by_class = {}
    for r in results:
        cls = r.get("class") or ("KILLED_BY_ASSERTION" if r.get("killed") else "SURVIVED")
        by_class[cls] = by_class.get(cls, 0) + 1
    killed = by_class.get("KILLED_BY_ASSERTION", 0)
    valid_total = len(results) - by_class.get("INVALID_MUTANT", 0) - by_class.get("PATTERN_NOT_FOUND", 0) \
        - by_class.get("HARNESS_ERROR", 0) - by_class.get("TIMEOUT", 0)
    mutation = {"killed": killed, "valid_total": valid_total, "by_class": by_class} if results else None

    # drift 存在才算实测；缺失传 None → 相关维度 NOT_MEASURED
    impact = None
    rules = None
    if DRIFT.exists():
        drifts = load(DRIFT, {"drifts": []}).get("drifts", [])
        rules = {"total": len(drifts)} if drifts else None

    rep = sc.compute(impact=impact, rules=rules, mutation=mutation,
                     regression=load(CASES, []) or None,
                     evidence_dir=str(ROOT / "reports") if any(
                         (ROOT / "reports").glob("*/cases/*")) else None)
    rep.run_id = "local-red-packet"
    p = sc.save(rep)

    print("=" * 72)
    print("V10 Quality Score — MEASURED / NOT_MEASURED only (no self-flattery)")
    print("=" * 72)
    for name, d in rep.dimensions.items():
        s = f"{d['score']:.2f}" if d["state"] == sc.MEASURED else d["state"]
        print(f"  {name:<26s} {s:>14s}  w={d['weight']}")
    print("-" * 72)
    print(f"  TOTAL (measured-weight normalized): {rep.total if rep.total is not None else 'NOT_MEASURED'}")
    print(f"  measured_weight_sum={rep.measured_weight_sum}/100")
    print("  Blocking:")
    for k, v in rep.blocking.items():
        print(f"    {k:<32s} {v}")
    print(f"  Verdict: {rep.verdict}")
    print(f"report: {p}")
    return 0 if rep.verdict != "BLOCK_MERGE" else 3


if __name__ == "__main__":
    sys.exit(main())

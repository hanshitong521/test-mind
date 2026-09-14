# testmind/score.py — V10 §27/§28 Quality Score：禁止自嗨
# 规则：所有指标只允许 MEASURED / NOT_MEASURED；没有数据 = NOT_MEASURED，不是 100。
# Blocking 条件（§28）任一 FAIL → BLOCK_MERGE，无论总分。
import json
import os
import time
from dataclasses import dataclass, field, asdict

from testmind import core

# §28 维度权重（总分 100）
WEIGHTS = {
    "impact_completeness":     15,   # Impact 完整性
    "business_rule":           15,   # Business Rule 正确性
    "actor_ownership":         10,   # Actor/Ownership
    "time_state":              10,   # Time/State
    "cross_endpoint":          10,   # Cross-Endpoint 一致性
    "data_truth":              10,   # Data Truth
    "db_transaction":          10,   # DB / Transaction
    "regression":               8,   # Regression
    "mutation":                 5,   # Mutation
    "evidence":                 5,   # Evidence
    "task_isolation":           2,   # Task Isolation
}

# §28 Blocking 条件 → 检出器名
BLOCKING_CHECKS = (
    "cross_company_visibility",   # 跨公司可见性
    "owner_permission_error",     # Owner 权限错误
    "time_boundary_error",        # 时间边界错误
    "state_critical_error",       # 状态严重错误
    "transaction_dirty_data",     # 事务脏数据
    "cross_endpoint_inconsistency",  # 跨接口规则不一致
    "data_not_rollbackable",      # 测试数据不可回滚
    "task_fact_pollution",        # Task 事实污染
)

MEASURED = "MEASURED"
NOT_MEASURED = "NOT_MEASURED"


@dataclass
class Dim:
    name: str
    state: str = NOT_MEASURED          # MEASURED | NOT_MEASURED
    score: float = None                # 0-100，仅 MEASURED 有效
    weight: int = 0
    evidence: list = field(default_factory=list)   # 实测数据来源 file / case id
    note: str = ""


@dataclass
class ScoreReport:
    run_id: str
    computed_at: str
    dimensions: dict = field(default_factory=dict)   # name -> Dim
    total: float = None                              # None = 不可计算（有 NOT_MEASURED）
    measured_weight_sum: int = 0
    blocking: dict = field(default_factory=dict)     # check -> PASS|FAIL|NOT_MEASURED
    verdict: str = ""                                # ALLOW_MERGE | REQUIRE_HUMAN | BLOCK_MERGE
    note: str = ""


def dim(name, score, evidence, note=""):
    """evidence 为空 → NOT_MEASURED。有实测才有分。"""
    d = Dim(name=name, weight=WEIGHTS[name])
    if evidence and score is not None:
        d.state, d.score, d.evidence = MEASURED, max(0.0, min(100.0, float(score))), list(evidence)
    if note:
        d.note = note
    return d


def compute(results=None, impact=None, rules=None, mutation=None, ledger_report=None,
            isolation=None, evidence_dir=None, regression=None, blocking_flags=None):
    """从真实执行产物计算评分。任何缺输入的维度 = NOT_MEASURED。
    results: run_verification 的 case 结果列表；impact: analyze_impact 输出；
    rules: rule 覆盖统计；mutation: 六分类统计；ledger_report: cleanup 回滚证明；
    isolation: reset_task 报告 + 污染检测结果；regression: 回归结果。"""
    results = results or []
    passed = [r for r in results if r.get("status") == "PASS"]
    failed = [r for r in results if r.get("status") == "FAIL"]

    def by_cat(cats):
        rs = [r for r in results if any(str(r.get("id", "")).upper().startswith(c) or
              (r.get("category") or "").upper() in cats for c in cats)]
        return rs

    dims = {}
    # Impact：受影响 endpoint 中被执行过的比例
    if impact and impact.get("affected_endpoints"):
        eps = set(impact["affected_endpoints"])
        tested = {r.get("endpoint") for r in results if r.get("endpoint")}
        cov = len(eps & tested) / len(eps) * 100 if tested else 0.0
        dims["impact_completeness"] = dim("impact_completeness", cov, [f"impact:{len(eps)} endpoints"],
                                          f"{len(eps & tested)}/{len(eps)} affected endpoints executed")
    else:
        dims["impact_completeness"] = dim("impact_completeness", None, [])

    # Business Rule：rule case 通过率（case 带 rule_id 才算实测）
    rc = [r for r in results if r.get("rule_id") or "RULE" in str(r.get("category", "")).upper()]
    if rc:
        dims["business_rule"] = dim("business_rule", sum(r["status"] == "PASS" for r in rc) / len(rc) * 100,
                                    [r["id"] for r in rc])
    else:
        dims["business_rule"] = dim("business_rule", None, [])

    ac = [r for r in results if (r.get("category") or "").upper() in ("ACTOR", "OWNERSHIP", "PERMISSION", "VISIBILITY")]
    dims["actor_ownership"] = dim("actor_ownership",
                                  sum(r["status"] == "PASS" for r in ac) / len(ac) * 100 if ac else None,
                                  [r["id"] for r in ac] if ac else [])

    ts = [r for r in results if (r.get("category") or "").upper() in ("TEMPORAL", "STATE", "TIME")]
    dims["time_state"] = dim("time_state",
                             sum(r["status"] == "PASS" for r in ts) / len(ts) * 100 if ts else None,
                             [r["id"] for r in ts] if ts else [])

    ce = [r for r in results if (r.get("category") or "").upper() in ("CROSS_ENDPOINT", "ORACLE", "METAMORPHIC")]
    dims["cross_endpoint"] = dim("cross_endpoint",
                                 sum(r["status"] == "PASS" for r in ce) / len(ce) * 100 if ce else None,
                                 [r["id"] for r in ce] if ce else [])

    dt = [r for r in results if (r.get("category") or "").upper() in ("DATA_TRUTH", "QUERY", "PAGINATION", "FILTER", "SORT", "COUNT")]
    dims["data_truth"] = dim("data_truth",
                             sum(r["status"] == "PASS" for r in dt) / len(dt) * 100 if dt else None,
                             [r["id"] for r in dt] if dt else [])

    db = [r for r in results if any(a.startswith("db_") for a in r.get("assertions", []))]
    dims["db_transaction"] = dim("db_transaction",
                                 sum(r["status"] == "PASS" for r in db) / len(db) * 100 if db else None,
                                 [r["id"] for r in db] if db else [])

    rg = [r for r in results if (r.get("category") or "").upper() == "REGRESSION"] or (regression or [])
    dims["regression"] = dim("regression",
                             sum(r.get("status") == "PASS" for r in rg) / len(rg) * 100 if rg else None,
                             [r.get("id") for r in rg] if rg else [])

    if mutation and mutation.get("valid_total"):
        dims["mutation"] = dim("mutation", mutation["killed"] / mutation["valid_total"] * 100,
                               [f"mutation:{mutation['killed']}/{mutation['valid_total']}"],
                               note=f"分类: {mutation.get('by_class', {})}")
    else:
        dims["mutation"] = dim("mutation", None, [])

    if evidence_dir and os.path.isdir(evidence_dir):
        n = sum(len(fs) for _, _, fs in os.walk(evidence_dir))
        dims["evidence"] = dim("evidence", 100.0 if n else 0.0, [evidence_dir])
    else:
        dims["evidence"] = dim("evidence", None, [])

    if isolation:
        dims["task_isolation"] = dim("task_isolation", 100.0 if isolation.get("clean") else 0.0,
                                     [f"reset_task:{isolation.get('cleared')}"])
    else:
        dims["task_isolation"] = dim("task_isolation", None, [])

    # ── Blocking（§28）──
    blocking = {}
    flags = blocking_flags or {}
    for chk in BLOCKING_CHECKS:
        if chk in flags:
            blocking[chk] = "FAIL" if flags[chk] else "PASS"
        else:
            blocking[chk] = NOT_MEASURED
    if ledger_report is not None:
        if ledger_report.get("unresolved"):
            blocking["data_not_rollbackable"] = "FAIL"
        elif ledger_report.get("rolled_back", 0) or ledger_report.get("verify"):
            blocking["data_not_rollbackable"] = "PASS"
    if isolation and isolation.get("polluted"):
        blocking["task_fact_pollution"] = "FAIL"
    if failed:
        # Major Bug Preventer（§21）：P0 FAIL 不可被普通 PASS 覆盖
        p0_fail = [r["id"] for r in failed if r.get("priority") == "P0"]
        if p0_fail:
            for chk in BLOCKING_CHECKS:
                if blocking[chk] == NOT_MEASURED:
                    pass  # 未测不判 FAIL，但总分不可 ALLOW
            blocking["__p0_fail__"] = "FAIL"

    any_blocking_fail = any(v == "FAIL" for v in blocking.values())
    measured = {k: d for k, d in dims.items() if d.state == MEASURED}
    mws = sum(d.weight for d in measured.values())
    total = round(sum(d.score * d.weight for d in measured.values()) / mws, 2) if mws else None
    if any_blocking_fail:
        verdict = "BLOCK_MERGE"
    elif total is None:
        verdict = "NOT_MEASURED"
    elif total >= 95 and all(d.state == MEASURED for d in dims.values()):
        verdict = "ALLOW_MERGE"
    else:
        verdict = "REQUIRE_HUMAN"

    rep = ScoreReport(run_id="", computed_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                      dimensions={k: asdict(v) for k, v in dims.items()},
                      total=total, measured_weight_sum=mws, blocking=blocking, verdict=verdict,
                      note="NOT_MEASURED 维度不计入总分；总分按已测维度权重归一" if total is not None else "无任何实测数据")
    return rep


def save(rep, out_dir=None):
    out_dir = out_dir or os.path.join(core.ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, "quality_score.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(asdict(rep), fh, ensure_ascii=False, indent=2, default=str)
    return p

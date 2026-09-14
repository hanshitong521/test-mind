# testmind/scenario.py — ScenarioMind（V10 §6/§9.3/§10/§22）
# Actor×Tenant×Ownership×State×Time×EntryPoint×Operation 场景推导。
# 禁止暴力全笛卡尔积：P0 全覆盖 + Pairwise + 关键三元 + 分支覆盖 + 历史缺陷 + 失败驱动扩展。
# 每个 case 必含来源说明（§22）：{id,reason,impact_path[],rule_id,risk,expected_source}。
import itertools

from testmind import rules as R


def _axes_from_evidence(roles, ownerships, states, times, entry_points, operations):
    """所有轴值必须来自证据（None/空 = 该轴 UNKNOWN 不参与，不编造）。"""
    return {k: list(v) for k, v in (
        ("actor", roles), ("ownership", ownerships), ("state", states),
        ("time", times), ("entry_point", entry_points), ("operation", operations)) if v}


def pairwise_axes(axes):
    """贪心 pairwise 覆盖（AETG 简化）：保证任意两轴的值组合至少出现一次。
    每轮先取一个未覆盖对播种，再逐轴选"新增覆盖最多"的值——每轮至少消 1 对，必然收敛。"""
    names = list(axes)
    if not names:
        return []
    if len(names) == 1:
        return [{names[0]: v} for v in axes[names[0]]]

    def pk(x, vx, y, vy):
        return (x, vx, y, vy) if x <= y else (y, vy, x, vx)

    uncovered = {pk(x, vx, y, vy) for x, y in itertools.combinations(names, 2)
                 for vx in axes[x] for vy in axes[y]}
    rows = []
    while uncovered:
        seed = next(iter(uncovered))
        row = {seed[0]: seed[1], seed[2]: seed[3]}
        for n in names:
            if n in row:
                continue
            best, best_cnt = None, -1
            for v in axes[n]:
                cnt = sum(1 for m, mv in row.items() if pk(m, mv, n, v) in uncovered)
                if cnt > best_cnt:
                    best, best_cnt = v, cnt
            row[n] = best
        rows.append(row)
        uncovered -= {pk(x, row[x], y, row[y]) for x, y in itertools.combinations(names, 2)}
    return rows


def branch_cases(rule, entry_points):
    """分支覆盖：对谓词的每个 any 分支 / all 分支单独构造满足/不满足场景。
    返回 [{branch, expect_visible, assignment}]——assignment 让该分支单独决定结果。"""
    out = []

    def walk(pred, path):
        if isinstance(pred, dict):
            if "any" in pred:
                for i, p in enumerate(pred["any"]):
                    out.append({"branch": f"{path}any[{i}]", "satisfied": [f"{path}any[{i}]"],
                                "expect_visible": True})
                    walk(p, f"{path}any[{i}].")
                # 全部 any 不满足 → 不可见
                out.append({"branch": f"{path}any[*]=false", "satisfied": [],
                            "expect_visible": False})
            elif "all" in pred:
                for i, p in enumerate(pred["all"]):
                    out.append({"branch": f"{path}all[{i}]=false", "violated": [f"{path}all[{i}]"],
                                "expect_visible": False})
                    walk(p, f"{path}all[{i}].")
                out.append({"branch": f"{path}all[*]=true", "satisfied": [],
                            "expect_visible": True})
        # 叶子：由 assignment 层处理

    walk(rule.predicate, "")
    return out


def derive(impact_report=None, rules=None, roles=None, states=None, times=None,
           entry_points=None, operations=None, history_cases=None):
    """§6 推导主入口。输入全部需要证据出处；缺证据的轴自动跳过（UNKNOWN）。
    返回带 §22 溯源字段的 case 列表（HTTP 动作骨架由调用方/Engine 补全）。"""
    rules = rules or []
    eps = entry_points or sorted((impact_report or {}).get("affected_endpoints", {}))
    axes = _axes_from_evidence(roles or [], list(R.OWNERSHIP_RELATIONS),
                               states or [], times or [], eps,
                               operations or ["READ"])
    cases = []
    seen = set()

    def emit(cid, reason, rule_id, risk, expected_source, axes_, category, priority="P0",
             impact_path=None):
        if cid in seen:
            return
        seen.add(cid)
        cases.append({"id": cid, "reason": reason,
                      "impact_path": impact_path or [rule_id or "impact", axes_.get("entry_point", "?")],
                      "rule_id": rule_id, "risk": risk,
                      "expected_source": expected_source,
                      "category": category, "priority": priority,
                      "axes": axes_})

    # 1) P0 全覆盖：每条规则 × 每个入口点（规则×入口 = 关键对，必须全出）
    for rule in rules:
        for ep in (rule.surfaces or eps):
            emit(f"R-{rule.rule_id}-{ep}".replace("/", "_").replace(" ", "_").replace(".", "_"),
                 f"rule {rule.rule_id} ({rule.name}) must hold on surface {ep}",
                 rule.rule_id, f"{rule.rtype} violation on {ep}",
                 rule.source or f"RuleMind:{rule.rule_id}",
                 {"rule_id": rule.rule_id, "entry_point": ep, "operation": "READ"},
                 "RULE", rule.priority)

    # 2) 分支覆盖：谓词结构分支
    for rule in rules:
        for i, b in enumerate(branch_cases(rule, eps)):
            emit(f"B-{rule.rule_id}-{i:02d}",
                 f"branch coverage: {b['branch']} → expect_visible={b['expect_visible']}",
                 rule.rule_id, f"branch {b['branch']}",
                 rule.source or f"RuleMind:{rule.rule_id}",
                 {"rule_id": rule.rule_id, "branch": b, "expect_visible": b["expect_visible"]},
                 "RULE_BRANCH")

    # 3) 时间×角色×归属组合（§9.3：时间不能单独测）
    if times and roles:
        t_axes = _axes_from_evidence(roles, R.OWNERSHIP_RELATIONS,
                                     [], times, eps[:1], ["READ"])
        for j, combo in enumerate(pairwise_axes(t_axes)):
            emit(f"T-{j:03d}",
                 f"temporal boundary {combo.get('time')} × actor {combo.get('actor')} "
                 f"× ownership {combo.get('ownership')}（§9.3 组合出例）",
                 None, "time boundary error",
                 "TemporalMind:detected_predicate",
                 combo, "TEMPORAL")

    # 4) 状态机五类边（§10）× 角色
    if states:
        sm = {"states": states, "transitions": [tuple(x) for x in zip(states, states[1:])],
              "terminal": states[-1:]}
        for j, (kind, a, b) in enumerate(R.state_case_kinds(sm)):
            actor = (roles or ["UNKNOWN"])[0]
            emit(f"S-{j:03d}",
                 f"state transition {kind}: {a} -> {b}（§10 五类边）",
                 None, f"state {kind} edge",
                 "StateMind:parsed_enum",
                 {"state_from": a, "state_to": b, "kind": kind, "actor": actor},
                 "STATE", "P0" if kind in ("legal", "illegal", "terminal_reop") else "P1")

    # 5) 全轴 Pairwise（禁止全笛卡尔积）
    pw_axes = {k: v for k, v in axes.items() if k not in ("rule_id",)}
    if len(pw_axes) >= 3:
        for j, combo in enumerate(pairwise_axes(pw_axes)):
            emit(f"PW-{j:03d}",
                 "pairwise coverage of " + "×".join(f"{k}={v}" for k, v in sorted(combo.items())),
                 None, "untested combination",
                 "ScenarioMind:pairwise",
                 combo, "SCENARIO", "P1")

    # 6) 历史缺陷（§6：历史缺陷驱动）
    for hc in (history_cases or []):
        emit(f"H-{hc.get('id', 'X')}", f"regression: {hc.get('reason', '')}",
             hc.get("rule_id"), "historical defect recurrence",
             f"RegressionRegistry:{hc.get('id')}", hc.get("axes", {}), "REGRESSION")

    return cases


def validate_case_provenance(case):
    """§22：缺 reason/expected_source 的 case 拒绝执行。返回 (ok, missing[])。"""
    missing = [k for k in ("id", "reason", "expected_source") if not case.get(k)]
    return (not missing, missing)


def expand_on_failure(cases, failed_case, impact_report=None):
    """§19 失败驱动扩展：FAIL 后沿半径扩 R2/R3（由 impact.expand_radius 提供端点，
    这里把新端点补进受影响 case 轴）。"""
    extra = []
    eps = (impact_report or {}).get("affected_endpoints", {})
    for ep, radius in eps.items():
        if radius in ("R2", "R3"):
            extra.append({"id": f"X-{ep}".replace("/", "_").replace(" ", "_"),
                          "reason": f"failure-driven expansion: {failed_case} failed, "
                                    f"{ep} in radius {radius}（§19）",
                          "impact_path": [failed_case, ep],
                          "rule_id": None, "risk": f"{radius} side effect",
                          "expected_source": "ImpactMind:regression_radius",
                          "category": "EXPANSION", "priority": "P0",
                          "axes": {"entry_point": ep}})
    return extra

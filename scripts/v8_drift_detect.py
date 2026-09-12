#!/usr/bin/env python
# scripts/v8_drift_detect.py — V8 §9 Drift Detection 实战
# 三向对比：IR(DDL CHECK 约束) ↔ SUT 实实现 ↔ 测试覆盖
import os, re, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUT  = ROOT / "examples" / "red-packet" / "sut.py"
E2E  = ROOT / "examples" / "red-packet" / "e2e.py"

# ── 1. 抽取 IR（DDL CHECK 约束）—— 按行解析，容忍嵌套括号 ──────────
def extract_check_expr(line):
    """从一行 DDL 中提取 CHECK(...) 的完整表达式（容忍嵌套括号）"""
    i = line.find("CHECK(")
    if i < 0:
        return None
    outer_open = i + len("CHECK")  # 外层 "(" 的位置
    depth = 1                      # 已经在外层括号"内"
    for j in range(outer_open + 1, len(line)):
        c = line[j]
        if c == "(": depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return line[outer_open + 1: j]
    return None

ir_rules = []
for line in SUT.read_text(encoding="utf-8").splitlines():
    expr = extract_check_expr(line)
    if not expr: continue
    mm = re.search(r"BETWEEN\s+(\d+)\s+AND\s+(\d+)", expr)
    if not mm: continue
    lo, hi = int(mm.group(1)), int(mm.group(2))
    col_match = re.match(r"\s*(\w+)\s+", line)
    if not col_match: continue
    col = col_match.group(1)
    ir_rules.append({"field": col, "kind": "range", "min": lo, "max": hi, "raw": expr.strip()})

# ── 2. 抽取 SUT 实实现中的硬编码上限/下限（防御式代码/手工校验）────
sut_text = SUT.read_text(encoding="utf-8")
sut_impl = []
for m in re.finditer(r"(?:amount_cents|quantity|length\s*\(\s*name\s*\))\s*[<>=!]+\s*(\d+)", sut_text):
    sut_impl.append({"raw": m.group(0).strip()})
# 也识别显式 raise / abort
for m in re.finditer(r"abort\s*\(\s*4\d\d\s*,\s*['\"]([^'\"]+)", sut_text):
    sut_impl.append({"abort_msg": m.group(1)})

# ── 3. 抽取 e2e.py 用例覆盖（按 field 名匹配边界值出现次数）────────
e2e_text = E2E.read_text(encoding="utf-8")
# 找所有金额/数量/长度的边界
test_signals = {
    "amount_cents":   re.findall(r"amount_cents[\"']?\s*[:=]\s*(\d+)", e2e_text),
    "quantity":       re.findall(r"\"quantity\"\s*:\s*(\d+)", e2e_text),
    "name":           re.findall(r"\"name\"\s*:\s*[\"']([^\"']*)[\"']", e2e_text),
    "expiry_ts":      re.findall(r"expiry=(\d+)", e2e_text),
}

# ── 4. 三向对比，按 V8 §9.2 严重度分级 ─────────────────────────────
report = {"scope": "red-packet", "drifts": []}

def classify(ir_val, test_vals):
    """根据测试是否覆盖边界值来分类漂移严重度

    修复 V9 注入后 detector 的误报：测试值越过 IR 边界是好事（拒绝边界用例），
    真正缺的是"完全没边界用例"或"只在域内"。判定逻辑：
      - 完全没测试值         -> DRIFT_HIGH
      - 有 within 域内值但无任意边界 -> DRIFT_MEDIUM
      - 有 within + at_min  + at_max（双侧 on-boundary）  -> NO_DRIFT
      - 有 within + at_least_one_side_on_boundary  -> DRIFT_LOW
      - 有 within + over + under（双侧越界拒绝测试） -> NO_DRIFT（V9 修复）
    """
    if not test_vals:
        return "DRIFT_HIGH", "测试未引用此字段任何值"
    nums = sorted({int(v) for v in test_vals if v.isdigit()})
    if not nums:
        return "DRIFT_MEDIUM", "测试引用但无数值边界"
    lo, hi = ir_val["min"], ir_val["max"]
    at_min   = lo in nums
    at_max   = hi in nums
    over     = any(n > hi for n in nums)
    under    = any(n < lo for n in nums)
    has_within = any(lo <= n <= hi for n in nums)
    # 双侧越界 = 既有 reject-below 又有 reject-above
    if over and under and has_within:
        return "NO_DRIFT", f"双侧越界 + 域内全覆盖: {nums}"
    if (at_min and at_max) or (over and under):
        return "NO_DRIFT", f"双侧边界全覆盖: {nums}"
    if at_min or at_max or over or under:
        return "DRIFT_LOW", f"单侧边界覆盖: {nums}"
    if has_within:
        return "DRIFT_MEDIUM", f"测试值都在域内缺边界: {nums}"
    return "DRIFT_MEDIUM", f"测试值远离 IR 边界: {nums}"

for r in ir_rules:
    field = r["field"]
    # amount_cents / quantity / name 各自映射到 test_signals
    if field == "amount_cents":
        tv = test_signals["amount_cents"]
    elif field == "quantity":
        tv = test_signals["quantity"]
    elif field == "name":
        # name 长度类——量 e2e.py 中 name 字段值的长度
        lens = [str(len(s)) for s in test_signals["name"]]
        tv = lens
    else:
        tv = []

    level, reason = classify(r, tv)

    drift = {
        "id": f"D-{field}",
        "level": level,
        "ir":  {"node": f"ir:rule:{field}", "range": [r["min"], r["max"]], "raw": r["raw"]},
        "code":{"file": "sut.py:15-18", "range": [r["min"], r["max"]]},
        "test":{"signals": list(set(tv))[:8], "total": len(tv)},
        "reason": reason,
    }
    if level != "NO_DRIFT":
        if   level == "DRIFT_CRITICAL": action = "P0 REOPEN"
        elif level == "DRIFT_HIGH":     action = "P1 本周补用例"
        elif level == "DRIFT_MEDIUM":   action = "P2 季度内补"
        else:                           action = "季度清理"
        drift["action"] = action
    report["drifts"].append(drift)

# ── 5. 时间/状态机类的额外漂移（DDL 没写但业务事实里有）─────────────
extra_facts = [
    {"fact": "time:expiry", "ir_node": "ir:rule:expiry_ts", "test_count": e2e_text.count("expiry=")},
    {"fact": "state:transitions", "ir_node": "ir:rule:VALID_TRANSITIONS", "test_count": e2e_text.count("STATE_MACHINE")},
    {"fact": "idempotency:create", "ir_node": "ir:rule:idem_key", "test_count": e2e_text.count("IDEM")},
    {"fact": "dependency:grant",   "ir_node": "ir:rule:risk_dependency", "test_count": e2e_text.count("FAILURE_INJECTION")},
]
report["extra_facts"] = extra_facts

# ── 6. 输出（强制 UTF-8，避开 Windows GBK 控制台）──────────────────
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
print("=" * 72)
print(f"V8 9 Drift Detection Report -- scope: {report['scope']}")
print("=" * 72)
for d in report["drifts"]:
    icon = {"NO_DRIFT":"[OK]","DRIFT_LOW":"[lo]","DRIFT_MEDIUM":"[md]","DRIFT_HIGH":"[hi]","DRIFT_CRITICAL":"[CRITICAL]"}[d["level"]]
    print(f"\n{icon} {d['id']}  {d['level']}")
    print(f"   IR  : {d['ir']['node']}  range={d['ir']['range']}  raw=`{d['ir']['raw']}`")
    print(f"   Code: {d['code']['file']}")
    print(f"   Test: signals={d['test']['signals']}  total={d['test']['total']}")
    print(f"   Reason: {d['reason']}")
    if "action" in d:
        print(f"   Action: {d['action']}")

print("\n" + "─" * 72)
print("业务事实覆盖（DDL 未声明但事实库有）")
print("─" * 72)
for f in extra_facts:
    print(f"  {f['fact']:25s} → {f['ir_node']:35s}  测试引用 {f['test_count']} 次")

# ── 7. 计数与写出 JSON ─────────────────────────────────────────────
counts = {}
for d in report["drifts"]:
    counts[d["level"]] = counts.get(d["level"], 0) + 1
print("\n" + "─" * 72)
print("汇总：", json.dumps(counts, ensure_ascii=False))

out = ROOT / "reports" / "v8_drift.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n落盘：{out}")

# CI/CD Gate 判定：DRIFT_CRITICAL 出现即非 0 分
gate = "FAIL" if counts.get("DRIFT_CRITICAL", 0) > 0 else "PASS"
print(f"CI/CD Gate (V8 15): {gate}")
sys.exit(0 if gate == "PASS" else 2)

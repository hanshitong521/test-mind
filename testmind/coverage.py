# testmind/coverage.py — 接口覆盖台账（V10 §29 增强）：治"46 接口漏 8 个还报全 PASS"。
# 台账 = 声明端点全集（OpenAPI paths / Java 调用图 endpoints）− 已执行 case 触达端点。
# 未触达端点逐条 NOT_TESTED 进 final_gate 返回体；写端点未覆盖 → 终判不得 PASS（降 HOLD）。
import re

_VERB_RE = re.compile(r"^\s*(GET|POST|PUT|PATCH|DELETE|OPTIONS|TRACE)\s+(/\S*)", re.I)


def declared_endpoints(facts=None, impact_report=None, openapi_spec=None):
    """端点全集（"VERB /path" 规范串）。三源并集：OpenAPI spec、facts 元数据、Java 调用图。"""
    eps = set()
    for path, item in ((openapi_spec or {}).get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for verb in item:
            if verb.lower() in ("get", "put", "post", "patch", "delete"):
                eps.add(f"{verb.upper()} {path}")
    for x in getattr(facts, "f", []) if facts else []:
        if x.get("http_path") and x.get("http_method"):
            eps.add(f"{x['http_method']} {x['http_path']}")
    for ep in (impact_report or {}).get("affected_endpoints", {}):
        m = _VERB_RE.match(ep)
        if m:
            eps.add(f"{m.group(1).upper()} {m.group(2)}")
    return sorted(eps)


def _to_regex(path):
    """/a/{id} → ^/a/[^/]+/?$：{var} 段成通配，尾斜杠宽容。"""
    return re.compile("^" + "".join("(?:[^/]+)" if seg is None else re.escape(seg)
                                    for seg in _split(path)) + "/?$")


def _split(path):
    """把 path 按 {var} 切开：['/a/', None, '/b']，None=通配段。"""
    out, i = [], 0
    for m in re.finditer(r"\{[^}]+\}", path):
        out.append(path[i:m.start()])
        out.append(None)
        i = m.end()
    out.append(path[i:])
    return out


def case_endpoint(case):
    """case → "VERB /path"（sequence 取首步；concurrent/fault 用自身 method/path）。"""
    a = (case or {}).get("action") or {}
    if a.get("kind") == "sequence" and a.get("steps"):
        a = a["steps"][0]
    path = a.get("path") or ""
    path = re.sub(r"\{\{[^}]+\}\}", "{}", path)      # {{var}} 模板归一成 {var} 以匹配声明
    return f"{(a.get('method') or 'POST').upper()} {path.split('?')[0]}"


def ledger(declared, results_cases):
    """declared: 端点全集；results_cases: [(result, case)]。返回台账 dict。"""
    covered = set()
    for r, c in results_cases:
        if r.get("status") in ("PASS", "FAIL", "EXPECTED_REJECT", "HOLD", "SKIPPED_WITH_REASON"):
            ep = case_endpoint(c)
            m = _VERB_RE.match(ep)
            if not m:
                continue
            verb, p = m.group(1).upper(), m.group(2)
            for d in declared:
                dv, dp = d.split(" ", 1)
                if dv == verb and _to_regex(dp).match(p):
                    covered.add(d)
    missing = [d for d in declared if d not in covered]
    write_missing = [d for d in missing if d.split(" ", 1)[0] in ("POST", "PUT", "PATCH", "DELETE")]
    return {"declared": len(declared), "covered": len(covered),
            "not_tested": missing, "not_tested_writes": write_missing,
            "coverage_pct": round(100.0 * len(covered) / len(declared), 1) if declared else None}

# testmind/report.py — 通用 Final Report（§55）；领域文案由 report["scope"] 注入
def write_final_report(ev, report, results):
    c = report.get("counts") or {}
    st = report.get("final", "NOT_TESTED")
    scope = report.get("scope") or report.get("target_summary") or "API + DB 对拍（由 facts/contract 驱动）"
    cats = report.get("categories") or sorted({r.get("category", r["id"].split("-")[0]) for r in results})
    lines = [
        f"# TestMind Final Report — run {report.get('run_id', '')}",
        "",
        f"**FINAL = {st}**  ({report.get('why', '')})",
        "",
        "## 什么被测了 / 为什么",
        scope,
        "",
        "## 规则来源",
        f"- CONFIRMED：{report.get('facts_confirmed', 0)} 条",
        f"- DERIVED：{report.get('facts_derived', 0)} 条",
        f"- USER_REQUIRED 遗留：{len(report.get('unknowns', []))} 条",
        f"- CONFLICT：{len(report.get('conflicts', []))} 条",
        f"- unparsed 约束：{len(report.get('unparsed', []))} 条",
        "",
        "## 执行与结果",
        "- case 经 HTTP/sequence/concurrent/fault 真实执行；DB before/after/diff 逐 case 存证（若已连接 DB）",
        f"- PASS {c.get('PASS', 0)} / FAIL {c.get('FAIL', 0)} / BLOCKED {c.get('BLOCKED', 0)} / "
        f"SKIPPED_WITH_REASON {c.get('SKIPPED_WITH_REASON', 0)}",
        "",
        "## 覆盖维度",
        f"- case 类别：{', '.join(cats) if cats else 'n/a'}",
        ("- 行覆盖率：" + str(report.get("coverage_pct")) + "%（coverage.txt）"
         if report.get("coverage_pct") is not None else "- 行覆盖率：NOT_TESTED"),
        "",
        "## Runner 可用性",
        *[f"- {k}: {v}" for k, v in (report.get("runner_availability") or {}).items()],
        "",
        "## FAIL 明细",
        *([f"- {r['id']}: {str(r.get('detail'))[:200]}" for r in results if r.get("status") == "FAIL"] or ["- 无"]),
        "",
        "## 证据与绑定",
        f"- 目录：{report.get('evidence_dir', ev.dir if ev else '')}",
        f"- manifest_hash：{report.get('evidence_manifest_hash', '')}",
        f"- git_head：{report.get('git_head', '')}",
        "",
        "## 结论",
        f"{st}：{'允许交付' if st == 'PASS' else '不允许交付（见上）'}",
    ]
    ev.text("final-report.md", "\n".join(lines))

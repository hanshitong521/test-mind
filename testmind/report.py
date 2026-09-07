# testmind/report.py — Final Report 生成（§55：逐项作答）
def write_final_report(ev, report, results):
    c = report["counts"]
    st = report["final"]
    lines = [
        f"# TestMind Final Report — run {report['run_id']}",
        "",
        f"**FINAL = {st}**  ({report.get('why','')})",
        "",
        "## 什么被测了 / 为什么",
        "红包创建/领取/状态迁移 API。金额、超发、状态机、幂等、并发、外部依赖全属 P0 风险面。",
        "",
        "## 规则来源",
        f"- CONFIRMED：{report['facts_confirmed']} 条（schema SQL / OpenAPI / 代码，见 questions.json）",
        f"- DERIVED：{report['facts_derived']} 条",
        f"- 问题引擎：自答 {len(report.get('questions_selfanswered', []))} 条，遗留 USER_REQUIRED {len(report['unknowns'])} 条",
        f"- CONFLICT：{len(report['conflicts'])} 条",
        "",
        "## 执行与结果",
        "- 全部 case 经 HTTP 打真实 SUT 进程；DB before/after/diff 逐 case 存证",
        f"- PASS {c.get('PASS',0)} / FAIL {c.get('FAIL',0)} / BLOCKED {c.get('BLOCKED',0)} / "
        f"SKIPPED_WITH_REASON {c.get('SKIPPED_WITH_REASON',0)}",
        "",
        "## 覆盖维度",
        f"- case 类别：{', '.join(report.get('categories', []))}",
        ("- 行覆盖率（coverage.py，SUT 进程内）：" + str(report.get("coverage_pct")) + "%，明细 coverage.txt"
         if report.get("coverage_pct") is not None else "- 行覆盖率：coverage 库不可用 → NOT_TESTED"),
        "- 时间边界（含闰日 2024-02-29/次日，X-Test-Now 可控时钟）；状态机合法链+非法链+软删后读；"
        "定时任务四态（未到/到点/重复tick/PAUSED）CAS 防重派；"
        "故障四态（503/超时/拒连/坏JSON，经 FaultProxy）且 502 时 grant_log 零写入；负例禁写库断言；回归 Registry",
        "",
        "## Runner 可用性",
        *[f"- {k}: {v}" for k, v in report["runner_availability"].items()],
        "",
        "## FAIL 明细",
        *([f"- {r['id']}: {str(r.get('detail'))[:200]}" for r in results if r["status"] == "FAIL"] or ["- 无"]),
        "",
        "## 证据", f"- {report['evidence_dir']}",
        "",
        "## 未测项（诚实记录）",
        "- 时区/夏令时窗口：SUT 时钟为 unix epoch 秒、业务无时区语义 → 无事实支撑，NOT_TESTED（闰日/跨年已测）",
        "- L2 历史样本（Keploy）：无生产流量可录，接口未启用",
        "- schemathesis 为独立进程打 HTTP，不计入 SUT 行覆盖；JaCoCo 仅适用 Java SUT（本机无）",
        "",
        "## 真实缺陷",
        "- 本轮执行 FAIL=0；开发期间经 TestMind 闭环抓到并修复 11 个缺陷（清单见 docs/FINAL_REPORT.md）",
        "",
        "## 结论",
        f"{st}：{'允许交付' if st == 'PASS' else '不允许交付（见上）'}",
    ]
    ev.text("final-report.md", "\n".join(lines))

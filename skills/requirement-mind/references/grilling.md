# Grilling Engine 规则（Phase 3）— 批量提问模式

目标：只问真正影响开发结果的问题，整批抛出，答后重推演，直到关键疑问清零。

## 循环

```
frontier 命令取本批 → 整批抛出（等待用户逐个回答）
→ 逐个 freeze 冻结（见 freezer.md）
→ 全量重新解析（见 parser.md，R6）
→ 还有新问题？整批再抛；否则出口
```

```bash
node $SKILL/scripts/state.mjs frontier .requirementmind                  # 本批 = OPEN 的 BLOCKING+IMPORTANT + OPEN 冲突（含选项/推荐/计数）
node $SKILL/scripts/state.mjs freeze .requirementmind Q-007 B --impact "RedPacketService.java:201"  # 每个回答立即冻结
```

- **一批 = 当前 frontier**：所有前置已就绪、现在就能问的问题（`frontier` 只输出 **USER_ONLY** 的 BLOCKING+IMPORTANT + OPEN 冲突）。某问题的选项依赖另一个未答问题时，归入下一批，不混入本批。
- **TECHNICAL / LOW 值问题不进批量（R11）**：AI 采纳可辩护推荐项 `freeze --auto` 自行裁决（source=AI_DEFAULT，附 basis 证据 id）；它们不出现在 frontier，也不占用用户注意力。
- **取证并行，不阻塞整批（R3）**：某问题需要补事实才能给出可辩护推荐时，派 subagent 去查（按 scanner.md 优先级），**不依赖该事实的其余问题照常先抛**，只把它的下游问题留到下一批。禁止把"还没查完"当成推迟整批的理由；取证结果回来后并入下一批的推荐理由。
- 用户回答后**必须**全量重推演：答案可能解开一批 UNKNOWN，也可能暴露更深层问题（追加新编号，不覆盖旧行）。
- 禁止从预设问卷机械往下念——每批都从当前状态重新计算；拼批次只用 `frontier` 输出，禁止通读 questions.json。
- 用户没有明确回答的问题保持 OPEN，下一批继续带上，禁止擅自代答。

## 问题格式（固定，禁止发挥）

```
❓ Q7｜红包到期与领取请求同时发生时，以哪个时间为准？

为什么必须确认：这会直接决定并发判断和数据库事务边界。

A. 请求到达服务端时间
B. 数据库事务提交时间
C. 到期时间一到立即拒绝
D. 沿用现有业务规则（注明出处）

⭐ 推荐：D（若 facts 中已有 RedPacketXxxService:123 的到期判定则写清出处；若无则推荐 A 并说明与常见 HTTP 幂等语义一致）
推荐理由：优先与项目已验证事实对齐，避免新语义与存量逻辑冲突。
```

- 一句话问题 + 一句话"为什么必须确认" + 2~5 个选项 + **必须**有 `⭐ 推荐：A|B|C|D` + 一句话推荐理由。
- 推荐须可辩护：引用 FACT id 或 `path:line`；证据不足时推荐「显式新规则」并说明风险，禁止空说「行业惯例」。
- 禁止长篇背景解释；禁止用推荐代替追问（推荐项仍须用户确认后冻结）。

## 禁止问的问题

- "还有其他补充吗？" / "你希望效果怎么样？" / "你有没有特别要求？"
- 项目里已经能查到答案的（违反 R3）。
- 不影响实现结果的、只为"显得严谨"的。
- 已经回答过的（除非出现显式冲突，R4/R5）。

## 出口条件（R9 + 覆盖率，唯一出口）

```bash
node $SKILL/scripts/state.mjs stop .requirementmind
# 退出码 0 = 无 OPEN 关键问题（TECHNICAL 须已自行裁决）+ 无 OPEN 冲突
#          + 无 HIGH 未验证假设 + 无 PENDING 高危 challenge
#          + risk.json 高分维度全部有可解析证据（覆盖率达标）
```

stop 通过 → 进入 Phase 4。**此后禁止以"显得严谨"为由追加提问或审查轮次（R12）**。IMPORTANT 未答项带默认值进规格并记录 Open Questions；OPTIONAL/LOW 一律不问。

## 旧版 questions.json 迁移

若 `questions.json` 缺少 `recommended_option` / `recommendation_reason`（升级技能前落盘）：

```bash
node $SKILL/scripts/state.mjs migrate .requirementmind          # 预览
node $SKILL/scripts/state.mjs migrate .requirementmind --write  # 写入并规范化 A/B/C 选项前缀
node $SKILL/scripts/state.mjs validate .requirementmind
```

迁移会为 OPEN 问题默认推荐 A 并打 `【迁移占位】`；已 ANSWERED 且能匹配 `decisions.json` 的会推断推荐字母。Agent 在向用户展示前须把占位理由改成基于 facts 的正式推荐理由。

## 冲突与用户改变决定

- CONFLICT 类问题的裁决作为新 DEC 冻结，冲突置 RESOLVED 并回填 resolution_decision_id。
- 用户改变决定：旧决策 SUPERSEDED + replaced_by，全量重解析下游。

---
name: requirement-mind
description: AI requirement clarification, adversarial review and spec compilation. Turns a vague one-line requirement into a frozen, evidence-backed DEVELOPMENT_SPEC.md gated by a Requirement Gate, before any coding agent starts. Use when the user says 澄清需求 / 需求追问 / 需求审查 / Requirement Gate / 开始需求分析 / requirementmind, or asks to turn a rough requirement into a dev spec for Codex/Cursor/Claude Code.
---

# RequirementMind — 需求澄清、反驳审查与规格编译

把一句模糊需求，编译成经过取证、追问、反驳、验证、冻结的开发规格。
**核心原则：先把"做什么"彻底搞清楚，才允许 AI 解决"怎么做"。**

Skill home：本文件所在目录，下称 `$SKILL`。加载后将其展开为绝对路径（不要假设具体安装位置）。

## 硬规则（最高优先级，任何阶段不得违反）

- **R1** 需求未通过 Requirement Gate，不允许正式开发。
- **R2** 模型推理不是业务事实。项目里查不到的业务规则一律进 UNKNOWN，禁止擅自补全。
- **R3** 能从项目取证的问题，禁止问用户。优先级：代码 → 数据库 Schema → 接口定义 → 测试 → 历史文档（README/ADR/AGENTS.md/CLAUDE.md）→ Git 历史 → 配置 → 最后才问用户。
- **R4** 用户确认的决策必须冻结（FROZEN），记录来源与时间。
- **R5** 冻结决策不可被静默覆盖；新旧冲突必须显式 supersede。
- **R6** 每收到一个回答，必须重新分析整个需求，不允许机械执行预设问卷。
- **R7** Adversarial Reviewer 的发现默认不可信，必须二次验证。
- **R8** 没有实际证据，不得把 challenge 判为 CONFIRMED。
- **R9** 结束追问的唯一条件是关键疑问清零：Blocking Questions = 0 且 Blocking Conflicts = 0 且 Critical Assumptions = 0。
- **R10** 最终规格必须能让一个完全不了解对话历史的 Agent 直接完成开发。
- **R11** authority=TECHNICAL 或 value=LOW 的问题禁止问用户：AI 采纳可辩护推荐项自行裁决（`freeze --auto`，source=AI_DEFAULT 并附证据 basis）。
- **R12** `stop` 退出码 0 即收敛：禁止追加提问、禁止"为了严谨"加审查轮次；Reviewer/Validator 回流最多 2 轮。

## 状态目录（全部落盘，不依赖聊天记忆）

所有状态写在**目标项目根目录** `.requirementmind/`：

```
.requirementmind/
├── session.json      # 当前阶段游标 + 需求原文
├── facts.json        # FACT-xxx 项目事实（带文件级证据）
├── questions.json    # Q-xxx   问题（priority: BLOCKING|IMPORTANT|OPTIONAL）
├── decisions.json    # DEC-xxx 冻结决策（FROZEN | SUPERSEDED）
├── assumptions.json  # ASM-xxx 模型推断（risk: HIGH|MEDIUM|LOW）
├── conflicts.json    # CON-xxx 冲突（severity: BLOCKING|IMPORTANT）
├── challenges.json   # CH-xxx  Reviewer 的 CLAIM
├── evidence.json     # 裁决：CONFIRMED | PLAUSIBLE | REFUTED
├── gate.json         # Gate 检查表 + 最终状态
└── history/          # 每轮快照
```

Schema 定义见 `$SKILL/schemas/state.schema.json`。会话中断后从这里恢复，禁止凭聊天记忆续跑。

## 工作流

用户给出需求后，按顺序执行下列阶段。每个阶段先读对应的 `references/` 规则文件再动手。

### Phase 1 — Context Scanner → `references/scanner.md`
扫描目标项目（Java 系优先），产出 `facts.json` + 人读的 `PROJECT_FACTS.md`。
只记**已验证事实**，每条带 `path:line` 证据；查不到的领域留空，禁止臆造。

### Phase 2 — Requirement Parser → `references/parser.md`
把需求拆成 KNOWN / UNKNOWN / CONFLICT / ASSUMPTION 四类，分别落盘
`questions.json`（由 UNKNOWN 生成，每条标 `authority` + `value`）/ `conflicts.json` / `assumptions.json`。

### Phase 2.5 — Risk Router → `references/risk-router.md`
八维风险评分写入 `risk.json`（score>=2 必须带证据 refs）→
`node $SKILL/scripts/state.mjs risk .requirementmind` 分层，决定 Phase 5 审查投入：
LIGHT(0-7)=现流程 / FOCUSED(8-14)=+1 专项 / COUNCIL(15+)=至多 3 专项。**80% 需求应停在 LIGHT。**

### Phase 3 — Grilling Engine（循环）→ `references/grilling.md` + `references/freezer.md`
```
frontier 取本批 → 整批抛出 → 用户逐个回答 → freeze 逐个冻结 → 全量重解析 → 新疑问整批再抛
```
frontier 只出 **USER_ONLY** 问题；TECHNICAL/LOW 由 AI 采纳推荐 `freeze --auto` 自行裁决（R11），不问用户。
出口唯一（R9+覆盖率）：`node $SKILL/scripts/state.mjs stop .requirementmind` 退出码 0，之后禁止追加提问（R12）。
省 token 纪律：拼批次只用 `frontier` 输出，**禁止通读 questions.json/conflicts.json**；每个回答用 `freeze` 落盘，禁止手工改 JSON。

### Phase 4 — Spec Compiler → `references/spec-compiler.md`
从 canonical JSON 状态**单向生成** `docs/requirementmind/DEVELOPMENT_SPEC.md`。
禁止手改 Markdown 后反向生效。

### Phase 5 — Adversarial Review（独立上下文 subagent）→ `references/reviewer.md`
按 risk.json 的 tier 派发（全新上下文 subagent）：LIGHT=1 个 reviewer；FOCUSED=reviewer +
`references/specialists.md` 对应专项节；COUNCIL=+至多 3 个专项（专项只审其维度，不重复全量攻击）。
prompt = `$SKILL/references/reviewer.md` 全文（+ 专项节）+ DEVELOPMENT_SPEC.md 与 facts.json 的
绝对路径 + 项目根目录。它没有本对话历史——这是特性，不是缺陷。
产出的每条 CHALLENGE 写入 `challenges.json`，status=PENDING_VALIDATION。
**Reviewer 的输出只是 CLAIM，不是事实（R7）。**

### Phase 6 — Evidence Validator（独立上下文 subagent）→ `references/validator.md`
再派发一个独立 subagent，对每条 challenge 重新取证，裁决：
- **CONFIRMED** — 有实际证据。BLOCKING 级的 → 自动转成新 BLOCKING 问题，**回到 Phase 3 重新追问**。
- **PLAUSIBLE** — 合理但证据不足。HIGH 风险的必须转用户问题。
- **REFUTED** — 质疑不成立，销案，不保留为风险。

裁决写入 `evidence.json`。

### Phase 7 — Requirement Gate → `references/gate.md`
16 项检查表全 PASS 且硬门槛计数（Blocking Questions / Blocking Conflicts /
Critical Assumptions / Unvalidated High Risks）全为 0 → `gate.json` 置
READY_FOR_DEVELOPMENT；否则 BLOCKED 并列出缺失项。判定用
`node $SKILL/scripts/state.mjs gate .requirementmind` 复核。

### 收尾 — Prompt Export
READY 后，按 `$SKILL/templates/agent-prompt.md` 把 DEVELOPMENT_SPEC.md 转成
`.agent-prompts/{generic,codex,cursor,claude-code}.md`，交给用户的 Coding Agent。
导出内容必须内嵌开发 Agent 行为约束与 DEVELOPMENT_BLOCKER 报告格式。

**Agent OS 衔接**：Gate READY 时，把 `decisions.json` 中 `FROZEN` 同步到项目根
`.agent/state/decision_state.json`（schema 见技能库 `shared/agent-state.schema.yaml`）；
`goal` 写入 `project_state.json`。实现阶段用 `/agent-runtime` + `/ai-code`，勿依赖本 chat 历史。

### Phase 8 — Development Feedback Loop（开发期回流）→ `references/dev-feedback.md`
Coding Agent 开发中发现规格与代码冲突 / 影响面蔓延 / 新业务未知 → 输出结构化
DEVELOPMENT_BLOCKER 报告退回本流程：转 CONFLICT/QUESTION → 批量追问 → 冻结/supersede
→ 重编译规格（revision N+1）→ 重跑 Gate → 重新导出。**禁止 Coding Agent 在冲突中自己猜。**
这是常态循环，不是异常：规格与代码现实的冲突在写代码前不可能 100% 排除，靠回流收敛。

## 提问纪律（对用户的可见行为）— 批量模式

格式模板与禁例全文见 `references/grilling.md`（Phase 3 必读）。铁律：
- 每轮整批抛出 frontier（只含 **USER_ONLY** 的 BLOCKING+IMPORTANT + OPEN 冲突）；TECHNICAL/LOW 不问（R11）；选项依赖未答问题的归下一轮；取证进行中不阻塞其余问题先抛（R3）。
- 每问必须带 `⭐ 推荐：X` + 可追溯到 FACT/`path:line` 的理由；推荐是默认建议，用户选其他项照常冻结，不得劝说改选。
- 禁止问项目里能查到的（R3）；禁止"还有补充吗"式空问题；禁止代答未答项。
- 用户改主意：`freeze --supersede` 显式改判（旧 DEC SUPERSEDED + replaced_by，下游标记待重审），禁止静默覆盖（R5）。

## 常用辅助脚本

```bash
node $SKILL/scripts/state.mjs frontier .requirementmind                    # 本批待决项 + 计数；退出码 0 = R9 达成
node $SKILL/scripts/state.mjs freeze   .requirementmind Q-007 B [--supersede] [--auto] [--impact "a,b"]  # 冻结答案 → DEC（--auto=AI 自治）
node $SKILL/scripts/state.mjs risk     .requirementmind                    # 八维评分校验 → LIGHT/FOCUSED/COUNCIL + 专项路由
node $SKILL/scripts/state.mjs stop     .requirementmind                    # 停止条件（R9+覆盖率）；退出码 0 = 收敛
node $SKILL/scripts/state.mjs eval     .requirementmind                    # 会话指标（自治率/验真率），供 Eval 闭环
node $SKILL/scripts/state.mjs counters .requirementmind                    # 四项关键计数
node $SKILL/scripts/state.mjs validate .requirementmind                    # JSON 结构校验
node $SKILL/scripts/state.mjs gate     .requirementmind                    # Gate 硬门槛汇总
node $SKILL/scripts/state.mjs snapshot .requirementmind                    # 快照到 history/
node $SKILL/scripts/state.mjs migrate  .requirementmind [--write]          # 旧 questions 补推荐字段（预览/写入）
node $SKILL/scripts/selftest.mjs                                           # 确定性自测（改动本 skill 后必跑）
```

## 下游栈（Gate READY 之后 · 禁本 skill 兼做实现）

职责切分见技能库 `shared/pipeline-contract.md`（五层：WHAT/WHY/CTX/HOW/DO）。

- **WHAT 已冻结**：禁止再用 `/ai-design` 或 `@grilling` 重问同一 FROZEN；HOW（模块/接缝/验证档）才走 `/ai-design`。
- **同步**：`decision_state.json` + 业务仓 `node scripts/sync-requirementmind-brain.mjs`（FROZEN → project-brain）。brain 记忆不得覆盖 FROZEN。
- **CTX**：Handoff 经 `handoff_to_task_bundle.py` 写入 `.contextmind/task.active.json`；Java 结构用 `context_orient`（非 brain）。
- **DO**：`/ai-code`。规格与代码冲突 → `DEVELOPMENT_BLOCKER`（`references/dev-feedback.md`），禁止编码 Agent 私裁。
- **Grill 互斥**：本流程 Phase 3 是 WHAT grilling 唯一入口；未 READY 时不要另挂 `grilling` skill 并行。

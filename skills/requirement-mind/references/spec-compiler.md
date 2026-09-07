# Spec Compiler 规则（Phase 4）

目标：从 canonical JSON 状态单向生成开发规格。**JSON → Markdown，单向，禁止反向。**

## 输入

`.requirementmind/` 下的 facts / decisions / conflicts / assumptions / questions / evidence（Phase 6 后重编译时）。

## 输出

`docs/requirementmind/DEVELOPMENT_SPEC.md`，使用 `$SKILL/templates/DEVELOPMENT_SPEC.md` 模板，章节固定：

```
# Goal
# Current System Facts      ← 只引用 FACT-xxx，每条带 path:line
# Scope / # Out of Scope
# Frozen Business Rules     ← 只列 DEC-xxx 冻结决策，禁止出现任何推断规则
# Database Changes / # API Changes / # State Machine
# Validation Rules / # Permission Rules
# Concurrency Rules / # Idempotency
# Exception Handling / # Compatibility Requirements
# Impact Analysis            ← 影响面分析：反向检索所有会读到/写到被改表、字段、状态值、接口的
#                              现有代码（grep 调用方/引用方），逐条列 file:line；发现规格未
#                              覆盖的受影响处，要么显式纳入 Scope，要么立 QUESTION，禁止静默遗漏
# Files Expected To Change / # Files Forbidden To Change
# Acceptance Criteria / # Required Tests
# Known Risks               ← 来自 REFUTED 以外的 challenges 与 MEDIUM assumption
# Frozen Decisions          ← DEC 索引表（含 SUPERSEDED 链）
# Open Questions            ← OPTIONAL 问题 + 默认值声明
# Development Gate          ← 当前 gate.json 状态快照
```

附带生成：
- `docs/requirementmind/00-PROJECT_CONTEXT.md` — PROJECT_FACTS.md 的副本引用
- `docs/requirementmind/13-OPEN_QUESTIONS.md` — 仅当有余留 OPTIONAL 项时生成

## 生成纪律

- **R10 检验**：假设读者是完全不了解对话历史的 Coding Agent——每个章节自足，不出现"如前所述""和用户商量过"这类引用。
- HIGH 风险 assumption 出现在任何章节 = 编译失败，先回 Phase 3 处理。
- 每个 Freeze Rules 条目必须能追溯到 DEC id；每条 Current System Facts 必须能追溯到 FACT id。追溯不了的条目删掉。
- Out of Scope 必须显式列出，防止 Coding Agent 自由发挥。
- 阶段游标置 `SPEC_COMPILED`，快照到 `history/`。

## 重编译

Phase 6 产生新决策回流后，或发生 supersede 后，必须重新编译整个 DEVELOPMENT_SPEC.md（从 JSON 全量生成，不做增量补丁）。

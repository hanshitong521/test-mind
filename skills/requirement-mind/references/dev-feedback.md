# Development Feedback Loop 规则（开发期回流，Phase 8）

目标：Coding Agent 按 DEVELOPMENT_SPEC.md 开发时，撞上规格与代码现实的冲突、或规格未覆盖的受影响代码，**必须停下来提问，禁止猜**。所有回流走同一条管道，最终收敛为修订版规格。

## 触发条件（Coding Agent 侧）

开发中遇到以下任一情况，立即停止相关部分的开发：

1. **规格与代码冲突** — 按规格实现会破坏现有逻辑，或现有代码使规格方案不可行。
2. **影响面蔓延** — 修改波及规格未提及的代码/表/接口/调用方。
3. **新业务未知** — 规格没覆盖、项目里也查不到的业务规则。
4. **规格自身矛盾或不可实现**。

## DEVELOPMENT_BLOCKER 报告格式（Coding Agent 填写）

```
BLOCKER-xxx
类型:        SPEC_CODE_CONFLICT | SCOPE_EXPLOSION | NEW_BUSINESS_UNKNOWN | SPEC_INTERNAL
场景:        一句话说明正在做什么时发现的
规格出处:     DEVELOPMENT_SPEC.md 的具体章节 + 原文
代码证据:     file:line（真实打开核对过）
冲突内容:     规格要求 vs 代码现实，各一句
影响面:      可能被波及的其他代码/表/接口（能列多少列多少）
建议问题:     需要用户/需求方裁决的问题草案（1 个或多个）
```

禁止：自己在冲突里选边继续写代码；批量攒到最后一次性报；只报现象不给规格出处和代码证据。

## RequirementMind 处理管道（主 Agent 侧）

```
收到 BLOCKER 报告
→ 每条逐项落地：
   SPEC_CODE_CONFLICT / SCOPE_EXPLOSION → conflicts.json（spec 侧 vs 代码侧，均带证据）
   NEW_BUSINESS_UNKNOWN / 建议问题      → questions.json（沿用优先级分级规则）
→ 能从项目取证的自答（R3），剩余的按 grilling.md 批量抛给用户
→ 用户裁决 → 冻结（涉及旧决策的走 supersede）
→ 全量重编译 DEVELOPMENT_SPEC.md（revision N+1，变更点清单写入文档头）
→ 重跑 Gate → 重新导出 agent prompt（标注 revision 与增量范围）
→ Coding Agent 从 BLOCKER 对应章节继续，已完成的合法部分不重做
```

## 硬约束

- BLOCKER 处理期间，Coding Agent 只允许开发与任何 BLOCKER 无关的章节。
- 同一个 BLOCKER 不允许第二次出现：若回流后规格仍未消除该冲突，视为流程缺陷，必须修规格生成逻辑而不是让 Coding Agent 再撞一次。
- 回流产生的决策与其他冻结决策冲突时，显式 supersede，禁止静默并存。
- Gate 的 Spec Consistency 检查项必须覆盖所有历史 revision 的变更点。

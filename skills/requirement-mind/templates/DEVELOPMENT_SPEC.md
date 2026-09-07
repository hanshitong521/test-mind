# DEVELOPMENT_SPEC 模板

> Spec Compiler 从 `.requirementmind/*.json` 单向生成本文件。每节必须可追溯到 FACT-xxx / DEC-xxx / CH-xxx，追溯不了的条目删除。读者是完全不了解对话历史的 Coding Agent（R10），每个章节自足。

# Goal
<!-- 一段话：本需求要达成什么业务目标，判定"做对了"的标准 -->

# Current System Facts
<!-- 编译自 facts.json。格式：- [FACT-001] 事实陈述（sql/red_packet.sql:18） -->

# Scope
<!-- 本需求要做的事，逐条列出 -->

# Out of Scope
<!-- 显式禁止 Coding Agent 顺手做的事 -->

# Frozen Business Rules
<!-- 编译自 decisions.json，每条带 DEC id。禁止出现任何无 DEC 来源的"规则" -->
<!-- 格式：- [DEC-007] topic: <decision 原文> -->

# Database Changes
<!-- 表/字段/索引变更；无变更则写"无"并说明依据 -->

# API Changes
<!-- 新增/修改的接口契约：路径、方法、入参、出参、错误码 -->

# State Machine
<!-- 状态集合、流转图（mermaid 或列表）、每个流转的触发条件与权限 -->

# Validation Rules
<!-- 入参校验、业务校验，逐条可测试 -->

# Permission Rules
<!-- 谁可以对什么做什么；身份定义必须来自冻结决策 -->

# Concurrency Rules
<!-- 并发场景与判定口径（引用冻结决策，如 REQUEST_RECEIVED_AT） -->

# Idempotency
<!-- 哪些操作必须幂等、幂等键是什么、重复请求的预期行为 -->

# Exception Handling
<!-- 每类失败的处理：回滚、重试、补偿、对用户的可见错误 -->

# Impact Analysis
<!-- 反向检索现有代码中所有引用被改表/字段/状态值/接口的位置（file:line）。
     规格未覆盖的受影响处必须显式处理：纳入 Scope 或立 QUESTION，禁止静默遗漏。 -->

# Compatibility Requirements
<!-- 对既有数据/接口/状态语义的兼容要求 -->

# Files Expected To Change
<!-- 预期改动的文件/目录清单（基于 facts.json 的结构） -->

# Files Forbidden To Change
<!-- 明确不许动的文件/模块 -->

# Acceptance Criteria
<!-- 可验证的验收条款，逐条 "Given/When/Then" 或等价形式 -->

# Required Tests
<!-- 必须编写的测试清单：单测/集成/边界用例，映射到验收条款 -->

# Known Risks
<!-- 来自未被 REFUTED 的 challenges 与 MEDIUM assumption，每条带来源 id -->

# Frozen Decisions
<!-- DEC 索引表：id | topic | 决策 | 状态 | replaced_by -->

# Open Questions
<!-- OPTIONAL 问题及已采用的默认值；BLOCKING 项出现在这里 = Gate 必然 BLOCKED -->

# Development Gate
<!-- gate.json 快照：状态 + 硬门槛计数 + 检查表摘要 -->

# Agent Prompt 导出模板

输入：READY 状态的 `docs/requirementmind/DEVELOPMENT_SPEC.md`。
输出：`.agent-prompts/<target>.md`，target ∈ `generic` / `codex` / `cursor` / `claude-code`。

## 生成规则

1. 开头固定放下面的「开发 Agent 行为约束」块（原文照贴，不许删改）。
2. 正文 = DEVELOPMENT_SPEC.md 全文。
3. 按 target 在末尾追加差异指令：
   - **generic** — 无追加。
   - **codex** — "按 spec 顺序实现；每完成一个章节运行对应 Required Tests；遇到 Development Gate 约束冲突立即停止并输出 DEVELOPMENT_BLOCKER 报告。"
   - **cursor** — "严格遵守 Files Forbidden To Change；冻结业务规则所在文件只在注释标注处做最小修改。"
   - **claude-code** — "先读 Current System Facts 与 Frozen Decisions 再写代码；实现全部完成后对照 Acceptance Criteria 自检并输出逐条结果。"
4. 若 gate.json 不是 READY：禁止导出，向用户报 BLOCKED 及缺失项。

## 开发 Agent 行为约束（原样嵌入）

```
## 行为约束（最高优先级）
允许：
- 选择技术实现细节（设计模式、内部结构、函数拆分）
- 局部重构你正在修改的代码
- 增加必要的测试
禁止：
- 修改 Frozen Business Rules / Frozen Decisions 中的任何决策
- 擅自新增业务含义或默认值
- 修改数据库语义、状态含义、权限规则、验收口径
- 遇到业务未知项时自己猜
发现以下任一情况，立即停止相关部分开发，输出 DEVELOPMENT_BLOCKER 报告：
- 按规格实现会与现有代码/逻辑冲突
- 修改波及规格未提及的代码、表、接口或调用方
- 规格未覆盖且项目里查不到的业务规则
报告格式（逐字段填写）：
BLOCKER-<序号>
类型: SPEC_CODE_CONFLICT | SCOPE_EXPLOSION | NEW_BUSINESS_UNKNOWN | SPEC_INTERNAL
场景: <发现时正在做什么>
规格出处: <章节 + 原文>
代码证据: <file:line>
冲突内容: <规格要求 vs 代码现实>
影响面: <可能被波及的其他代码/表/接口>
建议问题: <需要裁决的问题草案>
报告处理完成并获得修订版规格（revision N+1）前，只允许开发与 BLOCKER 无关的章节。
```

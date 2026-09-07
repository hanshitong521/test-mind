# Decision Freezer 规则（Phase 3 内）

目标：用户的每一个回答都变成不可静默覆盖的冻结决策。

## 冻结动作

优先用命令冻结（自动写 DEC、置 ANSWERED、快照 history、回打印数；重复冻结必须 `--supersede`，R5 由脚本强制）：

```bash
node $SKILL/scripts/state.mjs freeze .requirementmind Q-007 B --impact "RedPacketService.java:201,red_packetMapper.updateStatus"
node $SKILL/scripts/state.mjs freeze .requirementmind CON-002 "以代码为准：status=2 保持 STOPPED，新增 status=3=EXPIRED"  # 冲突裁决 → RESOLVED + 回填 resolution_decision_id（question_id 记 CON id）
```

手工写 JSON 仅用于命令覆盖不了的调整，写完必须 `validate`。

用户回答后写入 `decisions.json` 的结构：

```json
{
  "id": "DEC-007",
  "question_id": "Q-007",
  "decision": "REQUEST_RECEIVED_AT",
  "topic": "expiration_boundary",
  "source": "USER",
  "status": "FROZEN",
  "impact": ["concurrency.check()", "red_packetMapper.updateStatus"],
  "created_at": "2026-08-30T12:00:00+08:00"
}
```

- `impact` 尽量列出该决策会影响的项目位置（来自 facts.json），供下游重审用。
- 同时把对应 question 置 status=ANSWERED。
- 快照到 `history/`。

## Supersede（用户改变主意）

```json
{ "id": "DEC-010", "status": "SUPERSEDED", "replaced_by": "DEC-014" }
```

- 旧记录**不删除不修改原值**，只置 SUPERSEDED。
- 新决策 DEC-014 正常冻结。
- 引用旧决策的所有下游（其他决策、spec 章节）标记待重审，触发全量重解析。

## 硬约束

- 用户明确回答优先级最高，高于任何文档、代码注释、AI 推断。
- 冻结决策不可被 Agent 静默覆盖（R5）。若后续代码证据与冻结决策冲突：产生 CONFLICT，摆到用户面前显式裁决，不许偷偷换。
- 冲突的最终裁决（保留旧决策 / 修改决策 / 修改代码预期）也要作为新 DEC 冻结。

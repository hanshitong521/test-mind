# Risk Router 规则（Phase 2.5 — 决定审查投入，不是角色越多越好）

原则：**80% 需求走现有轻量流程（主 Agent + Reviewer + Validator）；15% 加 1 名专项；5% 才启动 Mini Committee。**
不为覆盖而堆角色，不在 LIGHT 层"保险起见"加人。

## 评分（LLM 判分，脚本路由）

Phase 2 完成后写 `.requirementmind/risk.json`：8 个维度各 0-3 分，`score>=2` 必须给 `refs`（FACT-/CON-/DEC- id），`stop` 会做可解析校验——抬分无据 = 编造，会被拦。

| 维度 | 0 分 | 1 分 | 2 分 | 3 分 |
|---|---|---|---|---|
| business_criticality | 边缘功能 | 一般业务 | 核心业务链路 | 资金/交易主线 |
| data_impact | 无数据变更 | 只读新增 | 改表/改语义 | 迁移/删除/金额 |
| concurrency_risk | 单用户操作 | 低频并发 | 可预期竞争 | 抢单/秒杀/队列 |
| security_risk | 无鉴权面 | 内部接口 | 用户数据 | 支付/权限边界 |
| compatibility_risk | 无外部调用方 | 内部调用 | 有老客户端/老接口 | 公开协议变更 |
| irreversibility | 全可逆 | 可回滚 | 部分不可逆 | 不可逆删除/打款 |
| blast_radius | 单文件 | 单模块 | 跨模块 | 跨系统 |
| evidence_gap | 事实齐全 | 少量 UNKNOWN | 关键规则未知 | 大面积未知 |

```bash
node $SKILL/scripts/state.mjs risk .requirementmind
# 校验评分 → 输出 {total, tier, specialists, top_dims}；评分缺 refs 时退出码 1
```

## 分层（脚本决定，不靠感觉）

| 总分 | 层级 | 审查投入 |
|---|---|---|
| 0-7 | LIGHT | 现有流程：主 Agent + Reviewer + Validator |
| 8-14 | FOCUSED | + 1 名专项 Reviewer（最高分维度映射，取 specialists.md 对应节） |
| 15-24 | COUNCIL | + 至多 3 名专项 Reviewer；全部 BLOCKING CLAIM 必须过 Validator |

专项映射：`security_risk→security`；`data_impact/irreversibility→data_integrity`；`concurrency_risk→concurrency`；`compatibility_risk→compatibility`；`evidence_gap→testability`。`business_criticality`、`blast_radius` 不映射专项，只抬总分。

## 纪律

- LIGHT 层禁止追加任何角色；FOCUSED/COUNCIL 的专项角色**只审其专项维度**，不重复全量攻击（省 token，防同质化）。
- 评分随重解析更新：supersede 或新 CONFLICT 出现后重打分并重跑 `risk`。
- tier 写回 `risk.json`（total/tier/specialists 字段），Phase 5 按它派发。

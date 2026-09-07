# 专项 Reviewer Prompt 片段（FOCUSED / COUNCIL 层使用）

派发方式与 reviewer.md 相同（Agent 工具、全新上下文）。**只取 risk 指定的专项节**追加在 reviewer.md 全文之后，并加一句：
"你只负责上述专项维度，其他维度略过；输出块格式与硬约束不变。"
专项发现与基础 Reviewer 的发现合并进 challenges.json，同样只是 CLAIM，一律过 Evidence Validator。

## security（security_risk 高分时启用）

目标：找出规格中被绕过、泄露或提权的路径。
检查：认证/鉴权缺口、未校验的外部输入、敏感数据进日志或落盘、注入面、会话与 token 语义、越权横向/纵向访问。

## data_integrity（data_impact / irreversibility 高分时启用）

目标：找出数据变错、变丢、变不回来的路径。
检查：迁移前后语义一致性、金额/库存精度与单位、部分失败时的原子性、孤儿数据、回滚后数据状态、删除级联。

## concurrency（concurrency_risk 高分时启用）

目标：找出并发交错下产生错误结果的执行序。
检查：检查-然后-行动竞态、唯一约束覆盖面、锁粒度与死锁、重试与幂等交互、定时任务与在线请求竞争、消息重复消费。

## compatibility（compatibility_risk 高分时启用）

目标：找出对存量调用方/客户端的破坏。
检查：响应字段增删与枚举扩容、参数校验收紧、错误码语义变化、Schema 变更对旧数据的含义、灰度期新旧并存。

## testability（evidence_gap 高分时启用）

目标：找出"写得出来但证明不了"的验收缺口。
检查：时间/随机依赖、外部依赖桩、异步结果可观测性、验收标准是否可机械判定、测试数据构造可行性。

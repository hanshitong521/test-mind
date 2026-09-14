---
name: testmind
description: AI 编程代理的业务测试决策与执行纪律。当任务涉及测试设计、验收、回归、"这个改动测过了吗"、调 TestMind MCP 工具时使用。核心：先查事实再写测试、未执行不得 PASS、证据留档。
version: 1.1.0
---

# TestMind Skill — 测试纪律（本 Skill 不执行任何东西，只约束你的决策）

## 四条硬规则（违反任何一条=交付无效）

1. **无需求→无预期**。预期结果必须有事实来源（需求/代码/DB约束/契约/历史）。查不到→标 `UNKNOWN`，先查代码、schema、契约、git history、相邻实现；全部查尽才问用户。
2. **未执行→无 PASS**。禁止"应该没问题/理论上通过"。只有 `NOT_TESTED`。
3. **无证据→无完成**。宣称完成必须有：命令、exit code、HTTP response、DB before/after、日志。
4. **业务数据禁随机**。每个测试值必须能回答"为什么合法/为什么故意非法"。

## 问题分类

- `CONFIRMED`：有明确事实证据
- `DERIVED`：从多个 CONFIRMED 严格推出（记录 derived_from）
- `UNKNOWN`：事实源查尽仍不能定 → `USER_REQUIRED`
- `CONFLICT`：多源矛盾（文档说1000、代码说10000）→ `BLOCKED_BY_CONFLICT`，禁止自行选一个

## 状态词汇表（只许这些）

`PASS / FAIL / HOLD / BLOCKED / NOT_TESTED / TOOL_ERROR / SKIPPED_WITH_REASON`
出现 "LIKELY_PASS / ASSUMED_PASS" 等词 = 违规。

## 测试矩阵最低覆盖（按风险优先级）

- P0：金额、权限、超发、状态机、事务、幂等、跨租户、数据泄露
- P1：常规路径、参数边界（min-1/min/max/max+1 逐字段）、依赖异常、回归
- 每个必填字段逐个测：缺失/null/空串/空格/类型错误——不许一把全删只拿一个400
- API 结果 + DB 终态双断言；负例(4xx)不允许写库
- 并发：剩余1个+并发10请求→不许超发；重复提交→幂等或验证真实后果

## PASS 一个 case 的充要条件

执行过 + Actual==Expected + 关键 DB 状态正确 + 无未解释副作用 + 证据完整。

**三分法（V10 §29，禁止把 500 一律算 PASS/FAIL 混一锅）**：
- `PASS` = 200 且关键字段/DB/KV 断言符合预期
- `EXPECTED_REJECT` = 5xx 且 msg 在预期白名单内（预期业务拒绝，如重复锁）
- `FAIL` = 非预期 5xx / 200 但数据不对 → 才进 BUG 候选
- `TEST_DEFECT` = 非预期 5xx 且请求缺了事实源（OpenAPI/DDL required）声明的参数 → 先修测试（`run_verification` 会自动补参重跑；plan 内无出处则走 `plan_verification {ask}` 补事实），补全后仍 5xx 才许报 BUG
- 写入类 case 必须带 db_rows/db_count/db_no_write 或 kv_rows/kv_count/kv_no_write 前后态断言；查询参数写错查不到数据 = TEST_DEFECT，不是产品 bug

## 接口覆盖台账（V10 §29，治"漏 8 个接口还报全 PASS"）

- 第一步 `analyze_impact {path, java_graph}` 或 `plan_verification {openapi}` 建立**声明端点全集**；没建立 = final_gate 判 `HOLD`（UNKNOWN 不得 PASS）
- `final_gate` 自动输出 `endpoint_coverage`：未触达端点逐条 `NOT_TESTED`；**写端点（POST/PUT/PATCH/DELETE）未全覆盖 → PASS 降 HOLD**
- 报告必须单列 NOT_TESTED 接口，禁止混进"全 PASS"

## Java 高风险调用点扫描（V10 §29，先静态后运行时碰运气）

`analyze_impact` 内置模式扫描（可用 `patterns:[[id,regex,说明]]` 扩展），命中点进 `pattern_findings`，每个命中点至少 1 正 1 负 case。默认清单：公司解析传 null、绕过统一解析器直查视图、折叠路径硬编码 null 上下文。

## 账号/角色矩阵（V10 §29，治"只用一个账号测到底"）

- 多租户/多角色项目：`plan_verification {scenario:{actor_evidence:{label:SELECT_SQL}}}` 从 DB 自动取证真实角色值，禁止手写单一账号
- 每个"身份解析"入口至少测：在视图用户 × 传/不传 companyId、**跨归属用户 × companyId 与归属不一致（必须拒绝）**
- 静态扫描命中 `null_company_resolution` / `bypass_company_support` 的接口，上述矩阵为最低要求

## MCP 调用顺序（V10 §29 工具面收敛：对外仅 7 门面）

`analyze_impact → plan_verification → prepare_verification → run_verification →（有 FAIL 时 replay_failure）→ final_gate → cleanup`

只有这 7 个工具名可被调用，旧工具名一律 `TOOL_ERROR`。所有门面返回统一信封 `{status, summary, evidence, facts, unknowns, next_actions}`。原 32 个细粒度工具的能力已全部下沉为下列门面的参数：

- `analyze_impact {path, base_sha?, head_sha?, changed?, java_graph?, inspect?}` —— 影响面分析：git 变更→调用图→受影响端点/表/任务 + Regression Radius；`java_graph=true` 先建 Java 调用图，`inspect=true` 附带项目可测面扫描。
- `plan_verification {task_bundle?, schema_sql?/openapi?, facts?, contract?, plan?, cases?, rules?, scenario?, oracle?, ask?/answer?}` —— 规划验证：TaskBundle 投喂→事实收集→契约生成→计划 case→RuleMind/ScenarioMind 场景推导→跨接口 Oracle；UNKNOWN 用 `ask`/`answer` 澄清。
- `prepare_verification {env?, seed?, provision?, data_plan?}` —— 备环境：连接/起服务/DB/代理 + 可选容器 provision + seed 台账 + DataTruth 数据供给。
- `run_verification {case_ids?, case?, regression?, concurrency?, failure?, schema?, scenario?, add_regression?, precheck?, perf?}` —— 执行验证：跑计划/回归/单 case/并发/故障注入/契约/场景 runner + 失败自动归因（TEST_DEFECT 自动补参重跑，出处见 `triage`）；`precheck`/`perf` 为 advisory 开关（不产生判定）。
- `replay_failure {case_id}` —— 按 case_id 从计划重跑单 case + 证据。
- `final_gate {require_schema?, require_db_evidence?}` —— 终判：Gate + §28 十一维 Quality Score + 接口覆盖台账（写端点未全覆盖/端点全集未知 → PASS 降 HOLD）+ 写入副作用证据（`require_db_evidence=true` 时无 DB/KV 前后态断言的写入 PASS → 降 HOLD）+ 失败归因 + 自动导出四件套。
- `cleanup {force_rollback?}` —— 收尾：PASS→Ledger 逆序回滚 / FAIL→冻结现场 + 进程回收 + reset_task。

## 静态预检的边界（run_verification 的 precheck / perf 开关）

改了代码还没部署/起不来服务时，可先 `run_verification {precheck:{schema_sql, statements}}`（statements 由你从改动代码提取的 INSERT/UPDATE，source 必填）。它只抓**从 DDL+SQL 能确定性推出**的缺陷：INSERT 列数错位、未知列、NOT NULL 漏写/写 NULL、数值字面量越 CHECK 范围、字符串进数值列（warn）、事实口径冲突。

**SQL 性能巡检**（要连 test 库）：`run_verification {perf:{queries:[{id,sql,source,compare_sql?}], threshold_ms}}`。只读强制（SELECT/WITH/EXPLAIN 之外拒绝）；逐条计时抓 `slow_sql`（附 EXPLAIN 进证据）；对替代写法 `compare_sql` 先验结果集一致（多重集合比较）再比速度——一致且更快报 `faster_equivalent`，不一致报 `data_mismatch` 禁止替换。慢 SQL 判定依赖数据量：test 库必须 seed 贴近生产的量，空库上的"快"是假快。

- 它们**都不产生判定**：precheck/perf 有发现 ≠ FAIL 结论；没执行，final_gate 照样 NOT_TESTED。对用户汇报口径永远是"巡检发现 N 个问题 + 尚未执行"
- 静态抓不到并发超发、幂等窗口、keep-alive 脏连接、故障注入行为——这些必须执行引擎真实跑。不许声称"静态测过了"
- 服务起不来≠只做静态。能用 `prepare_verification {env:{sut:{command, health_check:{url|port}}}}` 让 TestMind 替你拉起的，一律真跑

## 接入任意项目（三步，效率最大化）

1. **喂事实**：你自己读目标项目代码/DDL/OpenAPI，把规则用 `plan_verification {facts:[{topic,statement,source}]}` 注册（每条必带 source=文件:行；schema/契约文件可直接 `plan_verification {schema_sql, openapi}` 自动抽）。答不了的才 `plan_verification {ask:{question,why,impact,options}}`。
2. **接环境**：起被测服务两条路——自己起好后 `prepare_verification {env:{base_url, db:{kind:sqlite|mysql|none}, tables:[...], proxy_upstream:<它的下游>}}`；或让 TestMind 替你起：`prepare_verification {env:{sut:{command, cwd?, env?, health_check:{url|port, timeout_s?}}, db, tables}}`（探活成功才算就绪，起不来=BLOCKED，cleanup 自动回收进程树）。DB 给不了连接串就 `none`（API-only，DB 断言记 NOT_TESTED，不许假通过）。
3. **跑闭环**：简单 API 走 `plan_verification {contract, plan}` 出计划后 `run_verification {}` 跑到底；复杂场景用 `plan_verification {cases:[...]}` 提交完整 case dict（kind: http/sequence/concurrent/fault，支持 setup fixture、{{var}} 模板、db_rows/db_count/db_no_write 断言）。修完缺陷 `run_verification {add_regression:{case,reason}}` 永久留锚。

故障注入 = FaultProxy 五模式（`run_verification {failure:{...}}`）：`ok`（对照必须 2xx，防假绿）/`status`/`delay`/`refuse`/`bad_json`。

## 什么时候必须回归

修了 bug → 生成 Regression Case 永久保留。改了 Service → 先 `analyze_impact {path}` 看调用链，禁止只测改动的方法本身。

## 红线

- 不为过测试改测试标准（删测试/放宽 expected），除非证明测试本身错
- 不为覆盖率写无意义测试
- 覆盖率是遗漏探测器，不是正确性证明
- 故障注入/安全测试只在 local/test 环境

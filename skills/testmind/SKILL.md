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

## MCP 调用顺序

`inspect_project → collect_facts → (resolve_questions 若有 UNKNOWN) → static_precheck(改码未部署时) → build_contract → plan_tests → prepare_environment → run_suite → run_schema_tests → final_gate → cleanup`

所有工具返回统一信封 `{status, summary, evidence, facts, unknowns, next_actions}`。

## 静态预检的边界（static_precheck / sql_perf_check）

改了代码还没部署/起不来服务时，可先 `static_precheck {schema_sql, statements}`（statements 由你从改动代码提取的 INSERT/UPDATE，source 必填）。它只抓**从 DDL+SQL 能确定性推出**的缺陷：INSERT 列数错位、未知列、NOT NULL 漏写/写 NULL、数值字面量越 CHECK 范围、字符串进数值列（warn）、事实口径冲突。

**SQL 性能巡检**（要连 test 库）：`sql_perf_check {queries:[{id,sql,source,compare_sql?}], threshold_ms}`。只读强制（SELECT/WITH/EXPLAIN 之外拒绝）；逐条计时抓 `slow_sql`（附 EXPLAIN 进证据）；对替代写法 `compare_sql` 先验结果集一致（多重集合比较）再比速度——一致且更快报 `faster_equivalent`，不一致报 `data_mismatch` 禁止替换。慢 SQL 判定依赖数据量：test 库必须 seed 贴近生产的量，空库上的"快"是假快。

- 它们**都不产生判定**：precheck/sql_perf_check 有发现 ≠ FAIL 结论；没执行，final_gate 照样 NOT_TESTED。对用户汇报口径永远是"巡检发现 N 个问题 + 尚未执行"
- 静态抓不到并发超发、幂等窗口、keep-alive 脏连接、故障注入行为——这些必须执行引擎真实跑。不许声称"静态测过了"
- 服务起不来≠只做静态。能用 `prepare_environment {sut:{command, health_check:{url|port}}}` 让 TestMind 替你拉起的，一律真跑

## 接入任意项目（三步，效率最大化）

1. **喂事实**：你自己读目标项目代码/DDL/OpenAPI，把规则用 `add_facts` 注册（每条必带 source=文件:行；schema/契约文件可直接 `collect_facts` 自动抽）。答不了的才 `ask_user`。
2. **接环境**：起被测服务两条路——自己起好后 `prepare_environment {base_url, db:{kind:sqlite|mysql|none}, tables:[...], proxy_upstream:<它的下游>}`；或让 TestMind 替你起：`prepare_environment {sut:{command, cwd?, env?, health_check:{url|port, timeout_s?}}, db, tables}`（探活成功才算就绪，起不来=BLOCKED，cleanup 自动回收进程树）。DB 给不了连接串就 `none`（API-only，DB 断言记 NOT_TESTED，不许假通过）。
3. **跑闭环**：简单 API 一发 `run_pipeline` 到底；复杂场景用 `generate_cases`/`run_case` 提交完整 case dict（kind: http/sequence/concurrent/fault，支持 setup fixture、{{var}} 模板、db_rows/db_count/db_no_write 断言）。修完缺陷 `add_regression_case` 永久留锚。

故障注入 = FaultProxy 五模式：`ok`（对照必须 2xx，防假绿）/`status`/`delay`/`refuse`/`bad_json`。

## 什么时候必须回归

修了 bug → 生成 Regression Case 永久保留。改了 Service → 先 `analyze_change` 看调用链，禁止只测改动的方法本身。

## 红线

- 不为过测试改测试标准（删测试/放宽 expected），除非证明测试本身错
- 不为覆盖率写无意义测试
- 覆盖率是遗漏探测器，不是正确性证明
- 故障注入/安全测试只在 local/test 环境

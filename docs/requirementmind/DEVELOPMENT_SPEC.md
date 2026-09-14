# DEVELOPMENT_SPEC — TestMind V10 逻辑质量整改（Phase 1–6）

> 编译自 `.requirementmind/*.json`（facts/decisions/assumptions/risk）。每条可追溯到 FACT-xxx / DEC-xxx。
> 读者是完全不了解对话历史的 Coding Agent（R10）。
> 需求源文档：`C:/Users/Administrator/Downloads/TestMind_V10_Logic_Quality_99_Rectification.md`（下称 V10 规范，§N 指其章节号）。
> 目标项目：`e:/workA/A-skill/A-github-skill-mcp/test-mind`（Python MCP skill，下称 `$PROJ`）。

# Goal

把 TestMind 从"接口测试执行器"升级为"专业测试人员级的逻辑质量验证系统"：改动影响面解析（ImpactMind）、业务规则一等公民（RuleMind）、举一反三场景推导（ScenarioMind）、跨接口一致性 Oracle、数据驱动测试（DataTruth + Seed Ledger + 生命周期）、Task 隔离、Logic Bug Lab 20 故障全检出、Quality Score 去假绿。
判定"做对了"的标准：本文档 # Acceptance Criteria 全部通过。§42 Definition of Done 15 条映射到 Gate 检查项 TM-L01..TM-L17（补 TM-L16=§42-11 失败无异常副作用、TM-L17=§42-12 历史回归通过；§42-5 归属关系由 TM-L05 Actor×Ownership 矩阵覆盖），机械可判。

# Current System Facts

- [FACT-001] test-mind 是 Python MCP skill：核心引擎 `testmind/core.py`(90KB)，MCP server `testmind/mcp.py`，TOOLS 字典暴露 30+ 工具（testmind/mcp.py:11-50）
- [FACT-002] `analyze_change` 只返回 git diff 文件清单，无 symbol/endpoint/rule/risk（testmind/git_change.py:34）
- [FACT-003] `java_scan` 只扫 @*Mapping 与 Bean Validation 注解，无调用图（testmind/java_scan.py:20-49）
- [FACT-004] `seed_database` 直接 exec SQL，无 REUSE/REPAIR/CREATE、无 Ledger（testmind/mcp.py:436-441）
- [FACT-005] `Session.reset()` 不清 facts，无 ledger/data_plan/actors/rules/impact_graph 字段（testmind/mcp.py:60-87）
- [FACT-006] Quality Score 假绿：无数据 return 100.0（scripts/v9_score.py:66,122,128）
- [FACT-007] 已具备 HTTP/DB 对拍/sequence/concurrency/failure injection/Evidence/Gate/Regression；无 ImpactMind/RuleMind/ScenarioMind/Cross-Endpoint/DataTruth/Bug Lab
- [FACT-008] V10 规范自带 Phase 1-7 路线图（§34-40）

# Scope

按依赖顺序分 6 个阶段交付（[DEC-001]）：

## Phase 1 — 修假绿（最高优先，§34）
1. 修 CI 路径：`.github/workflows/*` 与 `scripts/verify.ps1` 中失效的脚本路径。
2. `seed_database` 环境校验：destructive 写库前必须 `env_policy.check_prepare` 且 env_class ∈ {local,test}，否则 BLOCKED（沿用 mcp.py:398-402 语义，补齐 seed 侧）。
3. Seed Ledger（§16）：每次测试写操作登记 `{run_id,case_id,dataset_id,table,operation,primary_key,before,after,reason,owned_by_testmind}`；UPDATE 必存 before/after。落盘 `.testmind/ledger/<run_id>.jsonl`。
4. Task facts 隔离（§18）：拆 `reset_runtime` / `reset_task`；`reset_task` 必须清 facts/contract/plan/task_id/consumer_root/ledger/data_plan/actors/rules/impact_graph。新增 Blocking Test：TASK-A(amount<=100) → cleanup → TASK-B(amount<=1000)，断言 TASK-B 中不存在 TASK-A 任何事实。
5. cleanup 数据语义（§17）：PASS → 按 Ledger 逆序回滚 → 重查询证明 DB==before；FAIL → 冻结现场生成 `incident.json`（PK/request/response/db_before/db_after），不自动清理。
6. Mutation harness error 分类（§26）：结果区分 KILLED_BY_ASSERTION / SURVIVED / INVALID_MUTANT / HARNESS_ERROR / TIMEOUT / PATTERN_NOT_FOUND；只有 KILLED_BY_ASSERTION 计入 kill rate（改 `scripts/v9_mutation*.py` 与 `testmind/sim_harness.py`）。
7. Quality Score 去硬编码高分（§27）：所有指标只允许 MEASURED / NOT_MEASURED；无数据 = NOT_MEASURED，禁止 return 100/95/文件存在即满分。重写 `scripts/v9_score.py` 为 `testmind/score.py`，采用 §28 十一维权重 + Blocking 条件（任一 FAIL → BLOCK_MERGE，无论总分）。

## Phase 2 — ImpactMind（§3,4；[DEC-003] 纯标准库启发式）
新模块 `testmind/impact.py`：
- Java 解析器（stdlib 正则+括号匹配）至少支持：Spring Controller 注解、Controller→Service 调用、Service→Service、Service→Mapper、Mapper 注解 SQL（@Select/@Insert/@Update/@Delete）、XML Mapper（`*Mapper.xml` 的 SQL 片段→表）、QueryWrapper/LambdaQueryWrapper 的 `.from(TABLE)`/实体类→表推断、Feign/HTTP client、@Scheduled。
- Impact Graph 节点：Endpoint/ControllerMethod/ServiceMethod/DomainRule/Repository/SQL/Table/Column/Cache/ScheduledJob/EventConsumer/ExternalDependency；边：CALLS/READS/WRITES/USES_RULE/RETURNS/FILTERS_BY/DEPENDS_ON/INVALIDATES（§3.2）。
- 变更符号解析：git diff hunk → 定位所属类.方法 → changed_symbols；沿 CALLS/READS/WRITES 传递闭包 → affected_symbols/endpoints/queries/tables/jobs；shared_rules；risk_level(P0-P2)。
- `analyze_change` 输出升级为 §3.4 新格式（changed_files/changed_symbols/affected_symbols/affected_endpoints/shared_rules/risk_level）。
- Regression Radius R0-R4 计算（§4）：R0 直接修改、R1 共用方法、R2 共用业务规则、R3 共用数据/副作用、R4 历史缺陷关联（从 regression registry 的 reason 匹配 symbol）。
- 失败驱动扩展（§19）：初始只跑 R0+R1+P0 Rule；FAIL 时沿 Graph 扩到 R2/R3。

## Phase 3 — RuleMind + ScenarioMind（§5-10）
新模块 `testmind/rules.py`、`testmind/scenario.py`：
- 规则类型在 range/required/enum/len 之外新增：permission/visibility/ownership/tenant/temporal/state/aggregation/consistency/side_effect；规则结构 = §5.1（rule_id/name/resource/predicate(any/all)/applies_to/surfaces）。
- Actor Model（§7）：OWNER/SAME_TENANT_PEER/OTHER_TENANT/ADMIN/ANONYMOUS 及项目实际角色；**角色必须来源于代码、权限配置、Requirement、历史事实，禁止 AI 凭空生成**（无法取证 → UNKNOWN 挂起）。
- Ownership Model（§8）：SELF/SAME_TEAM/SAME_COMPANY/OTHER_COMPANY/UNASSIGNED/SYSTEM_OWNED。
- TemporalMind（§9）：识别 Java `now>=publishAt`、`isAfter`、`currentTimeMillis()` 与 SQL `<= NOW()` 等时间谓词；生成 T-ε/T/T+ε，ε 按精度（秒/毫秒/分钟/日期）自动决定；时间必须与 Actor/State/Ownership 组合出例。
- StateMind（§10）：从枚举/常量/状态字段解析合法状态与迁移；测合法边/非法边/重复边/越级边/终态再操作，与角色/时间/入口组合。
- ScenarioMind（§6）：Actor×Tenant×Ownership×State×Time×EntryPoint×Operation 推导，**禁止暴力全笛卡尔积**，用 P0 全覆盖 + Pairwise + 关键三元 + 分支覆盖 + 历史缺陷 + 失败驱动扩展。
- 每个生成 Case 必含来源说明（§22）：`{id,reason,impact_path[],rule_id,risk,expected_source}`；缺 reason/expected_source 的 case 拒绝执行。

## Phase 4 — Cross-Endpoint Oracle + Metamorphic（§11,12）
新模块 `testmind/oracle.py`：
- 自动识别同资源入口：detail/list/page/search/count/statistics/export/batch/download。
- 一致性关系：detail 不可见 → list/search/export/count/statistics 均不得出现（除非 Requirement 声明例外）；update 后各入口读回新值；delete 后各入口消失且 count 减少。
- Metamorphic 关系：filter(A∧B)⊆filter(A)；page 并集==visible dataset；页间不重复；total>=页 size；asc/desc 逆序；detail 不可见不得从 export/batch 绕过。

## Phase 5 — DataTruth（§13-15,17）
新模块 `testmind/datatruth.py`：
- 数据需求由场景反推（§13.1 结构）；动态数据量（§14）：page=2,size=20 有数据→min 21；满→40；存在第 3 页→41。禁止固定"默认造 50 条"。
- 处理顺序 inventory → REUSE → REPAIR → CREATE（§13.2-13.5）：REUSE 不改老数据；REPAIR 仅限 TestMind-owned/专用测试租户/临时库；CREATE 只补缺口（需 41 已有 37 → 只造 4）。
- 所有写操作走 Phase 1 的 Seed Ledger；数据前置条件必须真实查询证明（VERIFIED），不是假设。
- Query Logic 专项自动覆盖（§15）：分页 6 情形、Filter 6 情形、Sort 5 情形、Count 与可见性一致。

## Phase 6 — Logic Bug Lab + 三重验证（§23-25；[ASM-001]）
新目录 `examples/logic-bug-lab/`：
- 用 Python 内置轻量 SUT（仿 examples/red-packet：stdlib http.server + sqlite）实现 20 个故意 Bug（§24 清单 1-20），每个 Bug 一个 fixture：buggy 版 + fixed 版。
- Runner `examples/logic-bug-lab/run_lab.py`：对每 fixture 执行三重验证（§25）：Buggy→TestMind FAIL；Fixed→PASS；Reintroduce→FAIL。
- 验收：20/20 检出；任何漏检 = Lab FAIL，禁止"18/20 也算 90 分"。

## MCP 工具面彻底收敛（§29；[DEC-002]）
`testmind/mcp.py` TOOLS 最终只保留 7 个门面：
`analyze_impact` / `plan_verification` / `prepare_verification` / `run_verification` / `replay_failure` / `final_gate` / `cleanup`。
- **收敛时序（消除 big-bang 矛盾）**：Phase 1 第一步先建 7 门面骨架（门面内部转发既有 dispatch 逻辑，旧工具暂留 TOOLS 并标 deprecated）；Phase 2-5 新能力一律挂到对应门面（AC-2 起即可调 `analyze_impact`）；Phase 6 末删除全部旧工具名。
- 旧 32 个工具全部声明去向（无遗漏）：
  - `inspect_project`/`analyze_change`/`scan_java` → `analyze_impact`
  - `collect_facts`/`add_facts`/`build_contract`/`plan_tests`/`generate_cases`/`ask_user`/`answer_question`/`list_unknowns`/`intake_task` → `plan_verification`（TaskBundle 投喂为 `plan_verification{task_bundle}` 参数）
  - `prepare_environment`/`seed_database`/`provision_environment` → `prepare_verification`（容器为 `provision` 参数）
  - `run_case`/`run_suite`/`run_concurrency_tests`/`run_failure_injection`/`run_schema_tests`/`run_scenario_tests`/`run_regression`/`add_regression_case`/`static_precheck`/`sql_perf_check` → `run_verification`（precheck/perf 为可选开关参数，advisory 输出）
  - `get_coverage`/`get_failures`/`get_evidence` → 并入 `run_verification` 与 `final_gate` 返回体
  - `export_handoff` → 并入 `final_gate`（终判后自动导出四件套）
  - `run_pipeline` → 删除（由 7 门面按 §30 编排取代）
- 内部能力含 call graph/rule graph/actor/time/state/data planner/cross-endpoint oracle/mutation/DB（core.py 保留引擎函数，mcp.py 门面内部编排）。
- `cleanup` 门面必须保留现有进程回收语义：`_kill_sut()` 进程树 + `S.srv.shutdown()` + `S.proxy.shutdown()` + `S.reset()`，在此之上叠加 ledger 回滚/现场冻结/reset_task（CH-004）。
- seed 环境策略（CH-005）：`Session` 新增 `env_class` 字段（prepare 时写入）；seed/写库统一收紧为 env_class ∈ {local,test}，staging 不再允许 destructive（明确行为收紧，非沿用）；red-packet example 豁免保留。
- 同步改写全部代码引用点（[ASM-003]）：`tests/*.py`（6 文件）、`testmind/tool_schemas.py`、`testmind/sim_harness.py`、`testmind/dashboard/playbook.py`、`scripts/mcp_smoke.py`、`scripts/v9_*.py` 中经 MCP 调用的部分。
- 同步改写对外文档（CH-002/NF-2）：`skills/testmind/SKILL.md`、`README.md`、`examples/flash-sale/INTERFACE.md`。
- **grep 清零作用域**：仅对 `*.py` 代码文件与 `skills/testmind/SKILL.md` 断言旧工具名 0 命中；`契约文档母版.md`（根目录）为历史文档豁免。
- Gate 检查表替换为 §33 TM-L01..TM-L15 **＋补 TM-L16 失败无异常副作用（§42-11）、TM-L17 历史回归通过（§42-12）**；Major Bug Preventer（§21）：9 类重大逻辑错误任一检出 → P0 FAIL，禁止被普通 case PASS 覆盖。
- 主流程按 §30 新管道（intake→analyze_change→analyze_impact→collect_facts→build_rule_graph→build_contract→plan_verification→plan_data→prepare→inventory→ensure→verify→direct→impacted→actor_time_state→cross_endpoint→regression→final_gate→PASS:cleanup/FAIL:freeze）。
- Coverage 改为 §32 九维（Changed Symbol/Affected Endpoint/Rule Branch/Actor/Ownership/Time Boundary/State Transition/Cross Endpoint/Regression），不再只看 case 数。

# Out of Scope

- Phase 7 真实业务项目 Pilot（[DEC-004] 另行发起；本次只保证 Bug Lab + red-packet 示例闭环）。
- 攻击/渗透/安全对抗（V10 规范明确边界）。
- tree-sitter/javaparser 等外部解析依赖（[DEC-003]）。
- 修改 concise-mind / requirement-mind / 其他 skill。
- 修改 `testmind/core.py` 中 Engine/DBCheck/Evidence/Gate 的既有执行语义（只扩展不重写）。

# Database Changes

被测项目 DB 无 schema 变更。TestMind 自身新增状态文件：`.testmind/ledger/<run_id>.jsonl`（Seed Ledger）、`.testmind/incidents/<case_id>.json`（冻结现场）。依据：[FACT-004]、§16-17。

# API Changes

MCP 工具面：删除 30+ 旧工具，新增 7 门面（见 Scope 收敛节）。JSON-RPC 协议本身不变。入参/出参：`analyze_change` 输出改 §3.4 新格式；`plan_verification` 输出 §31 九类结构。

# State Machine

1. 测试运行：`IDLE → PLANNED → PREPARED → RUNNING → (GATE_PASS → CLEANED) | (GATE_FAIL → FROZEN → REPLAY → (PASS → CLEANED | FAIL → FROZEN))`。
2. 数据条目：`INVENTORIED → REUSED | REPAIRED | CREATED → LEDGERED → VERIFIED → (ROLLED_BACK | FROZEN)`。
3. StateMind 被测状态机：合法边/非法边/重复边/越级边/终态再操作 5 类断言（§10）。

# Validation Rules

- Case 必含 `reason` + `expected_source`，缺 → 拒绝执行并 BLOCKED（§22）。
- 数据前置条件未经真实查询 VERIFIED → final_gate TM-L09 FAIL。
- Actor 无法从代码/配置/Requirement 取证 → 立 UNKNOWN，禁止生成该 actor case（§7）。
- Mutation 结果分类为 HARNESS_ERROR/TIMEOUT/PATTERN_NOT_FOUND 的变异体不计入 kill rate，且 final_gate TM-L14 标记假绿风险。

# Permission Rules

TestMind 自身无新增权限面。被测系统权限验证按 RuleMind visibility/ownership/tenant 规则 + Actor×Ownership 矩阵执行（§7-8）。REPAIR 仅允许作用于 `owned_by_testmind=true` 数据、专用测试租户或临时库（§13.4）。

# Concurrency Rules

沿用现有 run_concurrency_tests 引擎语义，经 `run_verification` 门面暴露；并发 case 必须挂 FACT 来源。

# Idempotency

- `cleanup` 幂等：重复调用不重复回滚（Ledger 条目带 rolled_back 标记）。
- `replay_failure` 幂等：同一 incident 重放前校验现场未被外部修改（before 值比对）。
- 被测系统幂等测试保留现有 P0 风险计划能力。

# Exception Handling

- 执行 FAIL：冻结现场 incident.json，禁止自动清理（§17）。
- Ledger 回滚中途失败：剩余条目保留 + 报告 UNRESOLVED_LEDGER_ENTRIES，final_gate BLOCKED。
- SUT 起不来 / DB 连不上：BLOCKED + 证据，不产生 NOT_TESTED 假 PASS（沿用 mcp.py:523 "No Execution → No PASS"）。

# Impact Analysis

删除旧工具名的反向引用（grep 实测，共 210 处 / 11 文件）：
- `testmind/mcp.py`（106 处：TOOLS 定义+dispatch 分支）— 重写为 7 门面。
- `testmind/tool_schemas.py`（30 处）— 重写 schema 表。
- `testmind/sim_harness.py`（14 处）— 改用门面名。
- `testmind/dashboard/playbook.py`（39 处）— 改用门面名。
- `scripts/mcp_smoke.py`（4 处）— 改用门面名。
- `tests/test_all.py`(9)、`test_export_artifacts.py`(2)、`test_env_policy.py`(1)、`test_gate_bindings.py`(2)、`test_mcp_schema.py`(2)、`test_task_isolation.py`(1) — 全部改用门面名。
- `skills/testmind/SKILL.md` — 重写工具清单与主流程。
- `scripts/v9_score.py` — 由 `testmind/score.py` 取代（保留薄壳转发或删除，CI 同步更新）。
- `scripts/verify.ps1`、`.github/workflows/*` — Phase 1.1 CI 路径修复。
规格未覆盖的调用点若在执行中发现 → 按 DEVELOPMENT_BLOCKER 回流，禁止静默遗漏。

# Compatibility Requirements

[DEC-002] 明确非向后兼容：旧工具名一律删除。`.testmind/` 旧 session 状态文件不迁移，遇到旧格式直接 reset_task。Evidence 目录格式保持兼容（gate_meta.bind_pass_metadata 语义不变）。

# Files Expected To Change

`testmind/mcp.py`、`testmind/core.py`（扩展）、`testmind/tool_schemas.py`、`testmind/sim_harness.py`、`testmind/dashboard/playbook.py`、`testmind/java_scan.py`（并入 impact）、`testmind/gate_meta.py`；新增 `testmind/impact.py`、`testmind/rules.py`、`testmind/scenario.py`、`testmind/oracle.py`、`testmind/datatruth.py`、`testmind/score.py`、`testmind/isolation.py`；`examples/logic-bug-lab/**`；`tests/*.py`；`scripts/mcp_smoke.py`、`scripts/v9_mutation*.py`、`scripts/verify.ps1`、`.github/workflows/*`；`skills/testmind/SKILL.md`。

# Files Forbidden To Change

`testmind/secrets.py`、`testmind/export.py`、`testmind/faultproxy.py`、`examples/red-packet/sut.py`（Bug Lab 自建 SUT，不复用改造）、`skills/requirement-mind/**`、`契约文档母版.md`（根目录，历史文档豁免）。

# Acceptance Criteria

- AC-1（Phase 1）：已知假绿场景清单（封闭枚举，逐条对应回归测试）全部变红：
  1. `v9_score.py:66` 无 drifts → 100.0；2. `:122` 无 drifts → 100.0；3. `:128` 文件存在 → 100.0/50.0；4. `:116` 硬编码 95.0；5. `:59` +5 基础分无实测来源；6. §27 禁令"文件存在→100"类模式全仓 grep 清零（`*.py` 内 `return 100` 且无实测分支）。
  另：seed 写库在 env_class 缺失/production/staging 时 BLOCKED；Ledger 每条 UPDATE 含 before/after；TASK-A/TASK-B 隔离 Blocking Test 通过；mutation 六分类生效且仅 KILLED_BY_ASSERTION 计入 kill rate。
- AC-2（Phase 2）：构造含 SharedService.method 被 4 个接口调用的 fixture，`analyze_impact` 报 4/4 impacted endpoints；§3.4 输出字段齐全。
- AC-3（Phase 3）：给定 visibility 规则 fixture，矩阵充分性机械判据：(a) 每个取证到的 Actor 值与每个 Ownership 值至少各出现 1 次；(b) 4 角色（Owner/SameCompany/OtherCompany/Admin）× T-1/T/T+1 共 12 组合逐条断言期望可见性（§9.3 表）；(c) pairwise 完备性断言：任意两维取值组合至少覆盖 1 次；(d) 总 case 数 < 全笛卡尔积。每 case 带 impact_path 与 expected_source。
- AC-4（Phase 4）：故意让 detail 正确、list 漏时间条件，run_verification 必须 FAIL 且归因 list。
- AC-5（Phase 5）：需 41 已有 37 场景只补 4 条；PASS 后 DB==before 证明查询通过；FAIL 后 incident.json 存在且数据未清理。
- AC-6（Phase 6）：`run_lab.py` 20/20 检出，且每个 fixture 三重验证（Bug→FAIL、Fix→PASS、Reintroduce→FAIL）全通过。**判定层级**：Lab 三重验证的 PASS/FAIL 指 `run_verification` 全 case 断言通过与否，不调用 `final_gate`（TM-L 依赖 Java diff，对 Python fixture 不适用，见 TM-L 适用性规则）。
- AC-7（收敛）：`tools/list` 只返回 7 门面；`python -m pytest tests` 全绿；`scripts/verify.ps1` 全绿；SKILL.md 描述与实现一致。
- AC-8（Gate）：final_gate 输出含 TM-L01..L17 逐项状态与 §28 十一维 MEASURED/NOT_MEASURED 分数；§28 Blocking 任一 FAIL → BLOCK_MERGE。**TM-L 适用性规则**：非 Java 项目（无 git diff 符号可解析）TM-L01..L03 判 NOT_APPLICABLE（非 NOT_MEASURED、非 PASS），且 NOT_APPLICABLE 不阻断 Gate；Gate 状态枚举扩展为 PASS|FAIL|NOT_MEASURED|NOT_APPLICABLE。

# Required Tests

- `tests/test_impact.py`：AC-2 fixture（4/4 endpoint）+ XML Mapper/QueryWrapper/Feign/Scheduled 解析用例。
- `tests/test_rules_scenario.py`：AC-3 矩阵规模断言（pairwise 而非全积）+ 角色无取证→UNKNOWN。
- `tests/test_oracle.py`：AC-4 + metamorphic 各关系正反用例。
- `tests/test_datatruth.py`：AC-5 + 动态数据量推导表驱动用例（21/40/41）。
- `tests/test_task_isolation.py`（改造）：Blocking Test TASK-A/TASK-B。
- `tests/test_score_gate.py`：NOT_MEASURED 语义 + BLOCK_MERGE + TM-L01..L15。
- `tests/test_all.py`（改造）：7 门面端到端（red-packet example）。
- `examples/logic-bug-lab/run_lab.py`：AC-6，接入 CI。

# Known Risks

- [ASM-001] Bug Lab 用 Python SUT 而非 Java：TemporalMind/Java 解析路径的 Bug 只能靠 fixture 单测覆盖，端到端 20 Bug 依赖 Python 侧规则引擎等价实现。缓解：每个 Java 解析规则配独立单测。
- [ASM-003] 210 处调用点改写量大，漏改即自测红。缓解：先全量 grep 旧工具名清零（`python -c` 脚本断言 0 命中）再跑测试。
- 启发式 Java 解析对反射/动态代理失效 → 影响面漏报。缓解：漏报场景归 R2 shared_rules 兜底 + 失败驱动扩展（§19）。

# Frozen Decisions

| id | topic | 决策 | 状态 | replaced_by |
|---|---|---|---|---|
| DEC-001 | 交付范围 | 全量 Phase 1-6；Phase 7 Pilot 留接口另行执行 | FROZEN | — |
| DEC-002 | MCP 工具面收敛 | 新增 7 门面 + 删除旧工具（彻底收敛，破坏性） | FROZEN | — |
| DEC-003 | Java 调用图解析 | 纯标准库启发式（正则+括号匹配，覆盖 §3.3 清单） | FROZEN | — |
| DEC-004 | Pilot 目标项目 | 本次跳过（AI 默认，OPTIONAL） | FROZEN | — |

# Open Questions

无 BLOCKING/IMPORTANT 遗留。Q-004（OPTIONAL）默认值：本次跳过 Pilot，[DEC-004]。

# Development Gate

见 `.requirementmind/gate.json`：目标 READY_FOR_DEVELOPMENT；硬门槛 blocking_questions=0 / blocking_conflicts=0 / critical_assumptions=0 / unvalidated_high_risks=0；risk tier=FOCUSED（compatibility 专项）。

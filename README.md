# TestMind

面向 AI Coding Agent 的业务测试决策与执行系统：基于可证明的业务事实设计测试，用受控数据真实执行，用证据判定 PASS/HOLD/FAIL/BLOCKED。零 LLM API Key（宿主 AI 负责推理，Core 确定性优先）。

## 快速开始

```powershell
cd testmind
python -m unittest discover -s tests        # 自测 + 红队 + 假绿防线
python examples/red-packet/e2e.py           # 红包全闭环：78 case（边界/负例/关系/幂等/并发/时间/状态机/故障/回归）
python scripts/mcp_smoke.py                 # MCP 全链冒烟（7 门面按 §30 管道编排）
powershell -File scripts/verify.ps1         # 一键全量验证
python testmind/mcp.py                      # MCP Server（stdio JSON-RPC, UTF-8）
```

## 看板（只读，本机）

```powershell
python scripts/dashboard.py --open      # http://127.0.0.1:8901
python scripts/dashboard.py --check     # 不起服务，只自检数据层
```

回答三个问题：**今天该处理什么 / 从哪个入口进 / 进来之后还要顺手测什么**。

- **总览** —— 有效运行、门禁通过率（口径 `PASS/(PASS+FAIL)`，非判定态与空壳目录不进分母）、用例数、回归锚
- **测试入口分类** —— 4 个入口组覆盖全部 7 个门面工具，每个入口点开是「举一反三」：一条现象该扩展出哪些测试维度，以及依据
- **举一反三路由** —— 丢一句模糊需求（"用户付款成功但订单停在待支付"）指到入口；**没把握就不指**，空结果比乱指好
- **资产库 / 项目动态** —— 回归锚、技能、规则、示例；`reports/` 时间线与近 30 天趋势
- **测试纪律** —— 四条硬规则、状态词表、Q1–Q5 质量维度、10 类缺陷注入、故障注入五模式

只读、零依赖、仅监听 `127.0.0.1`；不进 MCP 工具面（宿主 AI 用 7 门面，人用看板）。

## 用 Cursor / Codex 测你自己的项目（三步）

1. **喂事实** —— 宿主 AI 读你的代码/DDL/OpenAPI：文件类走 `plan_verification {schema_sql, openapi}`，代码规则走 `plan_verification {facts:[{topic,statement,source}]}`（source 必填，无出处拒收）
2. **接环境** —— 起被测服务两条路：自己起好后 `prepare_verification {env:{base_url, db:{kind:sqlite|mysql|none,...}, tables, proxy_upstream}}`；或让 TestMind 替你起 `prepare_verification {env:{sut:{command, health_check:{url|port}}, db, tables}}`（health 探活成功才算就绪，起不来=BLOCKED，cleanup 回收进程树）。MySQL 走 pymysql，DB 给不了就 `none`（API-only，DB 断言诚实记 NOT_TESTED）
3. **跑闭环** —— 简单 API 走 `plan_verification {contract, plan}` → `run_verification {}`；复杂场景 `plan_verification {cases:[...]}` 提交完整 case（kind: http/sequence/concurrent/fault，setup fixture + {{var}} 模板 + db_rows/db_count/db_no_write 断言）；修完缺陷 `run_verification {add_regression:{case,reason}}` 永久留锚

## 静态预检：改码未部署窗口期能做什么

`run_verification {precheck:{schema_sql, statements}}`（statements=宿主 AI 从改动代码提取的 INSERT/UPDATE，source 必填）只抓**从 DDL+SQL 能确定性推出**的缺陷：INSERT 列数错位（列错位高危形态）、未知列、NOT NULL 漏写/写 NULL、数值字面量越 CHECK 范围、字符串进数值列（warn）、事实口径冲突。结果落 `reports/<run_id>/precheck.json`，占位符/函数表达式不检查、零误报优先。

**边界（诚实声明）**：静态发现≠判定。precheck 报 error 后 `final_gate` 没有真实执行照样 NOT_TESTED——并发超发、幂等窗口、keep-alive 脏连接、故障注入行为这些运行时缺陷只有执行引擎能抓。起不来的服务别只用它交差；能让 `sut` 参数拉起的一律真跑。

## SQL 性能巡检：慢 SQL + 等价更快写法对拍

`run_verification {perf:{queries, db?}}`（只读强制：仅 SELECT/WITH/EXPLAIN，写库语句直接拒绝）：
- **慢 SQL**：逐条计时（取 `runs` 次的 min），超 `threshold_ms`（默认 1000，可按条覆盖）报 `slow_sql`，慢语句自动附 EXPLAIN 执行计划进证据
- **替代写法对拍**：`compare_sql` 候选写法先验**结果集一致**（多重集合比较，窗口内），一致且实测更快才报 `faster_equivalent`（如 10s→1s）；不一致报 `data_mismatch` error **禁止替换**——不许拿"更快"换"不对"
- **诚实边界**：巡检是 advisory，不进 `final_gate`；慢 SQL 判定依赖数据量，test 库要 seed 贴近生产的量，空库上的"快"是假快；换写法的决策必须业务确认后走正常回归

## 结构

- `testmind/core.py` — Facts（CONFIRMED/DERIVED/UNKNOWN/CONFLICT）、契约、规划器（边界/负例）、静态预检（DDL vs 写库语句确定性对拍）、数据工厂、**通用 Engine**（HTTP+DB 对拍+证据）、Final Gate、回归 Registry、runner 探测
- `testmind/faultproxy.py` — stdlib 反向代理故障注入（§24 WireMock 的零依赖替代：ok/status/delay/refuse/bad_json，ok 对照组防假绿）
- `testmind/mcp.py` — 7 门面（§29 工具面收敛：analyze_impact / plan_verification / prepare_verification / run_verification / replay_failure / final_gate / cleanup；原 32 个细粒度工具下沉为 `_do_*` 私有函数），统一信封 `{status,summary,evidence,facts,unknowns,next_actions}`；`run_verification` 失败自动归因（TEST_DEFECT 补参重跑），`final_gate` 接口覆盖台账（写端点未全覆盖/端点全集未知 → PASS 降 HOLD）
- `testmind/triage.py` — 失败三分法归因：EXPECTED 拒绝 / TEST_DEFECT（缺必填参数→自动修参，值只许来自同 plan 出处）/ PRODUCT_BUG，治"500 一律 PASS 与 4 个 BUG 同时成立"
- `testmind/coverage.py` — 接口覆盖台账：声明端点全集（OpenAPI/facts/Java 调用图三源并集）− 已执行触达，未测逐条 NOT_TESTED
- `testmind/kv.py` — Redis/KV 副作用断言（kv_rows/kv_count/kv_no_write，before/after diff，stdlib RESP 实现）
- `testmind/impact.py` — Java 高风险调用点模式扫描（§29：公司解析传 null、绕过统一解析器等，命中点进 pattern_findings）
- `examples/red-packet/` — 演示 SUT（状态机/幂等/风控依赖/可控时钟）+ e2e 闭环
- `skills/testmind/SKILL.md` — 测试纪律 + 接入三步法
- `testmind/dashboard/` — 只读看板（`playbook` 知识层 / `aggregate` 数据层 / `server` + `index.html` 展示层），零依赖
- `tests/test_all.py` — 自测（含红队：冲突阻断/无执行不 PASS/注错必 FAIL/假绿防线）
- `regression/cases.json` / `reports/<run_id>/` — 回归锚 / 逐 case 证据

## Adapter 策略

外部 runner 三态接入（AVAILABLE / UNAVAILABLE / SKIPPED_WITH_REASON），见 `core.scan_runners()`。本机实测：Schemathesis ✅ 真跑；**Karate ✅**（karate-core 1.4.1 + 85 jar 预取于 `tools/karate/lib`，JDK17=`E:\jdk17` 真执行）；**Testcontainers 语义 ✅**（`docker_provision`/`docker_release`：VirtualBox docker-vm 的 daemon `tcp://127.0.0.1:2375`，现供真 MySQL8+pymysql 对拍→销毁，见 `scripts/full_power_check.py`）；JaCoCo CLI 在位待 Java SUT。WireMock/Keploy/CATS/RESTler/DB-Rider 维持 UNAVAILABLE（职能已被 FaultProxy/DBCheck 覆盖或环境不支持，理由见 probe 输出）。

**Docker 手册口径**（`E:\workA\本人电脑环境\AI-环境手册.md` §2.8）：VM 不随开机自启，探测失败先 `VBoxManage startvm "docker-vm" --type headless`；宿主 MySQL 端口需热加转发 `VBoxManage controlvm "docker-vm" natpf1 "mysql,tcp,,3306,,3306"`。

## 硬规则

无需求→无预期；未执行→无 PASS；无证据→无完成；业务数据禁随机。状态词汇表只有 PASS/FAIL/HOLD/BLOCKED/NOT_TESTED/TOOL_ERROR/SKIPPED_WITH_REASON。

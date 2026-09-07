# TestMind

面向 AI Coding Agent 的业务测试决策与执行系统：基于可证明的业务事实设计测试，用受控数据真实执行，用证据判定 PASS/HOLD/FAIL/BLOCKED。零 LLM API Key（宿主 AI 负责推理，Core 确定性优先）。

## 快速开始

```powershell
cd testmind
python -m unittest discover -s tests        # 自测 + 红队 + 假绿防线
python examples/red-packet/e2e.py           # 红包全闭环：78 case（边界/负例/关系/幂等/并发/时间/状态机/故障/回归）
python scripts/mcp_smoke.py                 # MCP 全链冒烟（run_pipeline 一发到底）
powershell -File scripts/verify.ps1         # 一键全量验证
python testmind/mcp.py                      # MCP Server（stdio JSON-RPC, UTF-8）
```

## 用 Cursor / Codex 测你自己的项目（三步）

1. **喂事实** —— 宿主 AI 读你的代码/DDL/OpenAPI：文件类走 `collect_facts {schema_sql, openapi}`，代码规则走 `add_facts {facts:[{topic,statement,source}]}`（source 必填，无出处拒收）
2. **接环境** —— 起被测服务两条路：自己起好后 `prepare_environment {base_url, db:{kind:sqlite|mysql|none,...}, tables, proxy_upstream}`；或让 TestMind 替你起 `prepare_environment {sut:{command, health_check:{url|port}}, db, tables}`（health 探活成功才算就绪，起不来=BLOCKED，cleanup 回收进程树）。MySQL 走 pymysql，DB 给不了就 `none`（API-only，DB 断言诚实记 NOT_TESTED）
3. **跑闭环** —— 简单 API 一发 `run_pipeline`；复杂场景 `generate_cases` 提交完整 case（kind: http/sequence/concurrent/fault，setup fixture + {{var}} 模板 + db_rows/db_count/db_no_write 断言）；修完缺陷 `add_regression_case` 永久留锚

## 静态预检：改码未部署窗口期能做什么

`static_precheck {schema_sql, statements}`（statements=宿主 AI 从改动代码提取的 INSERT/UPDATE，source 必填）只抓**从 DDL+SQL 能确定性推出**的缺陷：INSERT 列数错位（列错位高危形态）、未知列、NOT NULL 漏写/写 NULL、数值字面量越 CHECK 范围、字符串进数值列（warn）、事实口径冲突。结果落 `reports/<run_id>/precheck.json`，占位符/函数表达式不检查、零误报优先。

**边界（诚实声明）**：静态发现≠判定。precheck 报 error 后 `final_gate` 没有真实执行照样 NOT_TESTED——并发超发、幂等窗口、keep-alive 脏连接、故障注入行为这些运行时缺陷只有执行引擎能抓。起不来的服务别只用它交差；能让 `sut` 参数拉起的一律真跑。

## SQL 性能巡检：慢 SQL + 等价更快写法对拍

`sql_perf_check {queries, db?}`（只读强制：仅 SELECT/WITH/EXPLAIN，写库语句直接拒绝）：
- **慢 SQL**：逐条计时（取 `runs` 次的 min），超 `threshold_ms`（默认 1000，可按条覆盖）报 `slow_sql`，慢语句自动附 EXPLAIN 执行计划进证据
- **替代写法对拍**：`compare_sql` 候选写法先验**结果集一致**（多重集合比较，窗口内），一致且实测更快才报 `faster_equivalent`（如 10s→1s）；不一致报 `data_mismatch` error **禁止替换**——不许拿"更快"换"不对"
- **诚实边界**：巡检是 advisory，不进 `final_gate`；慢 SQL 判定依赖数据量，test 库要 seed 贴近生产的量，空库上的"快"是假快；换写法的决策必须业务确认后走正常回归

## 结构

- `testmind/core.py` — Facts（CONFIRMED/DERIVED/UNKNOWN/CONFLICT）、契约、规划器（边界/负例）、静态预检（DDL vs 写库语句确定性对拍）、数据工厂、**通用 Engine**（HTTP+DB 对拍+证据）、Final Gate、回归 Registry、runner 探测
- `testmind/faultproxy.py` — stdlib 反向代理故障注入（§24 WireMock 的零依赖替代：ok/status/delay/refuse/bad_json，ok 对照组防假绿）
- `testmind/mcp.py` — 29 工具（§37 全 21 + add_facts/ask_user/answer_question/run_pipeline/generate_cases/run_scenario_tests/static_precheck/sql_perf_check 等），统一信封 `{status,summary,evidence,facts,unknowns,next_actions}`
- `examples/red-packet/` — 演示 SUT（状态机/幂等/风控依赖/可控时钟）+ e2e 闭环
- `skills/testmind/SKILL.md` — 测试纪律 + 接入三步法
- `tests/test_all.py` — 自测（含红队：冲突阻断/无执行不 PASS/注错必 FAIL/假绿防线）
- `regression/cases.json` / `reports/<run_id>/` — 回归锚 / 逐 case 证据

## Adapter 策略

外部 runner 三态接入（AVAILABLE / UNAVAILABLE / SKIPPED_WITH_REASON），见 `core.scan_runners()`。本机实测：Schemathesis ✅ 真跑；**Karate ✅**（karate-core 1.4.1 + 85 jar 预取于 `tools/karate/lib`，JDK17=`E:\jdk17` 真执行）；**Testcontainers 语义 ✅**（`docker_provision`/`docker_release`：VirtualBox docker-vm 的 daemon `tcp://127.0.0.1:2375`，现供真 MySQL8+pymysql 对拍→销毁，见 `scripts/full_power_check.py`）；JaCoCo CLI 在位待 Java SUT。WireMock/Keploy/CATS/RESTler/DB-Rider 维持 UNAVAILABLE（职能已被 FaultProxy/DBCheck 覆盖或环境不支持，理由见 probe 输出）。

**Docker 手册口径**（`E:\workA\本人电脑环境\AI-环境手册.md` §2.8）：VM 不随开机自启，探测失败先 `VBoxManage startvm "docker-vm" --type headless`；宿主 MySQL 端口需热加转发 `VBoxManage controlvm "docker-vm" natpf1 "mysql,tcp,,3306,,3306"`。

## 硬规则

无需求→无预期；未执行→无 PASS；无证据→无完成；业务数据禁随机。状态词汇表只有 PASS/FAIL/HOLD/BLOCKED/NOT_TESTED/TOOL_ERROR/SKIPPED_WITH_REASON。

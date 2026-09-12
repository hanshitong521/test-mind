# ROUND 20260911c — 质量加固：逻辑缺陷 / 代码简洁 / SQL 效率 / 根因定位

> **性质**：第三轮 · 质量维度加固 + 一键自跑通。
> **方向（owner 决定）**：**不做性能/负载压测**。聚焦 **逻辑 bug、代码简洁度、高效 SQL、接口慢/卡顿、根因定位链**（对应 `问题文档.md` §0.1 Q1–Q5）。
> **附加要求**：路径必须能自己跑通（为后续远程/无人值守调用做准备）。
> **测量环境**：Windows 10 19045 · Python 3.11.9（装了 pymysql+schemathesis）/ 3.13.14（托管，裸）· Node 22.22.2 · JDK17 · Karate 1.4.1。

---

## 0. 一句话结论

本轮**修掉 6 类真实缺陷**（2 个逻辑 bug、1 个误导性诊断、10 处句柄泄漏、12 个死 import、1 处数据耦合 flaky 断言），新增**跨平台自选解释器的一键全链验证**。**全部门禁复跑绿**：`verify-all = PASS`、`verify.ps1 = VERIFY PASS`、peak-gate `TM0–TM9 rollup=PASS`。改动**未提交**。

---

## 1. 修复清单（按性质）

| # | 类别 | 缺陷 | 根因 | 影响 | 修复 |
|---|---|---|---|---|---|
| 1 | **逻辑 bug** | `resolve_schemathesis()` 永远解析失败 | schemathesis **4.x 删了 `__main__.py`**，`python -m schemathesis` 直接 ModuleNotFoundError；且 console script `schemathesis.exe` 在 `Scripts\` 下、**不在 PATH**，`which()` 也找不到 | 装了 schemathesis 的机器仍被判 `UNAVAILABLE` → schema 测试**永久降级**、`mcp_smoke.py` **恒退出 1** | 补两条候选：①当前解释器同级 `Scripts/bin` 的 console script；②`-c "from schemathesis.cli import schemathesis as _s; _s()"` 的 entry-point 回退（v4 正确入口是 `schemathesis.cli:schemathesis`） |
| 2 | **逻辑 bug** | `python -m unittest tests.X` 命中**错的 `tests` 包** | site-packages 里存在 **ultralytics 的顶层 `tests` 包**（正规包，带 `__init__.py`），而本仓 `tests/` 无 `__init__.py`（namespace 包）→ 被抢占 | peak-gate **TM4–TM8 五门全 FAIL**（`ModuleNotFoundError: No module named 'tests.test_x'`） | 新增 `tests/__init__.py`（含原因注释），cwd 优先于 site-packages；两种调用方式（`discover -s tests` 与 `unittest tests.X`）均验证通过 |
| 3 | **定位误导** | 报 `docker cli not found`，但 CLI 明明装了 | 手册口径 CLI=`C:\Docker\bin\docker\docker.exe`，但只认 `TESTMIND_DOCKER_CLI` env 或 PATH 上的 `docker` | 诊断把人带偏（去查"没装"，其实该查"daemon 没起"） | 新增 `resolve_docker_cli()`：env → PATH → **手册固定落点**兜底；探测与执行共用同一解析。现在准确报 `docker daemon unreachable at tcp://127.0.0.1:2375 …` + 启 VM 提示 |
| 4 | **资源泄漏** | 10 处 `open()` 未关闭（`ResourceWarning`） | `json.load(open(...))` / `open(...).read()` 一类写法 | 长跑句柄堆积、测试日志噪声 | 全部改 `with open(...)`：`env_registry` / `java_scan` / `sim_harness` / `mcp`(×3) / `benchmark/run_benchmark` / `2 scripts` / `5 tests` → **ResourceWarning 归零** |
| 5 | **代码简洁** | 12 个未使用 import | 历史演进遗留 | 噪声、误导读者 | pyflakes 在本机装不上（沙箱拦网）→ **自写 AST 审计 `audit_unused.py`** 找净（仅剩 1 个 `__future__` 误报，属正常） |
| 6 | **flaky 断言** | `verify_brain_registration.py` R8/R10 假红 | 硬编码检索词 **"口径"**，但真实记忆池 `shejiuPro.jsonl` 里已无该词 → `count=0` | 环境/数据一变就红，掩盖真实状态 | 改为**运行时从池里现取检索词**（`probe_term()`）；"必须命中真实池"的标准**不变**，只去掉数据耦合 |
| 7 | **陈旧输出** | `verify.ps1` 打印写死的 `(32 tests)` / `(85 cases)` | 计数未随规模更新（实为 **80 / 114**） | 误导读者 | 去掉会腐烂的数字 |

---

## 2. 新增：一键全链 + 自选解释器（`scripts/verify-all.py`）

**要解决的问题**：`verify.ps1` 假定 PATH 上的 `python` 恰好装了 `pymysql`/`schemathesis`。本机 PATH 首位是**托管 3.13.14（裸的）**，于是 schema 测试降级、冒烟非零退出 —— 这是**环境耦合，不是代码错**。

**做法**：
1. **自选解释器**：当前解释器优先 → 常见落点 → 挑第一个 `pymysql + schemathesis` 都可用的；退而求其次"有一个即可"；全无则用当前。
2. **传导解释器**：把所选解释器目录（及同级 `Scripts/bin`）顶到子进程 `PATH`，保证 **peak-gate 内部的 `python`** 也命中同一个。
3. 顺序执行：`unittest → e2e → mcp-smoke → gate-envelope → peak-gate → brain-register → compress-claims`；汇总 rollup，任一 FAIL 非零退出。
4. `--quick` 跳过 e2e/peak-gate 重活。

**实测（故意用裸的托管 3.13.14 启动）**：
```
# interpreter : C:\...\Python311\python.exe     ← 自动选中
# pymysql : yes   # schemathesis: yes
unittest PASS · e2e PASS · mcp-smoke PASS · gate-envelope PASS
peak-gate PASS · brain-register PASS · compress-claims PASS
# VERIFY-ALL = PASS
```

---

## 3. 验收矩阵（09-11 本机复跑）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 自测 + 红队 | `python -m unittest discover -s tests` | **80 / 80 OK**（3.11 与 3.13.14 双跑） |
| 红包端到端 | `python examples/red-packet/e2e.py` | **114 cases · final=PASS** |
| MCP 全链冒烟 | `python scripts/mcp_smoke.py` | **exit 0**；`run_schema_tests:PASS`；93 results / 0 fail；32 工具 |
| 门禁信封 | `node --test scripts/test_peak_gate_envelope.mjs` | PASS |
| **peak-gate** | `node scripts/peak-gate.mjs` | **TM0–TM9（含 TM8b）11 门全 PASS，rollup=PASS** |
| Karate 真跑 | `scripts/full_power_check.py` | **karate=PASS** |
| Docker+MySQL 对账 | 同上 | **BLOCKED**（daemon 未起，VM 需手动启）— 三态诚实，非失败 |
| 栈级视图 | `shared/scripts/peak-stack-view.mjs` | `testmind=PASS stale=no`；`peak_core=FAIL`（RM/Brain 无信封，真实状态） |
| 栈级契约测试 | `node --test test_peak_stack_view.mjs` | **8 / 8 pass** |
| brain 注册 | `scripts/verify_brain_registration.py` | **R1–R14 全 PASS** |
| context-compress 声明 | `scripts/verify_context_compress_claims.py` | **C0–C15 全 PASS（FAILED: 0）** |
| 一键（PS） | `verify.ps1` | **VERIFY = PASS** |
| 一键（跨平台） | `scripts/verify-all.py` | **VERIFY-ALL = PASS** |
| 静态编译 | `python -m compileall -q` | 0 错 |
| 资源泄漏 | `ResourceWarning` 计数 | **0** |
| 死 import | `audit_unused.py` | 12 → **0**（余 1 为 `__future__` 误报） |

---

## 4. 诚实边界（没做/做不到）

- **性能压测**：按 owner 决定**不做**（k6/并发 2xx 不算验收项，见 `问题文档.md` §0.1）。
- **Docker+MySQL 真对账**：BLOCKED —— VirtualBox `docker-vm` 不随开机自启，需手动 `VBoxManage startvm "docker-vm" --type headless`。**代码侧已能准确定位到这一步**。
- **栈级 `peak_core=FAIL`**：requirement_mind / token_mind / brain 未交门禁信封，shejiuPro 未装。**真实状态，不代位凑绿**。
- **`schemathesis` 只在 Python 3.11**：托管 3.13.14 无依赖 —— 现在靠 `verify-all.py` 自选解释器解决，而非假装可用。
- **模型**：TestMind 本体**零 LLM 依赖**（宿主 AI 推理，Core 确定性）。模型选择只影响"谁来驱动它"，不影响本轮的测试结论。

---

## 5. 改动文件

**修改 23 个**：`testmind/{core,mcp,env_registry,env_policy,gate_meta,java_scan,sim_harness}.py`、`tests/{test_all,test_export_artifacts,test_gate_bindings,test_mcp_schema,test_openapi_operation,test_secrets,test_task_isolation}.py`、`scripts/{verify.ps1,verify_brain_registration.py,verify_context_compress_claims.py,diag_context_compress_sandbox.py,full_power_check.py}`、`examples/red-packet/sut.py`、`benchmark/{run_benchmark.py,playwright/buggy_sut.py}`、`问题文档.md`（前一轮）。

**新增 2 个**：`tests/__init__.py`（防 `tests` 包被遮蔽）、`scripts/verify-all.py`（跨平台一键 + 自选解释器）。

> 均**未提交**（`git status`：23 M + 2 ??）。

# TestMind 整改轮次报告 — 2026-09-10（merge 解阻 + INV-005 + TM3 真跑）

> 依据：`问题文档.md`（缺陷 SSOT + F1–F10 满分规格）
> 规则遵循：审计 §1–§7 事实**未改动**；本轮新结论全部写入本报告（§8.2「新结论写入新附录」）。
> 状态：本仓 test-Mind 门禁 **rollup=PASS**（TM0–TM9 全绿）；**PILOT 定性不变**，远未到 9 分。

---

## 0. 一句话结论

仓库原本处于**半完成的 git merge**、核心代码带冲突标记、全仓 import 失败（跑出 **0** 个测试）。
本轮解阻并修复 5 类缺陷后：**单测 80/80**、**INV-005 信封契约 5/5**、**peak-gate rollup=PASS / incomplete=0**、
**red-packet e2e 114/114 + schemathesis PASS**。合并收尾**未提交**（待确认）。

---

## 1. 头号发现：半完成的 merge 使整仓不可用

| 事实 | 证据 |
|------|------|
| `test-Mind/.git/MERGE_HEAD` 存在（合 `origin/master`=gitee `5eca373` → 本地 `master`=`github/master` `0f085d1`） | `git status --porcelain` → `UU testmind/core.py` |
| `testmind/core.py:1507` 残留 `<<<<<<< HEAD / ======= / >>>>>>> origin/master` | `ast.parse` 报 `SyntaxError: expected 'except' or 'finally' block` |
| 后果：12 个测试模块全部 ImportError | `unittest discover` → `Ran 12 tests / errors=12`，**实际执行 0 个用例**（文档"79 tests"为旧数） |

**解析策略＝两边意图并集**（非取一侧）：

```python
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args=cli, returncode=124, stdout="", stderr="docker timeout")
    except FileNotFoundError:
        # 明确区分「CLI 不存在」与「daemon 不可达」：127 + not found → SKIPPED_WITH_REASON 而非 BLOCKED
        return subprocess.CompletedProcess(args=cli, returncode=127, stdout="", stderr="docker cli not found")
    except OSError as e:   # 本机没装 docker（CI/macOS 常态）：fail-open，探测=UNAVAILABLE，绝不崩闭环
        return subprocess.CompletedProcess([cli, *args], returncode=1, stdout="", stderr=repr(e))
```

理由：HEAD 侧保证 `docker_provision` 的「CLI 缺失 → `SKIPPED_WITH_REASON`」三态语义不自 TD；origin 侧保证 P1-01 的 fail-open（不崩闭环）。二者不冲突，缺一即退化。

**合并带入的真资产**（不应丢弃）：`benchmark/`（Playwright buggy/real SUT 对照打分板）、`examples/flash-sale/`（秒杀 SUT+e2e+红队）、`examples/generic/`、`tests/test_all.py::TestRunnerFailOpen`。

---

## 2. 本轮修复清单（逐项可复现）

### 2.1 P1-02 — runner 探针与执行路径不一致 + 硬编码盘符（`testmind/core.py`）

旧实现：`scan_runners()` 用 `py -3` **探测**，`run_schema_tests()` 却用写死的
`C:\Users\Administrator\AppData\Local\Programs\Python\Python311\Scripts\schemathesis.exe` **执行**。
"探针说 AVAILABLE、执行落在另一条路径/另一个解释器上" 正是假绿温床，且违反 **INV-001**（无盘符）。

修复：新增 `resolve_schemathesis()` / `schemathesis_status()`，**探测与执行共用同一解析**：

1. `TESTMIND_SCHEMATHESIS`（显式：指解释器 → `-m schemathesis`；否则当可执行文件）
2. `shutil.which("schemathesis")`
3. `sys.executable -m schemathesis`

全不可用 → `None` → 诚实三态 `UNAVAILABLE → SKIPPED_WITH_REASON`。命令改 **list 形式**（不经 shell），
`OSError` → `SKIPPED_WITH_REASON`。

### 2.2 `TestRunnerFailOpen` 是环境耦合 flaky（`tests/test_all.py`）

该用例断言「schemathesis 不可用 → `SKIPPED_WITH_REASON`」，却依赖**本机恰好没装 runner**：
Linux CI 绿、装了 schemathesis 的开发机必红。本机实际**有**（`shutil.which` 找得到，
PowerShell `where` 不显示 3.11 的 Scripts 目录 → 所以肉眼容易漏判）。

修复：改用注入缝 `runner_prefix` 显式构造"不可用"，并**新增**一条「runner 不可执行」用例 —— 变成任意平台确定性，
且覆盖面比原来更宽（原写法只覆盖"环境没装"这一条路径）。

### 2.3 P1-20 — Windows 用 TM1 代位 TM3（`scripts/peak-gate.mjs`）

旧：

```js
const tm3 = process.platform === "win32" ? tm1 : run("python", ["examples/red-packet/e2e.py"], ...);
```

即 Windows 上把"单测过了"当成"端到端过了"。改为**所有平台一律真跑** e2e（`TESTMIND_DOCKER_CLI=/bin/false`）。

### 2.4 INV-005 — 门禁信封字段完整（`shared/scripts/peak-gate-lib.mjs` + `scripts/peak-gate.mjs`）

- 信封补齐：`component_version / commit / contract_version / contract_hash / input_hash / evidence / measured_at`。
- 新增 `missingEnvelopeFields()`；**blocking gate 信封不完整 → 视同 FAIL**（信封是真门禁，不是装饰）。
- 落盘 `reports/peak-gate-envelope.json`（INV-010：可移植 relative_ref）。
- `peak-gate.mjs` 的 in-file **fallback 与 shared 库行为对齐**（否则"有 shared 严、没 shared 松"又是一种假绿）。
- 契约缺失时字段留 `null` 而不静默补值 → 由 INV-005 显性暴露（顺带把 P1-04 / F9 的 vendor 缺口**变成可见**而非沉默）。
- 新契约测试 `scripts/test_peak_gate_envelope.mjs`（node --test，5 例），已接入 `run_peak_verify.py` 与 CI。

### 2.5 TM3 真正变红的根因 —— 两个**真实的、pre-existing 的** SUT 缺陷

> 这两个缺陷在本轮之前就存在（`reports/20260830-184303-7c580e` 即有同样 FINAL=FAIL），**非本轮引入**。

**D1 — SUT 不接受 absolute-form 请求目标（违 RFC 9112 §3.2.2）**

给 SUT 挂 `log_request` 抓真实请求行，看到 schemathesis 发的**不是** `GET /red-packets`，而是：

```
RESP GET    'http://127.0.0.1:18101/red-packets'       -> 404
RESP POST   'http://127.0.0.1:18101/scheduler/tick'    -> 404
```

而 SUT 用 `self.path == "/red-packets"` 精确匹配 → 全部落到 `{"error": "route"}` 404。
于是 schemathesis 报"Unsupported method GET returned 404, expected 405"——**SUT 明明有 405+Allow 却被判不合格**。
修：`examples/red-packet/sut.py` 增 `_norm_path()`（absolute-form → origin-form，并剥离 query），
`do_POST` / `do_GET` / `_unsupported` 首行归一化。**仅此一改，4 个 finding → 0**（Coverage 阶段全绿）。

**D2 — OpenAPI `name` 的 `pattern` 是"假契约"**

```python
"pattern": "^[^\\u0000-\\u001f\\u007f]*\\S[^\\u0000-\\u001f\\u007f]*$"
```

看起来是"无控制字符 + 至少一个非空白"，实测**根本不排除控制字符**：

```
'a\x16b' -> pattern_match=True    'a\x02b' -> True    'a\x7fb' -> True
```

原因：中间那个 `\S` 是**消费型**元素，而 `\x16` 不是空白 → `\S` 自己就把控制字符吞了；
两侧的否定字符类管不到它。schemathesis 按 `pattern` 生成样本 → 生成含控制字符的 name →
SUT 按自己的规则**正确拒收** → 报 "API rejected schema-compliant request"——**看起来像 SUT 的锅，其实是契约假**。

修（全仓仅此一处）：

```python
"pattern": "^[^\\u0000-\\u001f\\u007f]*[^\\s\\u0000-\\u001f\\u007f][^\\u0000-\\u001f\\u007f]*$"
```

校验方式（可复用）：把 **SUT 自己的校验谓词**与 `re.fullmatch(pattern, v)` 对**同一批值**跑，
两边结论必须一致；不一致即"假契约"。

---

## 3. 验收证据（本机，2026-09-10）

```powershell
$py="C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
cd E:\workA\A-skill\test-Mind
```

| # | 命令 | 结果 |
|---|------|------|
| 1 | `& $py -m unittest discover -s tests -q` | `Ran 80 tests` → **OK** |
| 2 | `node --test scripts/test_peak_gate_envelope.mjs` | **5 pass / 0 fail** |
| 3 | `& $py examples/red-packet/e2e.py` | `final: "PASS"`，`counts.PASS=114`，`schema_tests.status="PASS"`，exit 0 |
| 4 | `node scripts/peak-gate.mjs` | `rollup=PASS`，TM0–TM9 全 PASS，`incomplete=0`，exit 0 |

信封证据：`reports/peak-gate-envelope.json`（`commit=0f085d1`，`component_version=1.2.0`，
`contract_version=1`，`contract_hash=eb5402feb726de5c`）。

---

## 4. 诚实边界（不得据此宣称更高分）

- **F2 未达成**：Linux CI 仍未实跑（P1-06 依旧 `NOT_RUN`）；本机 Windows 只能说与 Linux 同一条命令路径真跑。
- **F1/F6 未达成**：仍无真实 Java/Spring consumer 全链，`shejiuPro` INT0–INT2 仍空（M1 未做）。
- **F3 部分达成**：INV-005 信封已是真门禁；但 **INV-006 STALE 的栈级聚合视图/UI 仍缺**（F3 后半段）。
- **F5/F7/F10 未动**：MCP 仍 ~32 工具未收敛到 10–12；k6/JaCoCo/Redis 断言未做；运营指标未做。
- 本轮**未触碰**：W-P0-13 仓内去污、contract 的 `TM0–TM8 → TM8b/TM9` 漂移、`core.py` 上帝对象拆分。
- `regression/cases.json` 因跑 e2e 被追加回归锚（e2e 的设计行为），工作区较 HEAD 有改动。

---

## 5. 下一轮建议顺序（按本仓收益/风险比）

1. **合并收尾**（确认后 `git commit` 完成 5eca373 合并）→ 让工作区脱离 `MERGE_HEAD` 半态。
2. **contract 漂移**：`shared/pipeline-contract.yaml` testmind gates 补 `TM8b/TM9`，同步 `pipeline-contract.md`、
   `pipeline-contract.schema.json`、`shared/docs/AGENT_STACK_PEAK.md` Product DoD 表（§3）。
3. **INV-006 栈级 STALE**：把各仓 gate 信封汇总成一张 Peak 视图（先 CLI/JSON，再 UI）。
4. **W-P0-13 / F9 仓内去污**（破坏性，需先列清单逐项确认）：
   `docs/recovery-*.json`(含 575KB `recovery-candidates.json`)、`docs/burst-*.json`、`docs/cron-periodicity.json`、
   `docs/dangling-skills-manifest.json`、`docs/restore-applied.json`、`recovered_pyc/`、
   `scripts/recover_from_*.py` / `restore_*.py` / `probe_*.py` / `protect_trees.py` / `scan_burst_damage.py` / `correlate_bursts.py`；
   以及 `test-Mind/skills/requirement-mind/`（RequirementMind 被塞进 TestMind 仓 —— F9 仓职责纯净违例）。
5. **M1**：shejiuPro（或等价 Java consumer）TaskBundle v2 → intake → 四件套 → export_handoff 全链。
6. P1-04/F9：`shared` 以 vendor/submodule 固定，使独立克隆可跑 gate（当前峰值为此**显性 FAIL**，属预期）。

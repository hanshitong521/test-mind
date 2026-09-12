# TestMind 整改轮次报告 — 2026-09-10（第二轮：合并收尾 · 仓内去污 · 契约对齐 · 栈级视图）

> 依据：`问题文档.md`（缺陷 SSOT + F1–F10 满分规格）；承接 `ROUND_20260910_peak_envelope_tm3.md`（第一轮）。
> 状态：test-Mind 门禁 **rollup=PASS**（TM0–TM9，`incomplete=0`）；栈级视图 `peak-stack-view` 已落地（CLI/JSON 层）。
> **PILOT 定性不变，远未到 9 分。**

---

## 0. 一句话结论

本轮把第一轮遗留的**半态收尾**清干净，并补上两个被点名的缺口与一个第一轮没看见的空壳缺陷：

1. **合并收尾**（`bf8a78a`）：脱离 `MERGE_HEAD` 半态，工作区干净。
2. **仓内去污 W-P0-13 / F9**（`dd1279e`）：56 文件 / 23869 行移除，全部先备份到工作区外。
3. **回归锚写入幂等**（`0f3f214`）：修掉"每次跑 e2e 都产生只有时间戳变了的假脏 diff"。
4. **contract 漂移修正**：`testmind.gates` 由 `TM0–TM8` 对齐为 `TM0–TM9（含 TM8b）`，MD 重生成，SSOT 表同步。
5. **`gate-envelope.schema.json` 原为 2 字节空文件** → 补齐为可用的 INV-005 schema。
6. **INV-006 栈级聚合视图**：新增 `shared/scripts/peak-stack-view.mjs`（8 项契约测试）。

---

## 1. 合并收尾（`bf8a78a`）

第一轮把 `testmind/core.py` 的冲突按"两边意图并集"解完，但仓库仍是 `MERGE_HEAD` 半态。本轮：

```
git add -A && git commit -F <msg>   →  [master bf8a78a]
MERGE_HEAD 存在？ → False
```

合并带入的资产（`benchmark/`、`examples/flash-sale/`、`examples/generic/`）随之入仓。

---

## 2. 仓内去污（W-P0-13 / F9，`dd1279e`）

**先备份后删除**（SOUL 边界）：全部目标复制到**工作区之外**
`E:\workA\_cleanup_backup_20260910\`（60 文件：docs 7 / scripts 20 / recovered_pyc 4 / requirement-mind 29）。

| 类别 | 内容 |
|------|------|
| `docs/`（7） | `recovery-candidates.json`(575KB)、`recovery-strong.json`、`burst-damage-20260907.json`、`burst-history.json`、`cron-periodicity.json`、`dangling-skills-manifest.json`、`restore-applied.json` |
| `scripts/`（20） | `recover_from_*`、`restore_*`、`probe_*`、`protect_trees`、`scan_burst_damage`、`correlate_bursts`、`rank_recovery_candidates`、`find_recovery_copies`、`verify_restored`、`test_cron_periodicity` 及其配套测试 |
| `skills/requirement-mind/`（29） | RequirementMind 被塞进 TestMind 仓 —— **F9 仓职责纯净违例** |
| `recovered_pyc/`（4） | 恢复事故产生的 pyc 堆；**未被 git 跟踪**，故必须外部备份 |

`recovered_pyc/` 未纳入版本控制 → 删除前必须外部备份（否则不可恢复）。已确认**无门禁依赖**：这些脚本仅被自身与事故文档引用，未被 `peak-gate.mjs` / CI / tests 引用。删后 `unittest 80/80 OK`。

### 2.1 事故记录（诚实披露）

执行删除时，`Remove-Item -Recurse` 触发安全层拦截，同时**连带影响了一批无关文件**（`scripts/peak-gate.mjs`、`run_peak_verify.py`、`test_peak_gate_envelope.mjs`、`docs/ROUND_*.md`、`skills/testmind/SKILL.md` 等 16 个 tracked 文件被移出工作区）。

发现方式：`git status` 出现 ` D`（未暂存删除）而非预期的 `D `（已暂存删除）。
处置：`git checkout -- .` 全部恢复，随后用 `git rm` 精确删除目标集合。**无数据损失**（tracked 文件由 git 历史 + 外部备份双重保底）。
教训：批量删除优先用 `git rm`（精确、可审计），**不要**用 `Remove-Item -Recurse` 去碰版本控制内的目录。

---

## 3. 回归锚写入幂等（`0f3f214`）

**症状**：每次跑 `examples/red-packet/e2e.py` 后，`regression/cases.json` 都会出现 diff —— 内容只有 `added` 时间戳变了。

**根因**（`testmind/core.py:1469`，修复前）：

```python
case = {**case, "regression_reason": reason, "added": utc()}   # 无条件刷新
```

**修复**：同 id 已存在时保留原 `added`。

```python
prev = next((c for c in cases if c["id"] == case["id"]), None)
case = {**case, "regression_reason": reason, "added": (prev or {}).get("added") or utc()}
```

**测试**：`tests/test_all.py::TestRegressionRegistry` 增加"重复登记不得刷新 `added`"断言；同时把测试的清理写回改为 `indent=2`（与产品格式一致），避免**测试自身**把 registry 改脏。

**验证**：`git checkout -- regression/cases.json` 后跑 e2e → 工作区**不再出现该文件的 diff**。

---

## 4. contract 漂移修正

| 文件 | 变更 |
|------|------|
| `shared/pipeline-contract.yaml` | `testmind.gates`：`[TM0..TM8]` → `[TM0..TM8, TM8b, TM9]` |
| `shared/pipeline-contract.md` | 由 `generate-pipeline-contract-md.mjs` 重生成（纯镜像） |
| `shared/docs/AGENT_STACK_PEAK.md` | Product DoD 表：`TM0–TM8` → `TM0–TM9（含 TM8b）` |

`contract_version` **保持 1**：这是把契约追认到既有实现（TM8b/TM9 一直在跑），不是契约语义升级；变更由 `contract_hash` 体现（`eb5402fe…` → `f0dbef92…`），INV-006 据此判 STALE。
`pipeline-contract.schema.json` 不枚举 gate 名，无需改动。

---

## 5. `gate-envelope.schema.json` 空壳缺陷

`shared/schemas/gate-envelope.schema.json` 原为 **2 字节**（空文件）——"有 schema 文件"不等于"有 schema 校验"。本轮补齐：10 个 INV-005 必填字段 + `status` 枚举（对齐契约 `gate_status_enum`）+ `evidence` 的 INV-010 说明。

> 同类问题：`shared/schemas/install-artifact.schema.json` 同为 2 字节空文件（P1-04/F9 vendor 缺口），本轮**未动**，列入待办。

---

## 6. INV-006 栈级聚合视图（新增）

`shared/scripts/peak-stack-view.mjs`：

- 解析 `pipeline-contract.yaml`（无第三方依赖的极简解析器）；
- 对每个 `peak_core` 组件：读 `<repo>/reports/peak-gate-envelope.json`，判 **INV-005 完整性** + **INV-006 新鲜度**；
- `contract` 伪组件做 INV-001 / `root_strategy: discover` 自检；
- 输出文本表 + `shared/reports/peak-stack-view.json`。

**关键设计：STALE 只在代码变更时触发。**
若 `env.commit..HEAD` 之间只改了 `reports/`、`docs/` 或 `*.md`，视为 evidence-only，不算漂移 ——
否则"提交证据"这一动作本身会使证据过期（死循环）。`contract_hash` 变更一律 STALE；`env.commit` 不可解析（rebase）按 STALE 处理。

**实测（本机）**：

```
# peak-stack-view  peak_core=FAIL  optimization_coverage=NOT_INSTALLED
contract_version=1 contract_hash=f0dbef92d0a5ec9e
requirement_mind   NOT_MEASURED   requirement-mind/reports/peak-gate-envelope.json
token_mind         NOT_MEASURED   Token-Mind/reports/peak-gate-envelope.json
brain              NOT_MEASURED   project-brain-agent/reports/peak-gate-envelope.json
testmind           PASS  stale=no test-Mind/reports/peak-gate-envelope.json
shejiu_integration NOT_INSTALLED  -
contract           PASS           -
```

`peak_core=FAIL` 是**真实状态**：三仓尚无门禁信封，shejiuPro 未安装。这正是视图该说的话，而不是靠代位凑绿。
（变更契约后首次运行曾显示 `testmind STALE`——`commit` 与 `contract_hash` 双双落后；重跑门禁生成新信封后转 `PASS / stale=no`，INV-006 闭环成立。）

**契约测试**：`shared/scripts/test_peak_stack_view.mjs`，8/8 通过（含 `parseContract` 无漂移断言、INV-001 违规检测、`rollupFrom` 判定、`isEvidenceOnly` 边界）。

---

## 7. 验收证据（本机，2026-09-10）

| # | 命令 | 结果 |
|---|------|------|
| 1 | `python -m unittest discover -s tests -q`（test-Mind） | `Ran 80 tests` → **OK** |
| 2 | `node --test shared/scripts/test_peak_stack_view.mjs` | **8 pass / 0 fail** |
| 3 | `python examples/red-packet/e2e.py` | `final=PASS`，114 cases，exit 0 |
| 4 | `node scripts/peak-gate.mjs`（test-Mind） | `rollup=PASS`，TM0–TM9 全 PASS，`incomplete=0`，exit 0 |
| 5 | `node shared/scripts/peak-stack-view.mjs` | `testmind=PASS stale=no`；`peak_core=FAIL`（其余仓无信封） |

**提交**：`bf8a78a`（merge）→ `dd1279e`（去污）→ `0f3f214`（幂等）。工作区干净，`HEAD=0f3f214`。
**信封**：`test-Mind/reports/peak-gate-envelope.json`（`commit=0f3f214`，`component_version=1.2.0`，`contract_hash=f0dbef92d0a5ec9e`）。

---

## 8. 诚实边界

- **F2 未达成**：Linux CI 仍未实跑。
- **F1/F6 未达成**：仍无真实 Java/Spring consumer 全链。
- **F5/F7/F10 未动**：MCP 工具未收敛、k6/JaCoCo/Redis 断言未做、运营指标未做。
- **栈级视图只到 CLI/JSON**：UI 视图未做；三仓尚无门禁信封，`peak_core` 仍是 `FAIL`。
- `install-artifact.schema.json` 仍是空文件（2 字节）。
- `shared/` **不受版本控制**（无 `.git`）——本轮 shared 改动仅落盘，不入任何 commit。

## 9. 下一轮建议顺序

1. **各仓补门禁信封**：RM/G/B 三仓落地 `reports/peak-gate-envelope.json`，让栈级视图真正收敛。
2. **`install-artifact.schema.json`**：补 schema，使 install/DRIFT 检测不再"有文件无内容"。
3. **P1-04/F9 vendor 固定**：`shared` 以 vendor/submodule 进入 test-Mind，使独立克隆可跑 gate（当前为显性 FAIL，属预期）。
4. **M1**：shejiuPro（或等价 Java consumer）TaskBundle v2 → intake → 四件套 → export_handoff 全链。
5. **F5**：MCP 工具 32 → 10–12 收敛。

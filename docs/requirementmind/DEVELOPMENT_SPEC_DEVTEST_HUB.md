# DEVELOPMENT_SPEC — DevTest Hub（VerifyMind × Project-Brain × UI）

> 编译自 RequirementMind 追问冻结（2026-09-14）。读者是完全不了解对话历史的 Coding Agent（R10）。
> 涉及仓：`A-github-skill-mcp/UI`（主实现）· `verify-mind`（消费/回写）· `project-brain-agent`（hints 写入）。
> 栈合同：`A-skill/shared/pipeline-contract.yaml`（brain=WHY · verify-mind=verified_evidence）。

---

## Goal

打通**开发 AI → 测试 AI** 的交接闭环：开发完成一个任务（可含多个接口）后，由人触发提交；测试 AI 通过 VerifyMind 读契约、跑验收、报 BUG；**测试方可把漏交的关联接口/业务挂进同一任务**（扩面）；双方可在同一交接单上留言、质疑、举证。Project-Brain 只提供 **hints_only** 背景，**不得**作为测试判定依据。

**不做**：替代 RequirementMind Gate；替代 VerifyMind 执行引擎；在 Brain MCP 新增第六个工具（v1 走 UI HTTP）。

---

## Frozen Decisions（问卷冻结 · 不得静默覆盖）

| ID | 决策 | 来源 |
|----|------|------|
| **DEC-DH-001** | 交接粒度 = **单次开发任务**（`task_id` / TaskBundle v2 对齐；一条 handoff 可含多个 endpoint） | 用户问卷 |
| **DEC-DH-002** | 契约 SSOT = **UI `.data/handoffs/`**；业务仓 `CONTRACT_<模块>.md` 为**镜像**（开发 AI 提交时可选写入） | 用户问卷 |
| **DEC-DH-003** | 提交触发 = **人告知开发 AI「接口/任务做完了」→ 开发 AI 显式 HTTP submit**；v1 **不做** session-end / git-push 自动提交 | 用户口述 |
| **DEC-DH-004** | 防带偏 = **分层**：`contract` = 判定依据；`hints` = Brain 叙事，单独区块，VerifyMind **禁止**用 hints 写 `expected` | 用户问卷 |
| **DEC-DH-005** | 评论 = 线程式 JSONL，复用 `persona_store` 模式；`observer` = `cursor`/`qoder`/`human`/`verify-mind`/`fix-agent` | R11 技术裁决 |
| **DEC-DH-006** | API 挂在现有 UI Brain API **:18787**（`/api/handoffs/*`）；VerifyMind 通过 HTTP 读写，不新增 Brain MCP 工具 | R11 · STACK-CONTRACT |
| **DEC-DH-007** | 页面路径 **`/handoff`**，顶栏 `hub-nav.js` 增「开发测试交接」 | R11 |
| **DEC-DH-008** | BUG **允许对抗式结案**：开发/修复方可**只留言质疑、不修代码**；发现方可**驳回 / 反驳 / 举证 / 承认不是 BUG**；无证据不得静默关单 | 用户补充 2026-09-14 |
| **DEC-DH-009** | 测试 AI **有权扩面**：开发只交 2 个接口，测试可根据调用图/共用表/契约缺口把第 3–N 个接口、表、定时任务、旁路业务 **挂到同一 task**；扩面 ≠ BUG；开发可质疑「不在本次任务」 | 用户补充 2026-09-14 |
| **DEC-DH-010** | **终判分两层**：`submitted`（开发声明面）与 `discovered`（测试扩面）分开记账；声明面全绿但扩面未测/未决 → `HOLD`，禁止因漏测旁路报全 PASS | R11 · 对齐 VerifyMind endpoint_ledger |
| **DEC-DH-011** | 扩面项 expected **禁止臆造**：新接口须先有 `source`（代码/DDL/OpenAPI/FROZEN）；查不到 → `UNKNOWN` 挂 `contract_gap`，先问开发补契约，再测 | R11 · VerifyMind 硬规则 1 |

---

## Current System Facts

| ID | 事实 | 证据 |
|----|------|------|
| FACT-DH-001 | VerifyMind 已有契约母版 §0–§9、`final_gate` 四件套导出、`endpoint_ledger`、`triage` 三分法 | `verify-mind/契约文档母版.md` · `testmind/export.py` |
| FACT-DH-002 | TaskBundle v2 已有 `spec.verify.change_manifest` schema | `token-mind/contextmind/sdlc/task-bundle.verify.schema.json` |
| FACT-DH-003 | Brain MCP 对外仅五工具；TestMind 胶水走 `POST /v1/memory/{project}/candidate` | `project-brain-agent/docs/STACK-CONTRACT.md` |
| FACT-DH-004 | UI 已有 `persona_store` 评论 JSONL、`/v1/persona/{project}` API、顶栏导航 | `UI/src/brain_services/persona_store.py` · `app.py` |
| FACT-DH-005 | handoff-schema 已有 `code_to_test`，无 `code_to_verify`；需扩展或映射到 DevTest Hub | `code-mind/shared/handoff-schema.yaml` |
| FACT-DH-006 | Brain P1 规格已写 TaskBundle `meta.handoff_summary`，**未实现** | `MEMORY-LIFECYCLE.md:93` |
| FACT-DH-007 | shejiuPro 已有实例契约 `CONTRACT_舍九公海本地联调.md` | `shejiuPro/AI-舍九特供资料/05-本地测试代码/docs/` |

---

## Architecture

```mermaid
flowchart LR
  subgraph dev [开发侧]
    Human[人: 做完了]
    DevAI[开发 AI /ai-code]
    BrainW[Brain record_task_outcome]
  end
  subgraph hub [UI DevTest Hub :18787]
    Store[.data/handoffs/]
    API[/api/handoffs/*]
    Page[/handoff 页面]
    Comments[*.comments.jsonl]
  end
  subgraph test [测试侧]
    TestAI[测试 AI + VerifyMind]
    VM[plan → run → final_gate]
  end
  Human --> DevAI
  DevAI -->|POST submit| API
  DevAI -->|hints candidate| BrainW
  API --> Store
  Page --> API
  TestAI -->|GET contract SSOT| API
  TestAI --> VM
  VM -->|POST verify-run + bugs| API
  BrainW -.->|hints_only 检索| TestAI
```

### 职责切分

| 层 | 系统 | DevTest Hub 角色 |
|----|------|------------------|
| WHAT | RequirementMind FROZEN | 写入 handoff `decision_refs`，不重复辩论 |
| WHY | Project-Brain | 写 `hints`（candidate）；search 时带 `hints_only: true` |
| VERIFY | VerifyMind | 只读 `contract`；执行后回写 `gate` / `bugs` / 四件套路径 |
| 交接 SSOT | UI `.data/handoffs/` | 状态机 + 契约正文 + 评论 + BUG 台账 |

---

## Data Model

### 目录布局（SSOT）

```
UI/.data/handoffs/
└── {project_id}/
    ├── index.jsonl              # 轻量索引行（id, task_id, status, endpoints[], updated_at）
    ├── {handoff_id}.json        # 完整 HandoffPackage
    ├── {handoff_id}.comments.jsonl
    └── {handoff_id}/artifacts/  # 镜像 CONTRACT.md、verify 四件套拷贝或相对引用
```

### HandoffPackage（`{handoff_id}.json`）

```json
{
  "schema_name": "devtest_handoff",
  "schema_version": 1,
  "handoff_id": "DH-20260914-abc123",
  "project_id": "shejiuPro",
  "task_id": "TB-xxx",
  "status": "PENDING_TEST",
  "title": "公海 brandHandle 列表接口",
  "endpoints": [
    {"method": "GET", "path": "/ocean/.../brandHandle/products/list", "origin": "submitted"}
  ],
  "work_items": [
    {
      "item_id": "WI-E3",
      "kind": "scope_add",
      "status": "PROPOSED",
      "origin": "discovered",
      "target": {"type": "endpoint", "method": "GET", "path": "/ocean/.../visible/page"},
      "reason": "与 list 共用 BrandHandle 解析与同一视图；analyze_impact R1",
      "source": "Controller:line",
      "proposed_by": {"peer": "qoder", "role": "verify-mind"}
    }
  ],
  "change_manifest": { /* task-bundle.verify.change_manifest 子集 */ },
  "contract": {
    "version": "v1",
    "sections": {
      "0_requirement": "...",
      "1_state_machine": "...",
      "2_ddl": "...",
      "3_api": "...",
      "4_accounts": "...",
      "5_acceptance_matrix": "...",
      "6_impact_chain": "...",
      "7_regression_anchors": "...",
      "8_change_surface": "...",
      "9_environment": "..."
    }
  },
  "hints": {
    "disclaimer": "非判定依据；VerifyMind 禁止据此写 expected",
    "why": "解决公海看板折叠与公司维度切换…",
    "dev_notes": "companyId 为空走默认视图…",
    "sql_perf_notes": [{"sql_id": "Q1", "observed_ms": 120, "threshold_ms": 1000}],
    "brain_memory_ids": ["mem-xxx"]
  },
  "dev_self_check": {
    "compile": "PASS",
    "local_probe": "NOT_RUN",
    "notes": ""
  },
  "verify": {
    "claimed_by": null,
    "runs": [
      {
        "run_id": "20260914-172648",
        "gate_final": "FAIL",
        "report_dir": "artifacts/20260914-172648",
        "endpoint_coverage": {}
      }
    ]
  },
  "bugs": [
    {
      "bug_id": "BUG-001",
      "status": "DISPUTED",
      "severity": "P0",
      "title": "跨公司 companyId 未拒绝",
      "case_id": "R9-cross-company",
      "reported_by": {"peer": "cursor", "role": "verify-mind"},
      "evidence_refs": ["artifacts/.../cases/R9/response.json"],
      "triage": "PRODUCT_BUG",
      "dispute_round": 1,
      "resolution": null
    }
  ],
  "mirror": {
    "repo_relative_path": "AI-舍九特供资料/05-本地测试代码/docs/CONTRACT_舍九公海本地联调.md",
    "content_hash": "sha256:...",
    "synced_at": "2026-09-14T18:00:00+08:00"
  },
  "actors": {
    "submitted_by": {"peer": "cursor", "agent": "ai-code"},
    "test_by": null
  },
  "timestamps": {
    "created_at": "",
    "submitted_at": "",
    "updated_at": ""
  }
}
```

### 状态机（硬规则）

```
DRAFT ──submit──▶ PENDING_TEST ──claim──▶ IN_TEST ──final_gate──▶ PASS | FAIL | HOLD | BLOCKED
                      ▲                              │
                      └──────── resubmit ────────────┘ (FAIL/HOLD 修复后)
```

| 迁移 | 谁触发 | 条件 |
|------|--------|------|
| → PENDING_TEST | 开发 AI `POST .../submit` | `contract.sections.3_api` 非空；至少 1 endpoint；人已告知「做完了」 |
| → IN_TEST | 测试 AI `POST .../claim` | 当前 `PENDING_TEST`；写入 `claimed_by` |
| → PASS/FAIL/HOLD/BLOCKED | VerifyMind `POST .../verify-run` | 附 `gate.json`；仅 `IN_TEST` |
| 评论 | 任意角色 `POST .../comments` | 任意状态 |

非法迁移 → HTTP 409 + `next_actions`。

### BUG 状态机（对抗式 · DEC-DH-008）

```
                    ┌── accept_not_bug（发现方承认 + 证据）──▶ NOT_A_BUG
                    │
OPEN ──dispute──▶ DISPUTED ──rebuttal/evidence──▶ DISPUTED（回合+1）
  │                    │                                    │
  │                    └── confirm_bug（发现方维持）──────────▶ CONFIRMED
  │                                                              │
  │                    ┌── fix_claim + 证据（开发方）──────────▶ FIXED_PENDING
  │                    │                                         │
  │                    └── question only（只质疑不修）──────────▶ DISPUTED（保持）
  │                                                              │
  └──────────────────────────────────────────────────────── verify rerun PASS ──▶ VERIFIED
                                                                 verify FAIL ──▶ CONFIRMED
```

| BUG.status | 含义 | 谁可写入 |
|------------|------|----------|
| `OPEN` | 测试方刚登记，待开发响应 | verify-mind / 测试 AI |
| `DISPUTED` | 开发/修复方提出疑问或反驳，**尚未修代码** | fix-agent / dev AI `POST .../dispute` |
| `CONFIRMED` | 发现方维持 BUG 成立（驳回「不是 BUG」或复测仍 FAIL） | 测试 AI `POST .../resolve{verdict:confirmed}` |
| `NOT_A_BUG` | 发现方承认误报 / 接受对方证据 | **仅**测试 AI `resolve{verdict:not_a_bug}` + `evidence_refs` |
| `FIXED_PENDING` | 开发声称已修，待复测 | fix-agent + `evidence_refs`（diff/探针/说明） |
| `VERIFIED` | 复测 PASS 或发现方验收通过 | verify-mind rerun 或测试 AI |
| `WONTFIX` | 人裁定不修 | human only |

**硬规则（防假关单）**

1. **只质疑、不修**：开发/修复 AI 用 `comment.kind=question` 或 `POST .../dispute`，**禁止**同时改 `status=FIXED`。
2. **关「不是 BUG」**：必须发现方 AI 发 `resolve{verdict:not_a_bug}`，且附 `evidence_refs`（契约条款 / 复跑 request+response / SQL 结果）；开发方单方声称无效。
3. **维持 BUG**：发现方可 `rebuttal` + 新证据，或 `confirm_bug`；开发方再次 `dispute` 进入下一 `dispute_round`。
4. **无证据 = 无效发言**：`question` / `dispute` / `rebuttal` / `accept_not_bug` 均无 `evidence_refs` 时，API 返回 400，评论仍可存但标 `weak=true`（不进状态迁移）。
5. **契约冲突**：若 dispute 引用 contract 条款，须写 `contract_ref`（如 `§5.R9`）；与 contract 正文矛盾 → 测试方须升 `CONFLICT` 评论或改 contract，不得口头覆盖 SSOT。

### 评论类型（`comments.jsonl` · 绑定 `bug_id` 可选）

| `kind` | 角色 | 作用 | 是否驱动状态 |
|--------|------|------|----------------|
| `question` | dev / fix | 有疑问，**不修代码**，请澄清或证明无问题 | → `DISPUTED` |
| `dispute` | dev / fix | 明确反驳 BUG 成立性 | → `DISPUTED` |
| `evidence` | 任意 | 附 request/response/SQL/日志路径 | 供 resolve 引用 |
| `rebuttal` | verify-mind / test | 驳回对方「不是 BUG」，维持或补充证据 | 维持 `CONFIRMED` 或回 `DISPUTED` |
| `accept_not_bug` | verify-mind / test | 承认误报，附证据说明为何不是 BUG | → `NOT_A_BUG` |
| `confirm_bug` | verify-mind / test | 维持 BUG，要求修复 | → `CONFIRMED` |
| `fix_claim` | dev / fix | 声称已修 + 改动摘要 | → `FIXED_PENDING` |
| `fix_verify` | verify-mind | 复测结果 PASS/FAIL | → `VERIFIED` / `CONFIRMED` |
| `scope_propose` | verify-mind / test | 提议扩面 | → work_item `PROPOSED` |
| `scope_reject` | dev / fix | 主张不在本次任务 | 对抗，不直接 REJECT 生效 |
| `scope_keep` | verify-mind / test | 维持扩面 | 保持 PROPOSED/ACCEPTED |
| `contract_patch` | test | 提议补 §3/§5 | 待开发 accept |
| `note` | human / 任意 | 自由讨论，不驱动状态 | 否 |

每条评论字段：`id`, `bug_id?`, `item_id?`, `handoff_id`, `kind`, `observer`, `role`, `message`, `evidence_refs[]`, `contract_ref?`, `reply_to?`, `weak?`, `created_cn`。

### 工作项三分法（扩面 ≠ BUG · DEC-DH-009）

开发只交 2 个接口、设计/调用图实际碰到 5 个，是**漏测面**，不是立刻 3 个产品 BUG。Hub 用 `work_items[]`，`kind` 三选一：

| `kind` | 是什么 | 挂到任务里？ | 典型例子 |
|--------|--------|--------------|----------|
| `bug` | 已测接口 Actual≠Expected（或副作用错） | 是 | 跨公司 200 写库 |
| `scope_add` | 测试发现应测但开发没交的面 | **是（本任务）** | 共用 Service 的 `visible/page`、同表写接口、定时任务 |
| `contract_gap` | 该测但 **expected 未知** | 是，先补契约 | 新接口无 OpenAPI/无 FROZEN 口径 |

`origin`：`submitted`（开发声明）| `discovered`（测试扩面）。

`target.type`：`endpoint` | `table` | `job` | `cache` | `actor_matrix` | `sql` | `sibling_feature`。

#### 扩面状态机

```
PROPOSED ──dev accept──▶ ACCEPTED ──测完──▶ COVERED
    │                      │
    │                      └── 测出缺陷 ──▶ 派生 bug（item_id 回链 parent_item_id）
    │
    ├──dev reject「不在本次」──▶ REJECTED（须 evidence：任务边界/FROZEN 范围）
    │                              │
    │                              └── 测试 rebuttal ──▶ PROPOSED（回合+1）或 ESCALATE
    │
    └── 过大 / 另一模块 ──▶ SPLIT（生成新 handoff_id，本项只留链接）
```

| 规则 | 说明 |
|------|------|
| 测试**有权新增** | `POST .../work-items`，角色 verify-mind/test，不经开发批准即可 `PROPOSED` |
| 开发**有权质疑** | 同 BUG 对抗：`dispute`「这不在本次任务 / 共用但行为不该变」 |
| 测试**有权维持** | 给调用图/共用表/git range 证据；开发单方 REJECT 不生效，须测试接受或人 ESCALATE |
| **禁止用 BUG 冒充扩面** | 没跑过的接口不得登记 `bug`；先 `scope_add`，测完才允许派生 `bug` |
| **禁止臆造 expected** | `scope_add` 进入 plan 前必须有 `source`；否则转 `contract_gap`，`plan_verification {ask}` |
| **终判** | 声明面有 FAIL → 任务 `FAIL`；声明面绿但存在 `PROPOSED`/`ACCEPTED` 未 COVERED → `HOLD`（DEC-DH-010） |
| **拆单上限** | 单次 `scope_add` 使 endpoint 数 > `submitted×3` 或跨另一个 `service_id` → 建议 `SPLIT`，人可强制挂本任务 |

#### 扩面从哪来（测试 AI 必跑，不是灵感）

claim 之后、首轮 `run_verification` 之前：

1. `analyze_impact {path, java_graph}` → R0–R2 受影响 endpoints/tables/jobs
2. 对照 `endpoints[origin=submitted]` 做差集 → 候选 `scope_add`
3. 账号矩阵：开发只用一个 actor → 至少提 `actor_matrix` 扩面（跨公司/匿名）
4. `endpoint_ledger` 写端点未触达 → `scope_add` 或 `NOT_TESTED` 进 HOLD
5. 契约 §6 影响链若空，测试可标 `contract_gap` 要求开发补，而不是自己编调用链

### 设计补充（比「报 BUG + 留言」多出来的几招）

1. **Partial Gate 看板**：列表同时显示 `submitted_pass` / `discovered_open` / `bugs_open`，避免「2 个接口绿了就以为整包过」。
2. **同源去重**：新 `scope_add` 的 method+path 若已在**另一条** `IN_TEST`/`PENDING_TEST` handoff，只加 `related_handoff_id` 链接，不双测双报。
3. **性能项独立**：`sql_perf_notes` / 响应时间超阈记 `work_item.kind=perf_advisory`（可选，v1 可并入 `scope_add`+`target.type=sql`），**不进** `final_gate` FAIL（对齐 VerifyMind perf 是 advisory）。
4. **契约补丁权**：测试发现 §3 缺字段可 `POST .../contract-patch`（`status=PROPOSED`），开发 `accept` 才并入 SSOT；测试不得直接改判定条款。
5. **冻结现场优先于嘴炮**：FAIL/DISPUTED 期间 `artifacts/` 只追加不删；开发「证明没问题」必须引用同一 `run_id` 的 request/response 或新跑一条对照 case。
6. **人只需进场的三种**：`dispute_round≥3`、`SPLIT` 争议、`WONTFIX`。其余 AI 对 AI。

---

## UI 页面 `/handoff`（给人 + 给 AI 读同源 API）

### 列表视图

- 筛选：`project_id` · `status` · `endpoint` 模糊 · `task_id`
- 列：标题 · 状态 · 声明接口数 / 扩面数 · 提交人 · 测试人 · BUG 数 · 最后 gate · 更新时间
- 快捷：**待测试队列**（`PENDING_TEST` 置顶）；**HOLD 扩面未关** 单独筛

### 详情视图（单条 handoff）

1. **状态条** + 操作（claim / 附 verify 报告 / 重开）
2. **契约正文**（§0–§9 折叠面板；Markdown 渲染）
3. **Hints 区**（黄色边框 + 固定免责声明 DEC-DH-004）
4. **接口表**：两栏——**开发声明** vs **测试扩面**（PROPOSED/ACCEPTED/REJECTED/COVERED）
5. **自测摘要**：dev_self_check
6. **Verify 运行史**：run_id · gate · 链到四件套；声明面 / 扩面覆盖率分列
7. **工作项**：`scope_add` / `contract_gap` / `bug` 分 Tab；扩面可「接受进契约 / 质疑不在任务 / 拆新单」
8. **BUG 列表**：severity · status · dispute_round · 证据 · **操作**（质疑 / 维持 / 承认误报 / 声称已修）
9. **讨论区**：按 `bug_id` 或 `item_id` 分组
10. **对抗时间线**：BUG 与扩面共用同一套可视化

### 导航

- `hub-nav.js` / `/api/nav` 增加 `handoff: "/handoff"`
- `index.html` 总控制台增加卡片

---

## HTTP API（UI `brain_api/app.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/handoffs` | 列表；query: `project_id`, `status`, `limit` |
| POST | `/api/handoffs` | 创建 DRAFT（开发 AI） |
| POST | `/api/handoffs/{id}/work-items` | 测试扩面或开发补交；`kind=scope_add\|contract_gap\|bug`（bug 建议走 `/bugs`） |
| POST | `/api/handoffs/{id}/work-items/{item_id}/accept` | 开发接受扩面 → `ACCEPTED`；可同时补 contract §3 |
| POST | `/api/handoffs/{id}/work-items/{item_id}/reject` | 开发主张不在本次；须 `evidence_refs`；状态仍等测试 `resolve` |
| POST | `/api/handoffs/{id}/work-items/{item_id}/resolve` | **测试方**：`accepted` / `keep` / `split` / `drop` |
| POST | `/api/handoffs/{id}/work-items/{item_id}/split` | 拆成新 handoff，本项 `SPLIT` + `related_handoff_id` |
| POST | `/api/handoffs/{id}/contract-patch` | 测试提议补契约；`PROPOSED` 至开发 accept |
| GET | `/api/handoffs/{id}` | `include=hints,comments,bugs,work_items` |
| PATCH | `/api/handoffs/{id}` | 更新 contract sections / hints / dev_self_check |
| POST | `/api/handoffs/{id}/submit` | DRAFT→PENDING_TEST |
| POST | `/api/handoffs/{id}/claim` | PENDING_TEST→IN_TEST |
| POST | `/api/handoffs/{id}/comments` | 追加评论 |
| GET | `/api/handoffs/{id}/comments` | 评论列表 |
| POST | `/api/handoffs/{id}/bugs` | 登记 BUG（测试 AI / VerifyMind triage）→ `OPEN` |
| POST | `/api/handoffs/{id}/bugs/{bug_id}/dispute` | 开发/修复方质疑（`question`/`dispute`）→ `DISPUTED`；body 含 `message` + 建议 `evidence_refs` |
| POST | `/api/handoffs/{id}/bugs/{bug_id}/resolve` | **仅发现方**：`verdict` = `confirmed` \| `not_a_bug` \| `needs_more_info`；`not_a_bug` 必填 `evidence_refs` |
| POST | `/api/handoffs/{id}/bugs/{bug_id}/fix-claim` | 开发声称已修 → `FIXED_PENDING`；必填 `summary` + `evidence_refs` |
| POST | `/api/handoffs/{id}/bugs/{bug_id}/verify-fix` | 测试方复测后 → `VERIFIED` 或回 `CONFIRMED`；附 rerun case 证据 |
| PATCH | `/api/handoffs/{id}/bugs/{bug_id}` | 仅 `severity` / `title` 元数据；**禁止** PATCH 直改 `status`（须走上行专用端点） |
| POST | `/api/handoffs/{id}/verify-run` | 挂载 run_id + gate.json + artifacts 路径 |
| POST | `/api/handoffs/{id}/mirror` | 写镜像到业务仓 `mirror.repo_relative_path` |
| GET | `/api/handoffs/{id}/contract.md` | 导出 Markdown（给 VerifyMind `plan_verification`） |

**鉴权 v1**：仅 `127.0.0.1`（与现有看板一致）；`actor_peer` 请求头可选（`X-Actor-Peer: cursor`）。

---

## VerifyMind 改动（消费方）

### SKILL 增补（`skills/verifymind/SKILL.md`）

```markdown
## DevTest Hub 接入
1. 取契约：`GET http://127.0.0.1:18787/api/handoffs/{id}` → 只用 `contract` 建 plan
2. `hints` 区块：只读背景，**禁止**写入 expected / 断言 / PASS 依据（违规则 TEST_DEFECT 自审）
3. claim → 跑管道 → `final_gate` 后 `POST .../verify-run` + `POST .../bugs`（FAIL 项）
4. 评论：对不清楚的口径 `POST .../comments`，等开发 AI 回复后再冻结 plan
5. **BUG 对抗**：开发方 `dispute` 后，测试方须 `resolve` 或 `rebuttal`；承认误报用 `accept_not_bug` + 证据，禁止口头关单
6. 开发方「只是有疑问」→ `kind=question`，**不要**改代码冒充已修
7. **扩面**：`analyze_impact` 差集先 `POST /work-items`，未跑过的接口禁止直接 `/bugs`
8. 声明面绿、扩面未关 → `HOLD`，禁止全 PASS
```

### MCP / 脚本（v1 最小）

- `scripts/handoff_client.py`：urllib 封装上述 API（stdlib）
- `plan_verification` 可选参数 `handoff_id`：内部拉 contract sections 转 facts（**不**拉 hints 进 facts）
- `final_gate` 成功后/失败后回调 `verify-run`（env `VERIFY_HANDOFF_CALLBACK=1`）

**不做的 v1**：VerifyMind 不新增第 8 个 MCP 门面；交接读写走 HTTP。

---

## Project-Brain 改动（WHY 层）

### 写入（开发 AI 收尾）

`record_task_outcome` / 现有 HTTP candidate 路径扩展 metadata：

```json
{
  "kind": "experience",
  "source": "agent",
  "metadata": {
    "handoff_id": "DH-...",
    "hint_role": "dev_narrative",
    "do_not_use_as_oracle": true
  }
}
```

同步把 narrative 写入 handoff `hints`（UI API PATCH），Brain 只存摘要 + 回链 id。

### 读取（测试 AI）

`search_project_context` 当 query 含 `handoff_id` 或 endpoint 时：

```json
{
  "memories": [ /* verified only，照旧 */ ],
  "hints_only": {
    "disclaimer": "...",
    "items": [ /* candidate handoff_hint，降权排序 */ ]
  }
}
```

**禁止**把 `hints_only` 合并进 `memories` 数组（防带偏）。

### 不改动

- MCP 工具仍为五工具；不暴露 handoff CRUD
- verified 晋升规则不变；BUG 经 VerifyMind FAIL + 人 promote

---

## 开发 AI 工作流（人触发）

```
1. 人：「这个任务接口做完了，提交测试」
2. 开发 AI 编译 contract §0–§9（从代码/DDL/OpenAPI/RM FROZEN）
3. POST /api/handoffs（DRAFT） + PATCH 填满 sections
4. PATCH hints（why/dev_notes/sql_perf）
5. POST /api/handoffs/{id}/submit → PENDING_TEST
6. record_task_outcome（带 handoff_id）
7. 可选 POST /mirror → 业务仓 CONTRACT_*.md
8. POST /comments：「有疑问 @测试」
```

## 测试 AI 工作流

```
1. GET /api/handoffs?status=PENDING_TEST
2. POST /claim → IN_TEST
3. 读 contract（SSOT）；hints 仅浏览
4. analyze_impact → 差集 → POST /work-items（scope_add / contract_gap）
5. 开发可 accept/reject；测试 resolve；过大则 split
6. 仅对有 source 的面 plan_verification → prepare → run → final_gate
7. POST /verify-run + /bugs（只对已执行 case）
8. 声明面 FAIL → FAIL；声明面绿但扩面未 COVERED → HOLD；两侧都关且无 OPEN bug → PASS
```

## 修复 AI 工作流（跨 IDE · 含「只质疑不修」）

### 路径 A — 有疑问，先不修

```
1. GET handoff + BUG-001 + 评论线程
2. 若认为误报 / 契约理解不一致：
   POST .../bugs/BUG-001/dispute { kind: "question", message: "...", evidence_refs: [...] }
   → status=DISPUTED（不修代码）
3. 等测试 AI：rebuttal（维持）| accept_not_bug（关单）| 要求补充证据
4. 若 accept_not_bug → 本条 BUG 结案，handoff 可继续其他项
5. 若 confirm_bug → 走路径 B
```

### 路径 B — 确认要修

```
1. 修代码 + 更新 contract §8
2. POST .../bugs/BUG-001/fix-claim { summary, evidence_refs: [diff/探针] }
   → FIXED_PENDING
3. 测试 AI POST .../verify-fix（复跑 case_id）→ VERIFIED 或 CONFIRMED
4. 全部 BUG VERIFIED / NOT_A_BUG 后，人 trigger resubmit → PENDING_TEST
```

## 测试 AI 对抗职责（发现方 · 唯一可关「不是 BUG」）

```
收到 dispute / question 后，四选一（必须带证据）：
  ① rebuttal      — 驳回，维持 BUG，附新 request/response 或契约条款
  ② confirm_bug   — 简化版维持，要求对方修
  ③ accept_not_bug — 承认误报（TEST_DEFECT / 契约写错 / 环境问题），附为何不是产品 BUG
  ④ needs_more_info — 对方证据不足，列清还要什么

禁止：无 evidence_refs 的「我觉得不是 BUG」
禁止：开发方单方 PATCH status=NOT_A_BUG
禁止：把未执行的旁路接口直接登记为 bug（先 scope_add）
```

## 扩面争议（开发只交 2 个、测试要挂 5 个）

```
测试：POST work-items ×3（visible/page、同表写接口、跨公司 actor）
开发：reject「本次只改 list」
测试：scope_keep + impact 图「list 与 page 共用 BrandHandleSupport」
  → 开发 accept 并补契约  或  人 ESCALATE / SPLIT 成第二条 handoff
在争议未关前：list 可以先测，任务不得 PASS（HOLD）
```

---

## Scope

### In scope（M1）

| 仓 | 交付 |
|----|------|
| **UI** | `handoff_store.py` · API 上表 · `/handoff` 页面 · nav · pytest |
| **verify-mind** | `handoff_client.py` · SKILL 增补 · `plan_verification` 读 handoff_id |
| **project-brain-agent** | `hints_only` 分轨 · outcome metadata · 文档 |
| **shared** | `handoff-schema.yaml` 增 `code_to_verify` 块（映射 Hub 字段） |

### Out of scope（v1）

- 多用户权限 / 外网部署
- WebSocket 实时评论
- 自动 session-end 提交
- Brain 新 MCP 工具
- VerifyMind 内置 UI 看板替代（8901 保持只读自有 reports）

---

## Acceptance Criteria

| AC | 判据 |
|----|------|
| AC-1 | 开发 AI 仅凭本文 + API 可创建 handoff 并 submit 到 `PENDING_TEST` |
| AC-2 | 测试 AI claim 后，VerifyMind `plan_verification{handoff_id}` 只用 contract 生成 facts |
| AC-3 | hints 出现在 UI 独立黄框；VerifyMind 单元测试断言 hints **未**进入 plan facts |
| AC-4 | `final_gate` FAIL 后，`/api/handoffs/{id}` 含 BUG 行 + `verify.runs[].gate_final=FAIL` |
| AC-5 | 评论线程：dev 留言 → test 回复 → fix-agent 追问，三端 `observer` 可区分 |
| AC-6 | `POST /mirror` 写出业务仓 CONTRACT 文件，`content_hash` 与 Hub 一致 |
| AC-7 | `pytest` UI handoff API ≥12 cases 绿；verify-mind handoff_client 冒烟绿 |
| AC-8 | Brain `search_project_context` 返回结构含 `hints_only`，且与 `memories` 分离 |
| AC-9 | 开发 AI 仅 `dispute{kind:question}` 后 BUG 为 `DISPUTED`，且无代码 fix-claim；测试 AI `accept_not_bug` 后 → `NOT_A_BUG` |
| AC-10 | 开发 AI 试图 `PATCH status=NOT_A_BUG` → 403；测试 AI `rebuttal` 无 `evidence_refs` → 400 或 `weak=true` 不迁移状态 |
| AC-11 | UI 对抗时间线展示完整回合：OPEN → question → rebuttal → accept_not_bug（或 → fix-claim → verify-fix） |
| AC-12 | 测试 AI `POST /work-items` 后列表出现 `origin=discovered`；开发未 accept 时 `final_gate` 不得因「只测了声明 2 接口」报 PASS（须 HOLD 或显式 drop） |
| AC-13 | 无 `source` 的 `scope_add` 被拒或自动转为 `contract_gap`；不得进入 plan facts |
| AC-14 | 开发 `reject` 扩面后 status 仍非最终 REJECTED，直至测试 `resolve{drop}` 或人 ESCALATE |
| AC-15 | `split` 产生新 `handoff_id`，旧项留 `related_handoff_id`，两边不重复跑同一 endpoint |

---

## Risk & Mitigations

| 风险 | 缓解 |
|------|------|
| 测试 AI 偷看 hints 写 expected | SKILL 硬规则 + plan 审计（facts 来源不得含 hints） |
| UI 与业务仓契约漂移 | mirror hash 校验；handoff 展示「镜像滞后」警告 |
| 一人占坑 IN_TEST 不释放 | `claim` 超时 4h 可 `force_reclaim`（human only，v1 可选） |
| `.data/handoffs` 丢数据 | 鼓励 mirror 到 git；index.jsonl 可重建 |
| 开发方用嘴关 BUG | `NOT_A_BUG` 仅发现方 `resolve`；API 禁止 dev 角色调用 |
| 双方空对空吵架 | 无 `evidence_refs` 不迁移状态；`dispute_round`≥3 且仍 DISPUTED → 标 `ESCALATE` 给人 |
| 测试无限扩面拖死任务 | `submitted×3` 或跨 `service_id` → SPLIT；人可 drop |
| 扩面变假 BUG | 未执行禁止 `kind=bug`；SKILL + API 校验 |
| 开发「本次只改两个」挡漏测 | 单方 reject 不生效；HOLD 直到对抗结束 |

---

## Implementation Order

1. **UI** `handoff_store.py` + CRUD API + 状态机单测
2. **UI** `/handoff` 页面（列表 + 详情 + 评论）
3. **verify-mind** `handoff_client.py` + SKILL + plan 接入
4. **project-brain** hints_only 分轨
5. **E2E** shejiuPro 公海任务走通一条真实 handoff

---

## Requirement Gate Status

| 检查项 | 状态 |
|--------|------|
| Blocking Questions | **0**（问卷已答） |
| Blocking Conflicts | **0** |
| Critical Assumptions | **0** |
| task_level | **L2**（新子系统 · 跨三仓） |
| Gate | **READY_FOR_DEVELOPMENT** |

---

## 变更记录

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-09-14 | v1 | 初版：问卷冻结 DEC-DH-001..007 + 全栈规格 |
| 2026-09-14 | v1.2 | DEC-DH-009..011：测试扩面 work_items、双层终判 HOLD、contract_gap、SPLIT、AC-12..15 |

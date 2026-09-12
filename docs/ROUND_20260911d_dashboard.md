# test-Mind 看板（TestPilot 对标）落地报告 · 2026-09-11

> 目标：对标柠檬 AI 测试・华华的「TestPilot」，给 test-Mind 补一套**页面 + 机制**——
> 让人（和宿主 AI）一眼知道**今天该测什么、从哪进、进来之后还要顺手测什么**。
> 交付方式：自主执行（用户离线授权）。跑完自测，有问题已修。

---

## 0. 一句话结论

看板落地了：**三层零依赖结构（知识层 / 数据层 / 展示层）**，7 个入口覆盖全部 32 个 MCP 工具，
每条入口自带「举一反三」，`reports/` 的 947 个目录被聚合成项目动态。
过程中**修掉 3 个真实缺陷**（其中一个是会把通过率从 95.3% 误报成 21.2% 的假口径），
看板自检已并入 `verify-all.py` 标准验证链。

---

## 1. 交付物

| 文件 | 层 | 作用 |
|---|---|---|
| `testmind/dashboard/playbook.py` | 知识层 | 入口分类（7 组 / 32 工具）+ 举一反三映射 + 四条硬规则 + Q1–Q5 + 10 类缺陷 + 故障五模式 + 模糊需求路由表。**纯数据 + 纯函数** |
| `testmind/dashboard/aggregate.py` | 数据层 | 扫描 `reports/*/gate.json+results.json`、`regression/cases.json`、`.agent/state`、`skills/`、`examples/`。签名缓存、目录缺失/JSON 损坏一律降级 |
| `testmind/dashboard/server.py` | 展示层 | stdlib `http.server`，只读、**只监听 127.0.0.1**、静态资源白名单 |
| `testmind/dashboard/index.html` | 展示层 | 浅色中文看板（单文件、无 CDN） |
| `testmind/dashboard/__init__.py` | — | 包说明 |
| `scripts/dashboard.py` | 入口 | `--open` 起服务 / `--check` 只自检 |
| `tests/test_dashboard.py` | 测试 | 24 个用例 |
| `README.md` | 文档 | 新增「看板」章节 + 结构条目 |
| `scripts/verify-all.py` | 门禁 | 新增 `dashboard-check` 步骤（真实仓自检） |

### 看板区块（对标 TestPilot 的四大能力）

| TestPilot | 本看板 | 落地方式 |
|---|---|---|
| 工作拆解开做 | **测试入口分类**（7 组） | 按 MCP 工具的真实职责分组，每组给「什么时候用 + 第一枪命令 + 工具清单」 |
| 单干 + 全链路 | **举一反三 + 模糊需求路由** | 每条入口列出「一条现象 → 该扩展哪些维度 → 依据」；路由表把一句自然语言指到入口 |
| 现场留资产 | **资产库** | 回归锚（含 P0/P1 分布）、技能、规则、示例，全部读真实文件 |
| AI 只当副驾驶 | **测试纪律面板** | 四条硬规则 + 状态词表（只许 7 个）+ Q1–Q5 + 10 类缺陷 + 故障五模式 |
| 首页看今天 | **总览 + 今日待处理 + 近 30 天动态** | 来自 `.agent/state` 与 `reports/` |

---

## 2. 过程中发现并修复的真实缺陷

### 缺陷 1（最严重）：通过率假口径 —— 21.2% 其实是 95.3%

第一版 `scan_reports` 只扫**最近 80 个**报告目录。而实测 947 个目录里：

- 有产物（gate.json 或 results.json）的只有 **233 个**
- **714 个是空壳**（只建了目录，没有任何产物）

空壳目录被算进了通过率分母 → 显示 **21.2%**；而真实门禁分布是
**PASS 164 / FAIL 8 / NOT_TESTED 56 / TOOL_ERROR 5**，真实通过率 **95.3%**。

**修法**：全量扫描（实测 947 目录 stat ≈0.23s、解析 194 个 gate ≈0.18s，签名缓存后每轮只算一次）；
新增 `EMPTY` 状态区分空壳；通过率口径明确为 `PASS/(PASS+FAIL)`，非判定态与空壳单独列。
新增回归测试 `test_empty_shell_is_counted_but_excluded_from_verdict` 锁死这个口径。

### 缺陷 2：`bundle` 缓存键漏了 `limit`

先请求 `limit=10` 再请求 `limit=40`，会拿到被截断的旧包。
**修法**：缓存键改为 `_signature(root) + (limit,)`；新增测试 `test_bundle_cache_key_includes_limit`。

### 缺陷 3：时间线被空壳淹没

`activity` 原本把所有目录都列出来，40 行里 30+ 行是空壳，看不到真正的动态。
**修法**：时间线只列有产物的运行。

---

## 3. 关键设计决策（以及为什么）

1. **零依赖、只读、只听回环**。test-Mind 主包 `dependencies = []`，看板不能破这个约定；
   看板会暴露本机路径与报告内容，所以**不提供 `--host 0.0.0.0`**。
2. **知识层与数据层分离**。`playbook` 是可断言的纯数据，`aggregate` 是纯函数。
   这样"举一反三"的内容可以被单测锁住，而不是散在 HTML 字符串里。
3. **强不变式：7 组入口必须恰好覆盖 `tool_schemas.SCHEMAS` 的全部工具**。
   新增工具忘了归类 → 测试直接红。这是防止看板悄悄漏掉入口的机制保证。
4. **"没把握就不指"**。路由表匹配不到就返回空，宁可让用户看入口分类，也不乱指一个入口。
5. **看板不进 MCP 工具面**。宿主 AI 用 `context_*`，人用看板；不为了让 AI 读看板而多塞工具。

---

## 4. 验收证据

### 4.1 单测

```
python -m unittest tests.test_dashboard -q
Ran 24 tests ... OK
```

覆盖：7 组覆盖全部 32 工具（强不变式）、工具不重复归类、深拷贝隔离、路由命中/不命中、
报告解析、空壳语义、非判定态隔离、缓存命中与失效、缓存键含 limit、目录缺失/JSON 损坏降级、
以及 6 个 HTTP 端点（含 404）。

### 4.2 真实仓自检

```
python scripts/dashboard.py --check
报告目录 : 总 947 / 有效 233 / 空壳 714 (75.4%)
门禁     : PASS 164 / FAIL 8 / NOT_TESTED 56 / TOOL_ERROR 5
通过率   : 95.3%  （口径 PASS/(PASS+FAIL)，近7天 212 次）
用例     : 6069（失败 28）
回归锚   : 4
入口分类 : 7 组 / 32 个工具
待处理项 : 3
```

### 4.3 HTTP 端点实测

```
GET /api/health  → {"ok":true,"version":"1.2.0"}
GET /api/bundle  → total_dirs=947 effective=233 empty=714 pass_rate=95.3
                   trend_days=30 activity=40 entries=7 todo=3
GET /api/route?q=并发会不会超发
                 → {"hits":[{"entry":"execute","next":"gate",...}]}
GET /            → 200, 16299 bytes, 标题命中
```

### 4.4 全链回归

`python scripts/verify-all.py` —— 8 道门 **全 PASS**：

```
unittest          PASS  (68.9s, 104 tests)
dashboard-check   PASS  (1.7s)   ← 本轮新增
e2e               PASS  (99.7s, 114 cases)
mcp-smoke         PASS  (85.5s)
gate-envelope     PASS  (1.8s)
peak-gate         PASS  (194.6s, TM0–TM9)
brain-register    PASS  (6.2s, R1–R14)
compress-claims   PASS  (14.0s, C0–C15)
# VERIFY-ALL = PASS
```

> 看板已并入标准验证链，所以它不会像一次性页面那样腐化。

---

## 5. 诚实边界

1. **看板是只读聚合，不改任何状态**。它不写报告、不改回归锚；"入库"这类动作仍需走 MCP 工具 + 人工审核。
2. **没有做审核流引擎**。TestPilot 的「草稿→待审核→可上线」状态机需要持久化的审核表；
   本仓现有产物里没有这个状态字段，我**没有凭空造一套**，只在纪律面板把"AI 只当副驾驶"的规则显式列出。
   要做真审核流，需要先定存储位置（建议 `.agent/state/review_state.json`）。
3. **没有抄 TestPilot 的视觉**。只对标了信息结构（入口分类 / 资产 / 审核 / 首页），
   样式是按本仓浅色主题重画的。
4. **空壳目录没有清理**。714 个空壳是数据卫生问题（Q2 范畴），但**删除属于破坏性操作**，
   按你的习惯我没有动，只把它作为看板指标暴露出来——要不要清、留哪些，等你拍板。
5. **未提交 git**。test-Mind 仓当前有本轮新增（看板 5 个文件 + 1 脚本 + 1 测试）与改动（README / verify-all），
   未提交。

---

## 6. 建议的下一步

1. **清空壳**：714 个空壳目录建议归档或删除（可先移出仓外备份），看板指标会立刻回到真实水位。
2. **审核流**：若要补 TestPilot 的审核机制，先定 `.agent/state/review_state.json` 的 schema。
3. **看板进 MCP**：如果希望宿主 AI 也能读看板结论，可加一个只读工具
   （但按 DEEP-DEV-PLAN「工具面收敛」的方向，我倾向于不加）。

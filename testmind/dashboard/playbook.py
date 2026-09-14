"""看板知识层 —— 把 SKILL.md 的测试纪律结构化成「入口分类 + 举一反三」。

设计原则：
- **纯数据 + 纯函数**，零依赖、零 IO，可被单测直接断言。
- 内容全部来自仓内既有事实源：`skills/testmind/SKILL.md`（四条硬规则 / 问题分类 /
  测试矩阵最低覆盖 / 静态预检边界 / 三步接入 / 故障注入五模式）与
  `问题文档.md` §0.1（Q1–Q5 质量维度）。不发明新口径。
- §29 工具面收敛后，入口分类覆盖且仅覆盖 7 个门面工具。

为什么要有这一层：TestMind 的 MCP 工具是"动作"，但使用者（人或宿主 AI）拿到一个
模糊需求时真正缺的是"**该从哪进、进来之后还要顺手测什么**"。前者是入口分类，
后者是举一反三。两者都必须是确定性数据，不能靠模型即兴发挥。
"""

# ─────────────────────────── 硬规则 / 状态词 ───────────────────────────

HARD_RULES = [
    {"id": "R1", "text": "无需求→无预期", "detail": "预期必须有事实来源（需求/代码/DB约束/契约/历史）。查不到→标 UNKNOWN，先查尽代码、schema、契约、git history、相邻实现。"},
    {"id": "R2", "text": "未执行→无 PASS", "detail": "禁止「应该没问题/理论上通过」。没跑过只有 NOT_TESTED。"},
    {"id": "R3", "text": "无证据→无完成", "detail": "宣称完成必须有：命令、exit code、HTTP response、DB before/after、日志。"},
    {"id": "R4", "text": "业务数据禁随机", "detail": "每个测试值都要能回答「为什么合法 / 为什么故意非法」。"},
]

STATUS_VOCAB = ["PASS", "FAIL", "HOLD", "BLOCKED", "NOT_TESTED", "TOOL_ERROR", "SKIPPED_WITH_REASON"]

FACT_KINDS = [
    {"id": "CONFIRMED", "text": "有明确事实证据"},
    {"id": "DERIVED", "text": "从多个 CONFIRMED 严格推出（记录 derived_from）"},
    {"id": "UNKNOWN", "text": "事实源查尽仍不能定 → USER_REQUIRED"},
    {"id": "CONFLICT", "text": "多源矛盾 → BLOCKED_BY_CONFLICT，禁止自行选一个"},
]

# 故障注入五模式（FaultProxy）—— ok 是对照组，缺它就会假绿
FAULT_MODES = [
    {"id": "ok", "text": "对照组，必须 2xx", "why": "没有对照组，故障路径的 4xx 可能本来就该 4xx → 假绿"},
    {"id": "status", "text": "返回指定错误码"},
    {"id": "delay", "text": "延迟响应"},
    {"id": "refuse", "text": "拒绝连接"},
    {"id": "bad_json", "text": "返回非法 JSON"},
]

# Q1–Q5 质量维度（问题文档.md §0.1）
QUALITY_DIMENSIONS = [
    {"id": "Q1", "text": "逻辑缺陷", "how": "边界/状态机/幂等/事务/超发/跨租户/并发竞态/条件反转/吞异常 → 10 类注入 ≥9/10 检出、0 假绿"},
    {"id": "Q2", "text": "代码简洁度", "how": "重复块/上帝类/超长方法/死代码/魔法数/复杂度 → 可量化清单，指出≠强制改"},
    {"id": "Q3", "text": "SQL 效率", "how": "N+1/缺索引/全表扫描/SELECT */隐式转换/无 LIMIT/JOIN 顺序 → EXPLAIN+计时，替代写法须结果集一致"},
    {"id": "Q4", "text": "接口慢/卡顿", "how": "单接口 P50/P95 实测（非压测）/阻塞调用/大对象序列化/连接池/锁等待/GC → 定位到具体行+数据规模；空库的「快」不算"},
    {"id": "Q5", "text": "根因定位链", "how": "每个发现必须附 [现象][触发条件][代码路径(文件:行)][证据][最小复现][修复建议]，缺链=未完成"},
]

# 10 类缺陷注入（Q1 的展开，也是「举一反三」的检查清单来源）
DEFECT_CLASSES = [
    "边界（min-1 / max+1）",
    "状态机（非法状态迁移）",
    "幂等（重复提交）",
    "事务（部分成功残留）",
    "超发（剩余 1 + 并发 10）",
    "跨租户（越权读写）",
    "并发竞态",
    "条件反转（if 写反）",
    "吞异常（catch 后静默）",
    "脏连接（keep-alive 复用污染）",
]

# 标准 MCP 调用顺序（§30 新管道，7 门面）
PIPELINE = [
    "analyze_impact", "plan_verification", "prepare_verification",
    "run_verification", "(replay_failure 若有 FAIL)", "final_gate", "cleanup",
]

# ─────────────────────────── 入口分类（覆盖全部 7 个门面） ───────────────────────────

ENTRY_GROUPS = [
    {
        "id": "change",
        "title": "变更分析",
        "subtitle": "改动来了先看影响面，别只测改的那个方法本身",
        "when": "拿到一个 PR / 一次提交 / 一个新接入的项目",
        "first_call": "analyze_impact {path}",
        "tools": ["analyze_impact"],
        "extrapolate": [
            {"from": "改了一个 Service 方法",
             "expand": ["调用链上游（谁调它）", "调用链下游（它调谁）", "同事务内的其它写操作"],
             "why": "SKILL.md：改了 Service 先看调用链，禁止只测改动的方法本身"},
            {"from": "改了 DDL / 加了列",
             "expand": ["存量数据回填", "NOT NULL 无默认值", "数值列进 CHECK 范围", "字符串进数值列"],
             "why": "run_verification 的 precheck 开关能从 DDL 确定性推出的四类缺陷"},
            {"from": "改了 Java 接口签名",
             "expand": ["所有实现类", "所有调用点", "序列化字段兼容性"],
             "why": "analyze_impact 的 java_graph 可枚举实现与引用，避免漏改"},
        ],
    },
    {
        "id": "plan",
        "title": "事实澄清与用例设计",
        "subtitle": "先把「预期」钉死，再谈测；查不到就标 UNKNOWN，别编",
        "when": "准备写用例前 / 多源口径打架时 / 要出测试矩阵时",
        "first_call": "plan_verification {schema_sql|openapi|facts|contract|plan}",
        "tools": ["plan_verification"],
        "extrapolate": [
            {"from": "文档说上限 1000、代码说 10000",
             "expand": ["标 CONFLICT + BLOCKED_BY_CONFLICT", "禁止自行选一个", "拉出两处 source 让 owner 拍板"],
             "why": "问题分类：多源矛盾不得静默选边"},
            {"from": "某字段约束查不到",
             "expand": ["先查 schema", "再查契约/OpenAPI", "再查 git history", "再查相邻实现", "全查尽才 ask"],
             "why": "硬规则 R1：查不到→UNKNOWN，先查尽才问"},
            {"from": "一个必填字段",
             "expand": ["缺失", "null", "空串", "纯空格", "类型错误"],
             "why": "SKILL.md：每个必填字段逐个测，不许一把全删只拿一个 400"},
            {"from": "一个数值字段",
             "expand": ["min-1", "min", "max", "max+1", "非整数", "bool 冒充 int"],
             "why": "测试矩阵 P1：参数边界逐字段"},
            {"from": "一个 P0 风险点（金额/权限/超发/状态机/事务/幂等/跨租户/泄露）",
             "expand": DEFECT_CLASSES,
             "why": "P0 覆盖清单 = 10 类缺陷注入"},
        ],
    },
    {
        "id": "execute",
        "title": "环境与执行",
        "subtitle": "服务起不来≠只做静态；能真跑的一律真跑",
        "when": "要连 DB / 起服务 / 跑用例 / 注入故障 / 扫 SQL 时",
        "first_call": "prepare_verification {env:{base_url|sut, db, tables}}",
        "tools": ["prepare_verification", "run_verification"],
        "extrapolate": [
            {"from": "一条普通正例跑通了",
             "expand": ["并发：剩余 1 + 并发 10 → 不许超发", "幂等：重复提交", "DB 终态双断言"],
             "why": "SKILL.md：并发与幂等必须执行引擎真实跑，静态抓不到"},
            {"from": "要测下游依赖异常",
             "expand": ["ok 对照组必须 2xx", "status 错误码", "delay 延迟", "refuse 拒连", "bad_json 非法体"],
             "why": "故障注入五模式；ok 缺了就会假绿"},
            {"from": "DB 给不了连接串",
             "expand": ["db=none（API-only）", "DB 断言记 NOT_TESTED", "禁止假通过"],
             "why": "SKILL.md 步骤 2：没有 DB 就不许声称 DB 断言通过"},
            {"from": "空库上跑 SQL 很快",
             "expand": ["先 seed 贴近生产的量", "再计时", "否则判定为假快"],
             "why": "run_verification 的 perf 开关：空库上的「快」不算"},
            {"from": "静态抓到 INSERT 列数错位",
             "expand": ["报「巡检发现 N 个问题 + 尚未执行」", "不许当成 FAIL 结论", "补执行用例才算闭环"],
             "why": "precheck 开关不产生判定；没执行 final_gate 照样 NOT_TESTED"},
        ],
    },
    {
        "id": "gate",
        "title": "门禁与收尾",
        "subtitle": "修了 bug 就留锚；未执行不得放行；PASS 回滚 / FAIL 冻结",
        "when": "跑完一轮要出结论 / 失败要复现 / 修完缺陷要固化 / 收尾时",
        "first_call": "final_gate {}",
        "tools": ["replay_failure", "final_gate", "cleanup"],
        "extrapolate": [
            {"from": "修好一个缺陷",
             "expand": ["run_verification 的 add_regression 永久留锚", "标 priority", "写清 source=缺陷描述"],
             "why": "SKILL.md：修了 bug → 生成 Regression Case 永久保留"},
            {"from": "一轮跑完要放行",
             "expand": ["final_gate（未执行→NOT_TESTED）", "coverage 只当遗漏探测器", "failures 逐条要证据"],
             "why": "覆盖率是遗漏探测器，不是正确性证明；四件套随 final_gate 自动导出"},
            {"from": "有个 case 挂了",
             "expand": ["replay_failure 按 case_id 重跑单 case + 证据", "cleanup FAIL 时冻结现场不清理"],
             "why": "§17：PASS 回滚，FAIL 保留数据供排查/重放"},
            {"from": "跑完一轮",
             "expand": ["cleanup 回收进程树与临时容器", "别留悬空 mysqld/容器"],
             "why": "幂等收尾，防止残留进程锁文件"},
        ],
    },
]

# 面向「模糊需求」的入口路由表：给一句自然语言，指到最合适的入口
ROUTING_HINTS = [
    {"pattern": "支付/订单/金额 对不上、状态停在某处", "entry": "change", "next": "plan", "note": "先看影响面，再补状态机与事务用例"},
    {"pattern": "改了代码/发了版，测过没", "entry": "change", "next": "gate", "note": "analyze_impact → run_verification（regression）"},
    {"pattern": "接口报 500 / 返回不对", "entry": "execute", "next": "gate", "note": "prepare_verification → run_verification → final_gate"},
    {"pattern": "SQL 慢 / 加了索引没", "entry": "execute", "next": "gate", "note": "run_verification 的 perf 开关（先 seed 贴近生产的量）"},
    {"pattern": "并发/重复提交会不会超发", "entry": "execute", "next": "gate", "note": "run_verification 的 concurrency 开关（剩余 1 + 并发 10）"},
    {"pattern": "下游挂了会怎样", "entry": "execute", "next": "gate", "note": "run_verification 的 failure 开关（ok 对照组必须有）"},
    {"pattern": "需求没写清 / 文档和代码打架", "entry": "plan", "next": "execute", "note": "CONFLICT 不许自选，拉 source 让 owner 拍板"},
    {"pattern": "要交给别人 / 接别人的活", "entry": "plan", "next": "gate", "note": "plan_verification 投喂 task_bundle，final_gate 自动导出四件套"},
]


# ─────────────────────────── 纯函数 API ───────────────────────────

def entry_groups():
    """返回入口分类（深拷贝，避免调用方改到模块常量）。"""
    return [
        {**g, "tools": list(g["tools"]),
         "extrapolate": [dict(e, expand=list(e["expand"])) for e in g["extrapolate"]]}
        for g in ENTRY_GROUPS
    ]


def covered_tools():
    """本层分类覆盖到的工具名集合（用于与 tool_schemas 对账）。"""
    out = set()
    for g in ENTRY_GROUPS:
        out.update(g["tools"])
    return out


def route(query):
    """把一句模糊需求路由到入口。返回命中列表（可能为空 = 没把握，别乱指）。"""
    if not query:
        return []
    q = str(query)
    hits = []
    for h in ROUTING_HINTS:
        keys = [k for k in h["pattern"].replace("，", "/").replace("、", "/").split("/") if k]
        if any(k and k in q for k in keys):
            hits.append(dict(h))
    return hits


def playbook_payload():
    """给看板的完整知识层载荷。"""
    return {
        "hard_rules": HARD_RULES,
        "status_vocab": STATUS_VOCAB,
        "fact_kinds": FACT_KINDS,
        "fault_modes": FAULT_MODES,
        "quality_dimensions": QUALITY_DIMENSIONS,
        "defect_classes": DEFECT_CLASSES,
        "pipeline": PIPELINE,
        "entries": entry_groups(),
        "routing": ROUTING_HINTS,
    }

# testmind/isolation.py — V10 §18 Context/Task Isolation
# reset_runtime：清进程/端口/连接等运行时资源；reset_task：清任务态（facts/contract/plan/
# task_id/consumer_root/ledger/data_plan/actors/rules/impact_graph），杜绝跨任务污染。
import time

TASK_FIELDS = ("contract", "plan", "task_id", "consumer_root", "ledger", "data_plan",
               "actors", "rules", "impact_graph", "impact", "mutation", "scenarios", "engine", "db", "proxy", "srv", "sut",
               "sut_proc", "sut_log", "precheck", "sqlperf", "ev", "results", "openapi_url",
               "schema", "hooks", "require_schema", "env_class",
               "kv", "openapi_spec", "triage", "executed_cases")


def reset_runtime(S):
    """运行时资源回收（进程/端口/代理）。不碰任务事实。"""
    from testmind.mcp import _kill_sut  # 延迟导入避免环
    _kill_sut()
    if getattr(S, "srv", None):
        S.srv.shutdown()
    if getattr(S, "proxy", None):
        S.proxy.shutdown()
    for f in ("engine", "db", "proxy", "srv", "sut", "sut_proc", "sut_log", "ev"):
        setattr(S, f, None)


def reset_task(S):
    """任务态清零：§18 清单全清。返回清理报告。"""
    cleared = []
    S.facts = __import__("testmind.core", fromlist=["Facts"]).Facts()
    for f in TASK_FIELDS:
        if hasattr(S, f):
            setattr(S, f, None)
            cleared.append(f)
    S.plan = []
    S.results = []
    S.executed_cases = []
    S.cleared_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    return {"status": "PASS", "cleared": sorted(set(cleared) | {"facts"}), "ts": S.cleared_at}

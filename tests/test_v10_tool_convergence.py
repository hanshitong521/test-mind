# tests/test_v10_tool_convergence.py — V10 §29/AC-7 工具面收敛的"清零"守卫。
# 两层断言：
#   1) 行为层：dispatch 对全部旧工具名一律 TOOL_ERROR（运行时拒绝，最强保证）。
#   2) 文本层：仓库内 *.py 不得把旧工具名当字符串字面量调用（"name"/dispatch("name"），
#      skills/testmind/SKILL.md 不得再出现旧工具名（对外文档与实现一致）。
# 引擎函数名（core.build_contract / _do_collect_facts 等）是合法保留（规格 §87），
# 故文本层只匹配"引号包裹的完整旧名"，不误伤标识符。
import os
import re
import sys
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind import mcp
from testmind.tool_schemas import SCHEMAS

# 规格 §29 旧 32 工具全清单（去向已在 DEVELOPMENT_SPEC 声明，此处仅断言不再作为工具名暴露）
LEGACY_TOOLS = [
    "inspect_project", "analyze_change", "scan_java",
    "collect_facts", "add_facts", "build_contract", "plan_tests", "generate_cases",
    "ask_user", "answer_question", "list_unknowns", "intake_task",
    "prepare_environment", "seed_database", "provision_environment",
    "run_case", "run_suite", "run_concurrency_tests", "run_failure_injection",
    "run_schema_tests", "run_scenario_tests", "run_regression", "add_regression_case",
    "static_precheck", "sql_perf_check",
    "get_coverage", "get_failures", "get_evidence",
    "export_handoff", "run_pipeline",
]

SKIP_DIRS = {".git", "__pycache__", ".testmind", "reports", ".requirementmind", ".agent", ".idea"}
SKILL_PATH = os.path.join(TM, "skills", "testmind", "SKILL.md")


def _iter_py():
    for dirpath, dirnames, filenames in os.walk(TM):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


class TestToolConvergence(unittest.TestCase):
    def test_dispatch_rejects_all_legacy_names(self):
        """行为层：任何旧工具名经 dispatch 必须 TOOL_ERROR，绝不静默执行。"""
        for name in LEGACY_TOOLS:
            with self.subTest(tool=name):
                r = mcp.dispatch(name, {})
                self.assertEqual(r["status"], "TOOL_ERROR",
                                 f"legacy tool {name!r} must not be dispatchable")

    def test_no_legacy_name_as_string_literal_in_py(self):
        """文本层：*.py 不得以引号字面量形式引用旧工具名（杜绝 tools/call 残留）。"""
        self_test = os.path.abspath(__file__)
        patterns = {n: re.compile(r'["\']' + re.escape(n) + r'["\']') for n in LEGACY_TOOLS}
        hits = []
        for path in _iter_py():
            if os.path.abspath(path) == self_test:
                continue  # 本测试文件自带清单，排除自身
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for n, pat in patterns.items():
                if pat.search(text):
                    hits.append(f"{os.path.relpath(path, TM)}: {n}")
        self.assertEqual(hits, [], "legacy tool name still referenced as string literal")

    def test_skill_md_has_no_legacy_tool_names(self):
        """对外文档层：SKILL.md 与实现一致，不得再出现旧工具名。"""
        with open(SKILL_PATH, encoding="utf-8") as fh:
            text = fh.read()
        leaked = [n for n in LEGACY_TOOLS if re.search(r'\b' + re.escape(n) + r'\b', text)]
        self.assertEqual(leaked, [], "SKILL.md still mentions legacy tool names")

    def test_facades_are_exactly_seven(self):
        self.assertEqual(set(mcp.FACADES), set(SCHEMAS))
        self.assertEqual(len(mcp.FACADES), 7)


if __name__ == "__main__":
    unittest.main()

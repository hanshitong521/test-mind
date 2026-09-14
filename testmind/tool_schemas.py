# MCP 工具真实 inputSchema（§38 / W-P0-01；§29 收敛后仅 7 门面）
_SCHEMA_VERSION = 2

_OBJ = {"type": "object"}
_STR = {"type": "string"}
_ARR = {"type": "array"}

SCHEMAS = {
    "analyze_impact": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": _STR,
            "base_sha": _STR,
            "head_sha": _STR,
            "changed": {"type": "object"},
            "java_graph": {"type": "boolean"},
            "inspect": {"type": "boolean"},
            "max_files": {"type": "integer"},
        },
    },
    "plan_verification": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task_bundle": {"type": "object"},
            "consumer_root": _STR,
            "schema_sql": _STR,
            "openapi": {},
            "facts": _ARR,
            "contract": {"type": "object"},
            "plan": {"type": "object"},
            "cases": _ARR,
            "ask": {"type": "object"},
            "answer": {"type": "object"},
            "rules": _ARR,
            "scenario": {"type": "object"},
            "oracle": {"type": "object"},
        },
    },
    "prepare_verification": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "env": {"type": "object"},
            "seed": _ARR,
            "provision": {"type": "object"},
            "data_plan": {"type": "object"},
        },
    },
    "run_verification": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "case_ids": {"type": "array", "items": _STR},
            "case": {"type": "object"},
            "regression": {"type": "boolean"},
            "precheck": {"type": "object"},
            "perf": {"type": "object"},
            "concurrency": {"type": "object"},
            "failure": {"type": "object"},
            "schema": {"type": "object"},
            "scenario": {"type": "object"},
            "add_regression": {"type": "object"},
        },
    },
    "replay_failure": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"case_id": _STR, "path": _STR},
    },
    "final_gate": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"require_schema": {"type": "boolean"},
                       "require_db_evidence": {"type": "boolean"}},
    },
    "cleanup": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"force_rollback": {"type": "boolean"}},
    },
}


def input_schema(tool_name):
    return SCHEMAS.get(tool_name, {"type": "object", "additionalProperties": True})


def schema_version():
    return _SCHEMA_VERSION

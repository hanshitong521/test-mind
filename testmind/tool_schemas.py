# MCP 工具真实 inputSchema（§38 / W-P0-01）
_SCHEMA_VERSION = 1

_OBJ = {"type": "object"}
_STR = {"type": "string"}
_ANY = {}

SCHEMAS = {
    "inspect_project": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"path": _STR},
    },
    "analyze_change": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": _STR,
            "base_sha": _STR,
            "head_sha": _STR,
            "include_untracked": {"type": "boolean"},
        },
    },
    "collect_facts": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_sql": _STR,
            "openapi": {},
            "operation_id": _STR,
            "path": _STR,
            "method": _STR,
        },
    },
    "add_facts": {
        "type": "object",
        "required": ["facts"],
        "additionalProperties": False,
        "properties": {
            "facts": {"type": "array", "items": {"type": "object"}},
        },
    },
    "ask_user": {
        "type": "object",
        "required": ["question"],
        "additionalProperties": False,
        "properties": {
            "question": _STR,
            "why": _STR,
            "impact": _STR,
            "options": {"type": "array"},
        },
    },
    "answer_question": {
        "type": "object",
        "required": ["qid", "answer"],
        "additionalProperties": False,
        "properties": {"qid": _STR, "answer": _STR},
    },
    "list_unknowns": {"type": "object", "additionalProperties": False, "properties": {}},
    "build_contract": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "contract_id": _STR,
            "target": {"type": "object"},
            "inputs": {"type": "object"},
            "expected": {"type": "object"},
            "ok_status": {"type": "integer"},
        },
    },
    "plan_tests": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "method": _STR,
            "path": _STR,
            "base_input": {"type": "object"},
            "ok_status": {"type": "integer"},
            "bad_status": {"type": "integer"},
            "overrides": {"type": "object"},
            "operation_id": _STR,
        },
    },
    "static_precheck": {
        "type": "object",
        "required": ["schema_sql"],
        "additionalProperties": False,
        "properties": {
            "schema_sql": _STR,
            "statements": {"type": "array"},
        },
    },
    "sql_perf_check": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "queries": {"type": "array"},
            "db": {"type": "object"},
            "runs": {"type": "integer"},
            "threshold_ms": {"type": "number"},
            "max_rows": {"type": "integer"},
        },
    },
    "generate_cases": {
        "type": "object",
        "required": ["cases"],
        "additionalProperties": False,
        "properties": {"cases": {"type": "array"}},
    },
    "prepare_environment": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "env_class": {"type": "string", "enum": ["local", "test", "staging", "production", "unknown"]},
            "base_url": _STR,
            "db": {"type": "object"},
            "tables": {"type": "array"},
            "proxy_upstream": _STR,
            "example": _STR,
            "sut": {"type": "object"},
            "openapi_url": _STR,
        },
    },
    "seed_database": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"statements": {"type": "array"}},
    },
    "run_case": {
        "type": "object",
        "required": ["case"],
        "additionalProperties": False,
        "properties": {"case": {"type": "object"}},
    },
    "run_suite": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"case_ids": {"type": "array", "items": _STR}},
    },
    "run_concurrency_tests": {"type": "object", "additionalProperties": True},
    "run_failure_injection": {"type": "object", "additionalProperties": True},
    "run_schema_tests": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"openapi_url": _STR, "required": {"type": "boolean"}},
    },
    "run_scenario_tests": {
        "type": "object",
        "required": ["feature"],
        "additionalProperties": False,
        "properties": {"feature": _STR},
    },
    "provision_environment": {"type": "object", "additionalProperties": True},
    "run_regression": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "project_id": _STR,
            "service_id": _STR,
            "consumer_root": _STR,
        },
    },
    "add_regression_case": {
        "type": "object",
        "required": ["case"],
        "additionalProperties": False,
        "properties": {"case": {"type": "object"}, "reason": _STR, "consumer_root": _STR},
    },
    "get_coverage": {"type": "object", "additionalProperties": False, "properties": {}},
    "get_failures": {"type": "object", "additionalProperties": False, "properties": {}},
    "get_evidence": {"type": "object", "additionalProperties": False, "properties": {}},
    "final_gate": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"require_schema": {"type": "boolean"}},
    },
    "run_pipeline": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_sql": _STR,
            "openapi": {},
            "contract": {"type": "object"},
            "plan": {"type": "object"},
            "env": {"type": "object"},
            "env_class": _STR,
        },
    },
    "scan_java": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"path": _STR, "max_files": {"type": "integer"}},
    },
    "intake_task": {
        "type": "object",
        "required": ["task_bundle"],
        "additionalProperties": False,
        "properties": {
            "task_bundle": {"type": "object"},
            "consumer_root": _STR,
        },
    },
    "export_handoff": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"task_id": _STR},
    },
    "cleanup": {"type": "object", "additionalProperties": False, "properties": {}},
}


def input_schema(tool_name):
    return SCHEMAS.get(tool_name, {"type": "object", "additionalProperties": True})


def schema_version():
    return _SCHEMA_VERSION

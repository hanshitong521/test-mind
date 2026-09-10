# SecretGuard — 证据/MCP/报告出口统一脱敏
import re

_SENSITIVE_KEY = re.compile(
    r"(authorization|api[_-]?key|password|secret|token|credential|bearer)",
    re.I,
)
_SENSITIVE_VALUE = re.compile(
    r"(Bearer\s+)[^\s\"']+|(password|api[_-]?key|secret)\s*[:=]\s*['\"]?([^'\"\s,}]+)",
    re.I,
)


def redact_str(s):
    if not isinstance(s, str):
        return s
    out = _SENSITIVE_VALUE.sub(lambda m: m.group(0)[:8] + "***", s)
    if re.search(r"Bearer\s+\S+", out, re.I):
        out = re.sub(r"(Bearer\s+)\S+", r"\1***", out, flags=re.I)
    return out


def redact_obj(obj):
    if isinstance(obj, dict):
        return {
            k: "***" if _SENSITIVE_KEY.search(str(k)) else redact_obj(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    if isinstance(obj, str):
        return redact_str(obj)
    return obj

# 环境类别与 allowlist（P0 最小 Gate）
import re

PRODUCTION_HINTS = re.compile(
    r"(prod(uction)?\.|\.prod\.|live\.|api\.(?!test)|生产)",
    re.I,
)


def classify_env(env_class=None, base_url=None):
    if env_class:
        return env_class.strip().lower()
    if base_url and PRODUCTION_HINTS.search(base_url):
        return "production"
    return "unknown"


def check_prepare(env_class, base_url, destructive=False, fault=False, concurrency=False):
    """返回 (ok, reason)。unknown/production 默认禁止破坏性动作。"""
    cls = classify_env(env_class, base_url)
    if cls == "unknown" and not env_class:
        return False, "env_class required (local|test|staging|production|unknown); default BLOCKED"
    if cls == "production":
        return False, "production: TestMind prepare blocked — use local/test/staging only"
    if (destructive or fault or concurrency) and cls not in ("local", "test", "staging"):
        return False, f"{cls}: destructive/fault/concurrency not allowed"
    return True, cls

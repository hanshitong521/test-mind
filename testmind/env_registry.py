# 环境档案加载（模拟/真实共用）：只接受 secret_ref，展开值仅进程内短暂存在
import json
import os
import re

from testmind import env_policy

_REF = re.compile(r"^env://([^/]+)/([^/]+)$")


def resolve_ref(environment_ref, consumer_root=None):
    """env://peak-sim/local → consumer_root/env/local.json 或包内 examples。"""
    if not environment_ref:
        return None
    m = _REF.match(environment_ref.strip())
    if m:
        pack, name = m.group(1), m.group(2)
        candidates = []
        if consumer_root:
            candidates.append(os.path.join(consumer_root, "env", f"{name}.json"))
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates.append(os.path.join(root, "examples", pack, "env", f"{name}.json"))
        for p in candidates:
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as fh:
                    return json.load(fh)
        return None
    path = environment_ref
    if consumer_root and not os.path.isabs(path):
        path = os.path.join(consumer_root, path)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def materialize_secrets(profile, secrets_dir=None):
    """模拟环境：从 secrets_dir/*.json 解析 secret_ref，不写回 profile。"""
    out = dict(profile)
    refs = profile.get("secret_refs") or {}
    vault = {}
    if secrets_dir and os.path.isdir(secrets_dir):
        for fn in os.listdir(secrets_dir):
            if fn.endswith(".json"):
                vault[fn[:-5]] = json.load(open(os.path.join(secrets_dir, fn), encoding="utf-8"))
    resolved = {}
    for k, ref in refs.items():
        key = str(ref).split(":")[-1] if ":" in str(ref) else str(ref)
        if key in vault:
            resolved[k] = vault[key].get("value", "***")
    out["_resolved_secrets"] = resolved
    return out


def check_profile(profile):
    if not profile:
        return False, "environment profile not found"
    ok, reason = env_policy.check_prepare(
        profile.get("env_class"),
        profile.get("base_url"),
        destructive=profile.get("capabilities", {}).get("destructive", False),
        fault=profile.get("capabilities", {}).get("fault", False),
        concurrency=profile.get("capabilities", {}).get("concurrency", False),
    )
    return ok, reason

# 最小 Spring/Java 静态扫描（P1）：Controller 映射 + Bean Validation 注解
import os
import re

from testmind.core import Facts

_MAPPING = re.compile(
    r"@(?P<ann>Get|Post|Put|Patch|Delete|Request)Mapping\s*"
    r"(?:\(\s*(?:value\s*=\s*)?[\"'](?P<path>[^\"']+)[\"'])?",
    re.I,
)
_VALIDATION = re.compile(
    r"@(?P<ann>NotNull|NotBlank|NotEmpty|Size|Min|Max|Pattern|DecimalMin|DecimalMax)\s*"
    r"(?:\(\s*(?P<args>[^)]*)\))?",
    re.I,
)
_CLASS = re.compile(r"class\s+(\w+)")


def scan_java_tree(root, max_files=500):
    facts = Facts()
    n = 0
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in (".git", "node_modules", "target", "build", ".gradle")]
        for fn in fns:
            if not fn.endswith(".java"):
                continue
            n += 1
            if n > max_files:
                facts.unparsed.append({"source": root, "what": "java scan truncated", "text": f">{max_files} files"})
                break
            path = os.path.join(dp, fn)
            rel = os.path.relpath(path, root).replace("\\", "/")
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            cls = _CLASS.search(text)
            cname = cls.group(1) if cls else fn
            for m in _MAPPING.finditer(text):
                ann, pth = m.group("ann"), m.group("path") or ""
                verb = "GET" if ann.lower() == "getmapping" else (
                    "POST" if ann.lower() in ("postmapping", "requestmapping") else ann.upper().replace("MAPPING", ""))
                facts.add(f"java:endpoint:{cname}", f"{verb} {pth or '/'}", f"{rel}:{m.start()}")
            for m in _VALIDATION.finditer(text):
                ann, args = m.group("ann"), (m.group("args") or "").strip()
                stmt = f"{ann}({args})" if args else ann
                facts.add(f"java:validation:{cname}", stmt, f"{rel}:{m.start()}")
    return facts

# testmind/kv.py — KV/Redis 副作用断言：补 DBCheck 只管 SQL、验不了 Redis 写入/删除的缺口
# 接口对齐 core.DBCheck 的 snapshot/counts/diff 语义，Engine 复用 before/after diff 模式。
# 依赖：仅 stdlib（socket/fnmatch/re），不引第三方 redis 包。
import fnmatch, re, socket, time

SECRET_RE = re.compile(r"secret|password|token", re.I)   # 与 core._mask 同一套关键词
SNAPSHOT_LIMIT = 1000                                     # 全库快照上限，防大库打爆内存


def open_kv(spec):
    """spec: None|{"kind":"none"}(API-only) | {"kind":"memory"} | {"kind":"redis","args":{host,port,password,db}}
    风格对齐 core.open_db：不认识的种类直接抛，NOT_TESTED beats fake。"""
    if not spec or spec.get("kind") in (None, "none"):
        return None
    if spec["kind"] == "memory":
        return MemoryKV()
    if spec["kind"] == "redis":
        return RedisKV(**(spec.get("args") or {}))
    raise ValueError(f"kv kind {spec['kind']} not supported (NOT_TESTED beats fake)")


class KVCheck:
    """KV 断言基类：子类只需实现 get/keys/ttl 三个原语，snapshot/counts/diff 共享。"""

    truncated = False                                     # 快照是否被截断（同 DBCheck 的诚实标注）

    def get(self, key):
        raise NotImplementedError

    def mget(self, keys):
        keys = list(keys)
        return [self.get(k) for k in keys] if keys else []

    def keys(self, pattern="*"):
        raise NotImplementedError

    def ttl(self, key):
        raise NotImplementedError

    def snapshot(self, keys=None):
        """{key: value} 快照；keys=None 时全库扫描。key 或 value 命中敏感词 → 值置 ***（脱敏）。
        读不到就是读不到，异常向上抛给 Engine 记 TOOL_ERROR，不许静默给空快照骗过 kv_no_write。"""
        ks = list(keys) if keys is not None else list(self.keys("*"))
        ks.sort()
        self.truncated = len(ks) > SNAPSHOT_LIMIT
        ks = ks[:SNAPSHOT_LIMIT]
        vals = self.mget(ks)
        return {k: ("***" if _masked(k, v) else v) for k, v in zip(ks, vals)}

    def counts(self, keys=None):
        """按前缀聚合计数：key 第一个 ':' 之前的段为前缀，无 ':' 则整 key 为前缀。"""
        ks = list(keys) if keys is not None else list(self.keys("*"))
        out = {}
        for k in ks:
            p = k.split(":", 1)[0]
            out[p] = out.get(p, 0) + 1
        return out

    @staticmethod
    def diff(before, after):
        """语义对齐 DBCheck.diff 但针对 KV：added 带新值，removed 只留 key，changed 带前后值。"""
        added = {k: after[k] for k in sorted(set(after) - set(before))}
        removed = sorted(set(before) - set(after))
        changed = {k: {"before": before[k], "after": after[k]}
                   for k in sorted(set(before) & set(after)) if before[k] != after[k]}
        return {"added": added, "removed": removed, "changed": changed}


def _masked(key, value):
    return bool(SECRET_RE.search(str(key))) or (isinstance(value, str) and bool(SECRET_RE.search(value)))


class MemoryKV(KVCheck):
    """纯内存 dict 实现：供单测与无 redis 环境构造前态。ttl 语义对齐 redis：-1 无过期，-2 不存在。"""

    def __init__(self, init=None):
        self._d = dict(init or {})
        self._exp = {}                                    # key -> 过期时刻（time.monotonic 基准）

    def _live(self, key):
        """惰性过期：读时判定，不后台清理。"""
        if key not in self._d:
            return False
        if self._exp.get(key) is not None and time.monotonic() >= self._exp[key]:
            del self._d[key]
            del self._exp[key]
            return False
        return True

    def get(self, key):
        return self._d.get(key) if self._live(key) else None

    def keys(self, pattern="*"):
        for k in list(self._d):
            self._live(k)
        return [k for k in self._d if fnmatch.fnmatchcase(k, pattern)]

    def ttl(self, key):
        if not self._live(key):
            return -2
        d = self._exp.get(key)
        return -1 if d is None else max(0, int(round(d - time.monotonic())))

    # ── 测试辅助（redis 语义子集）──
    def set(self, key, value, ex=None):
        self._d[key] = value
        self._exp[key] = time.monotonic() + ex if ex else None
        return True

    def delete(self, *keys):                                # del 是 python 关键字，命名 delete
        n = 0
        for k in keys:
            if self._live(k):
                del self._d[k]
                self._exp.pop(k, None)
                n += 1
        return n

    def incr(self, key):
        if not self._live(key):
            self.set(key, "1")
            return 1
        try:
            v = str(self._d[key]).strip()
            iv = int(v) + 1
        except (TypeError, ValueError):
            raise ValueError("ERR value is not an integer or out of range")  # 对齐 redis 报错
        self._d[key] = str(iv)
        return iv

    def expire(self, key, seconds):
        if not self._live(key):
            return False
        self._exp[key] = time.monotonic() + seconds
        return True


def encode_command(*args):
    """RESP2 数组编码：*N\\r\\n + 每段 $len\\r\\n<arg>\\r\\n。纯函数，可脱离真实 redis 单测。"""
    out = [f"*{len(args)}\r\n".encode()]
    for a in args:
        b = a if isinstance(a, bytes) else str(a).encode("utf-8")
        out.append(b"$%d\r\n" % len(b) + b + b"\r\n")
    return b"".join(out)


class RedisKV(KVCheck):
    """stdlib socket 手写的极简 RESP2 客户端（GET/MGET/KEYS/TTL/SCAN）。
    连接失败直接抛，不静默返回空：Engine 记 TOOL_ERROR 比假绿可信。"""

    def __init__(self, host, port, password=None, db=0, timeout=5):
        self._sock = socket.create_connection((host, int(port)), timeout=timeout)
        self._f = self._sock.makefile("rb")
        if password:
            self._command("AUTH", password)
        if db:
            self._command("SELECT", db)

    def close(self):
        try:
            self._f.close()
            self._sock.close()
        except OSError:
            pass

    # ── RESP 收发 ──
    def _read_line(self):
        line = self._f.readline()
        if not line:
            raise ConnectionError("redis 连接被对端关闭")
        return line.rstrip(b"\r\n")

    def _read_reply(self):
        line = self._read_line()
        t, rest = line[:1], line[1:]
        if t == b"+":
            return rest.decode("utf-8", "replace")
        if t == b"-":                                       # 服务端错误必须可见
            raise RuntimeError(f"redis error: {rest.decode('utf-8', 'replace')}")
        if t == b":":
            return int(rest)
        if t == b"$":
            n = int(rest)
            if n == -1:
                return None                                 # nil = key 不存在
            data = self._f.read(n)
            self._f.read(2)                                 # 吃掉结尾 \r\n
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                return data                                 # 二进制值原样返回，不假装是文本
        if t == b"*":
            n = int(rest)
            if n == -1:
                return None
            return [self._read_reply() for _ in range(n)]
        raise RuntimeError(f"bad RESP type {t!r}")          # 协议外回复=异常，不猜

    def _command(self, *args):
        self._sock.sendall(encode_command(*args))
        return self._read_reply()

    # ── KVCheck 原语 ──
    def get(self, key):
        return self._command("GET", key)

    def mget(self, keys):
        keys = list(keys)
        return self._command("MGET", *keys) if keys else []

    def keys(self, pattern="*"):
        return self._command("KEYS", pattern) or []

    def ttl(self, key):
        return self._command("TTL", key)

    def scan(self, pattern="*", count=200):
        """大库用 SCAN 替代 KEYS（非阻塞）；snapshot 默认走 KEYS，需要时调用方可显式用 scan 喂 keys。"""
        out, cursor = [], "0"
        while True:
            cursor, batch = self._command("SCAN", cursor, "MATCH", pattern, "COUNT", count)
            out += batch or []
            if cursor == "0":
                return out

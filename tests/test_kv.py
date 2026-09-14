# tests/test_kv.py — KVCheck/MemoryKV/RedisKV/open_kv 单测（unittest 风格，零第三方依赖）
import os
import socket
import sys
import time
import unittest

TM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TM)

from testmind.kv import KVCheck, MemoryKV, RedisKV, encode_command, open_kv


def _redis_alive():
    """探测本机 6379 是否可达（真实集成测试用，不可达则跳过）。"""
    try:
        s = socket.create_connection(("127.0.0.1", 6379), timeout=1)
        s.close()
        return True
    except OSError:
        return False


class TestMemoryKV(unittest.TestCase):
    def test_set_get_delete(self):
        kv = MemoryKV()
        kv.set("a:1", "v1")
        self.assertEqual(kv.get("a:1"), "v1")
        self.assertIsNone(kv.get("missing"))
        self.assertEqual(kv.delete("a:1"), 1)
        self.assertEqual(kv.delete("a:1"), 0)
        self.assertEqual(kv.mget(["a:1", "missing"]), [None, None])

    def test_incr(self):
        kv = MemoryKV()
        self.assertEqual(kv.incr("n"), 1)                # 不存在视 0 起算
        self.assertEqual(kv.incr("n"), 2)
        kv.set("bad", "abc")
        with self.assertRaises(ValueError):              # 非整数报错，对齐 redis，不静默
            kv.incr("bad")

    def test_expire(self):
        kv = MemoryKV()
        kv.set("k", "v", ex=1)
        self.assertEqual(kv.ttl("k"), 1)
        self.assertTrue(kv.expire("k", 0))
        time.sleep(0.05)
        self.assertIsNone(kv.get("k"))                   # 惰性过期
        self.assertEqual(kv.ttl("k"), -2)                # 不存在 = -2
        kv.set("p", "v")
        self.assertEqual(kv.ttl("p"), -1)                # 无过期 = -1

    def test_keys_glob(self):
        kv = MemoryKV({"user:1": "a", "user:2": "b", "order:1": "c"})
        self.assertEqual(sorted(kv.keys("user:*")), ["user:1", "user:2"])
        self.assertEqual(len(kv.keys("*")), 3)


class TestSnapshotMask(unittest.TestCase):
    def test_mask_by_key(self):
        kv = MemoryKV({"api_token": "tk-123", "user:1": "ok"})
        snap = kv.snapshot()
        self.assertEqual(snap["api_token"], "***")       # key 命中敏感词 → 值脱敏
        self.assertEqual(snap["user:1"], "ok")

    def test_mask_by_value(self):
        kv = MemoryKV({"cfg": "password=hunter2"})
        self.assertEqual(kv.snapshot()["cfg"], "***")    # value 命中同样脱敏

    def test_snapshot_subset_keys(self):
        kv = MemoryKV({"a": "1", "b": "2"})
        self.assertEqual(kv.snapshot(keys=["a"]), {"a": "1"})


class TestDiff(unittest.TestCase):
    def test_added_removed_changed(self):
        before = {"keep": "1", "chg": "a", "gone": "x"}
        after = {"keep": "1", "chg": "b", "new": "n"}
        d = KVCheck.diff(before, after)
        self.assertEqual(d["added"], {"new": "n"})
        self.assertEqual(d["removed"], ["gone"])
        self.assertEqual(d["changed"], {"chg": {"before": "a", "after": "b"}})

    def test_no_change(self):
        same = {"k": "v"}
        d = KVCheck.diff(same, dict(same))
        self.assertEqual(d, {"added": {}, "removed": [], "changed": {}})


class TestCounts(unittest.TestCase):
    def test_prefix_aggregation(self):
        kv = MemoryKV({"user:1": "a", "user:2": "b", "order:9": "c", "solo": "d"})
        self.assertEqual(kv.counts(), {"user": 2, "order": 1, "solo": 1})

    def test_counts_subset(self):
        kv = MemoryKV({"user:1": "a", "order:1": "b"})
        self.assertEqual(kv.counts(keys=["user:1"]), {"user": 1})


class TestOpenKV(unittest.TestCase):
    def test_none_and_memory(self):
        self.assertIsNone(open_kv(None))
        self.assertIsNone(open_kv({"kind": "none"}))
        self.assertIsInstance(open_kv({"kind": "memory"}), MemoryKV)

    def test_bad_kind_raises(self):
        with self.assertRaises(ValueError):              # 不认识的 kind 抛错，不 fake
            open_kv({"kind": "etcd"})

    def test_redis_branch(self):
        # 无 redis 环境下构造应抛连接异常而非返回假对象；有则返回 RedisKV
        if _redis_alive():
            kv = open_kv({"kind": "redis", "args": {"host": "127.0.0.1", "port": 6379}})
            self.assertIsInstance(kv, RedisKV)
            kv.close()
        else:
            with self.assertRaises(OSError):
                open_kv({"kind": "redis", "args": {"host": "127.0.0.1", "port": 6399}})


class TestRESPCodec(unittest.TestCase):
    def test_encode_command(self):
        self.assertEqual(encode_command("GET", "k"), b"*2\r\n$3\r\nGET\r\n$1\r\nk\r\n")
        self.assertEqual(encode_command("SET", "k", "v1"),
                         b"*3\r\n$3\r\nSET\r\n$1\r\nk\r\n$2\r\nv1\r\n")
        self.assertEqual(encode_command("PING"), b"*1\r\n$4\r\nPING\r\n")

    def test_encode_utf8_length(self):
        # 中文按 utf-8 字节数计长度（"键"=3 字节），不是字符数
        self.assertEqual(encode_command("GET", "键"), b"*2\r\n$3\r\nGET\r\n$3\r\n\xe9\x94\xae\r\n")

    def test_read_reply_via_fake_socket(self):
        # 用内存字节流喂解析器：覆盖 + - : $ * 五种回复，不依赖真实 redis
        class _Fake:
            def __init__(self, data):
                import io
                self._f = io.BytesIO(data)
            def makefile(self, mode):
                return self._f
            def sendall(self, b):
                pass

        r = RedisKV.__new__(RedisKV)
        r._f = _Fake(b"+OK\r\n$-1\r\n:42\r\n$3\r\nabc\r\n*2\r\n$1\r\na\r\n:7\r\n").makefile("rb")
        self.assertEqual(r._read_reply(), "OK")
        self.assertIsNone(r._read_reply())                # $-1 = nil
        self.assertEqual(r._read_reply(), 42)
        self.assertEqual(r._read_reply(), "abc")
        self.assertEqual(r._read_reply(), ["a", 7])
        r._f = _Fake(b"-ERR unknown\r\n").makefile("rb")
        with self.assertRaises(RuntimeError):             # 服务端错误必须抛
            r._read_reply()


@unittest.skipIf(not _redis_alive(), "127.0.0.1:6379 不可达，跳过真实 redis 集成测试")
class TestRedisLive(unittest.TestCase):
    def test_roundtrip(self):
        kv = RedisKV("127.0.0.1", 6379)
        key = "testmind:kv:test"
        try:
            kv._command("SET", key, "1")
            self.assertEqual(kv.get(key), "1")
            self.assertEqual(kv._command("DEL", key), 1)
            self.assertIsNone(kv.get(key))
        finally:
            kv._command("DEL", key)
            kv.close()


if __name__ == "__main__":
    unittest.main()

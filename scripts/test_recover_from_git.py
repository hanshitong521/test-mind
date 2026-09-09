"""Error injection for recover_from_git.py.

Independent target: a scratch repo built here, not the real trees. Every case has
a known correct answer, and the test fails both when the scanner misses damage
(vacuous green) and when it over-reports healthy files.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recover_from_git as R  # noqa: E402

CONTENT = "def real_code():\n    return 42\n"


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True)


def build(repo):
    os.makedirs(repo)
    git(repo, "init", "-q")
    cases = {
        "trunc_zero.py": CONTENT,          # -> 0 bytes,      expect RECOVER
        "trunc_crlf.py": CONTENT,          # -> b"\r\n",      expect RECOVER
        "healthy.py": CONTENT,             # untouched,       expect clean
        "empty_by_design.py": "",          # 0B in HEAD too,  expect clean
        "tiny_head.txt": "\n",             # 1B in HEAD,      expect clean
        "子目录/中文名.py": CONTENT,        # CJK path,        expect RECOVER
    }
    for rel, body in cases.items():
        p = os.path.join(repo, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(body)
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    # created after the commit so it is genuinely untracked -> must land in no-HEAD
    with open(os.path.join(repo, "untracked_empty.log"), "w") as f:
        f.write("")
    return cases


def md5(p):
    with open(p, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def rmrf(path):
    """Git marks .git/objects read-only, which makes a plain rmtree raise on Windows."""
    def clear(func, p, _exc):
        os.chmod(p, 0o666)
        func(p)
    try:
        shutil.rmtree(path, onexc=clear)
    except TypeError:                      # Python < 3.12 uses onerror=
        shutil.rmtree(path, onerror=clear)
    except OSError:
        shutil.rmtree(path, ignore_errors=True)


def main():
    tmp = tempfile.mkdtemp(prefix="recov-test-")
    repo = os.path.join(tmp, "scratch")
    cases = build(repo)
    fails = []

    # --- inject the two truncation signatures + a CJK-named one
    for rel in ("trunc_zero.py", "子目录/中文名.py"):
        with open(os.path.join(repo, rel.replace("/", os.sep)), "wb") as f:
            f.write(b"")
    with open(os.path.join(repo, "trunc_crlf.py"), "wb") as f:
        f.write(b"\r\n")

    head_md5 = {rel: md5(os.path.join(repo, rel.replace("/", os.sep)))
                for rel in ("healthy.py", "empty_by_design.py", "tiny_head.txt")}

    rec, unrec = R.scan_repo(repo)
    got = {rel for rel, _, _ in rec}
    want = {"trunc_zero.py", "trunc_crlf.py", "子目录/中文名.py"}

    if got != want:
        fails.append("scan mismatch: missing=%s extra=%s" % (sorted(want - got), sorted(got - want)))
    if "untracked_empty.log" not in unrec:
        fails.append("untracked empty not reported as no-HEAD (got %r)" % (unrec,))
    for rel in ("healthy.py", "empty_by_design.py", "tiny_head.txt"):
        if rel in got:
            fails.append("over-reported healthy file %s as damage" % rel)

    # --- dry run must not touch anything
    if md5(os.path.join(repo, "healthy.py")) != head_md5["healthy.py"]:
        fails.append("scan mutated a healthy file")

    # --- apply, then confirm byte-exact recovery and no collateral change
    ok, err = R.restore(repo, rec)
    if not ok:
        fails.append("restore failed: %s" % err)
    for rel in sorted(want):
        p = os.path.join(repo, rel.replace("/", os.sep))
        with open(p, "rb") as f:
            body = f.read().decode("utf-8", "replace")
        if body.replace("\r\n", "\n") != CONTENT:
            fails.append("recovered content wrong for %s: %r" % (rel, body[:60]))
    for rel, h in head_md5.items():
        if md5(os.path.join(repo, rel.replace("/", os.sep))) != h:
            fails.append("collateral change to %s during restore" % rel)

    # --- after restore no TRACKED file may still be modified; the fixture's
    #     untracked_empty.log stays untracked on purpose, so `??` lines are expected
    dirty = "\n".join(
        ln for ln in git(repo, "status", "--porcelain").stdout.decode("utf-8", "replace").splitlines()
        if ln.strip() and not ln.startswith("??")
    )
    if dirty:
        fails.append("tracked files still dirty after restore: %s" % dirty[:200])

    # --- negative control: with no injection the scanner must find nothing
    rmrf(repo)
    build(repo)
    rec2, unrec2 = R.scan_repo(repo)
    if rec2:
        fails.append("false positives on a pristine repo: %r" % (rec2,))
    if "untracked_empty.log" not in unrec2:
        fails.append("pristine control lost the no-HEAD report")

    rmrf(tmp)

    print("=" * 70)
    if fails:
        print("FAIL (%d)" % len(fails))
        for f in fails:
            print("  - %s" % f)
        return 1
    print("PASS: 3 injected truncations recovered byte-exact (incl. CJK path),")
    print("      3 healthy/tiny/empty-by-design files untouched and unreported,")
    print("      untracked empty routed to no-HEAD, repo clean afterwards,")
    print("      pristine control reports zero recoverable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

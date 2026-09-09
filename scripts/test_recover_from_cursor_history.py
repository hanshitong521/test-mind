"""Error injection for recover_from_cursor_history.py.

Independent target: a synthetic Cursor history and a synthetic damaged tree, both
built here. The cases target the two ways this recovery can be silently wrong --
picking a snapshot that is itself truncated, and failing to match a resource URI
to a real path -- plus the buckets for files it genuinely cannot help.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recover_from_cursor_history as R  # noqa: E402


def uri(path):
    """Build the file URI Cursor writes: forward slashes, percent-encoded colon."""
    return "file:///" + urllib.parse.quote(path.replace("\\", "/"), safe="/")


def add_history(hist, folder, resource, snaps):
    """snaps: list of (timestamp_ms, source_label, bytes)."""
    d = os.path.join(hist, folder)
    os.makedirs(d, exist_ok=True)
    entries = []
    for i, (ts, src, body) in enumerate(snaps):
        sid = "snap%d.py" % i
        with open(os.path.join(d, sid), "wb") as f:
            f.write(body)
        entries.append({"id": sid, "source": src, "timestamp": ts})
    with open(os.path.join(d, "entries.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "resource": resource, "entries": entries}, f)


def main():
    tmp = tempfile.mkdtemp(prefix="hist-test-")
    hist = os.path.join(tmp, "hist")
    target = os.path.join(tmp, "target")
    os.makedirs(hist)
    os.makedirs(os.path.join(target, "子目录"))
    fails = []

    def damaged(rel, body=b""):
        p = os.path.join(target, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(body)
        return p

    # A: newest snapshot is itself truncated -> must fall back to the middle one
    pA = damaged("picks_middle.py")
    add_history(hist, "-a", uri(pA), [
        (1000, "Edit", b"OLDEST VERSION\n"),
        (2000, "Edit", b"MIDDLE VERSION\n"),
        (3000, "Save", b""),                      # post-truncation snapshot
    ])

    # B: every snapshot empty -> unrecoverable bucket, must not be written
    pB = damaged("all_empty.py")
    add_history(hist, "-b", uri(pB), [(1000, "Edit", b""), (2000, "Save", b"\r\n")])

    # C: no history folder at all
    pC = damaged("no_history.py")

    # D: CJK path, and the URI uses forward slashes + %3A while the real path uses
    #    backslashes -- matching must survive normalisation
    pD = damaged("子目录/中文文件.py")
    add_history(hist, "-d", uri(pD), [(1500, "Edit", "内容正确\n".encode("utf-8"))])

    # E: healthy file that HAS history -- must never be touched
    pE = os.path.join(target, "healthy.py")
    with open(pE, "w", encoding="utf-8") as f:
        f.write("print('fine')\n")
    healthy_before = open(pE, "rb").read()
    add_history(hist, "-e", uri(pE), [(9000, "Edit", b"SOMETHING ELSE\n")])

    R.HISTORY = hist
    R.TARGET_ROOTS = [target]

    # --- dry run: correct plan, nothing written
    argv = sys.argv
    buf = io.StringIO()
    sys.argv = ["x"]
    try:
        with contextlib.redirect_stdout(buf):
            rc = R.main()
    finally:
        sys.argv = argv
    out = buf.getvalue()
    if rc != 0:
        fails.append("dry run returned %d" % rc)
    if "recoverable=2" not in out:
        fails.append("dry run did not plan exactly 2 recoveries; got:\n%s" % out[-400:])
    if open(pA, "rb").read() != b"":
        fails.append("dry run modified picks_middle.py")

    # --- apply
    buf2 = io.StringIO()
    sys.argv = ["x", "--apply"]
    try:
        with contextlib.redirect_stdout(buf2):
            rc2 = R.main()
    finally:
        sys.argv = argv
    out2 = buf2.getvalue()
    if rc2 != 0:
        fails.append("apply returned %d: %s" % (rc2, out2[-400:]))

    gotA = open(pA, "rb").read()
    if gotA != b"MIDDLE VERSION\n":
        fails.append("picked the wrong snapshot for picks_middle.py: %r" % gotA)
    gotD = open(pD, "rb").read()
    if gotD != "内容正确\n".encode("utf-8"):
        fails.append("CJK path not recovered: %r" % gotD)
    if open(pB, "rb").read() != b"":
        fails.append("wrote to a file whose snapshots are all empty")
    if open(pC, "rb").read() != b"":
        fails.append("wrote to a file with no history")
    if open(pE, "rb").read() != healthy_before:
        fails.append("overwrote a healthy file that had history")
    if "no history=1" not in out2 or "all-snapshots-empty=1" not in out2:
        fails.append("unrecoverable buckets not reported correctly")

    # --- idempotence: a second apply must change nothing further
    buf3 = io.StringIO()
    sys.argv = ["x", "--apply"]
    try:
        with contextlib.redirect_stdout(buf3):
            R.main()
    finally:
        sys.argv = argv
    if "recoverable=0" not in buf3.getvalue():
        fails.append("second apply still planned work: %s" % buf3.getvalue()[-300:])

    shutil.rmtree(tmp, ignore_errors=True)
    print("=" * 74)
    if fails:
        print("FAIL (%d)" % len(fails))
        for f in fails:
            print("  - %s" % f)
        return 1
    print("PASS: fell back past a truncated snapshot to the newest usable one,")
    print("      recovered a CJK path via URI normalisation, left all-empty and")
    print("      no-history files in their own reported buckets untouched,")
    print("      never overwrote a healthy file, and a second apply is a no-op.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

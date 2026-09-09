"""Recover truncated files from Cursor's local file history.

Dry run:  python scripts/recover_from_cursor_history.py
Apply:    python scripts/recover_from_cursor_history.py --apply

Cursor is VS Code-based and keeps a local snapshot per edited file under
%APPDATA%\\Cursor\\User\\History\\<hash>\\, indexed by entries.json:
    {"resource": "file:///e%3A/path/to/file.py",
     "entries": [{"id": "2Z2H.py", "source": "Undo Create Diff", "timestamp": 1788572940599}]}

This matters because it is the only recovery source for trees with no git --
shejiuPro has 829 history folders and 75 truncated files, and its .cursor/ tree
is gitignored, so neither git nor a protection baseline can help it.

A snapshot is usable only if it is itself non-empty: Cursor snapshots the file as
it was when edited, so a snapshot taken after the truncation is empty too. We take
the newest non-empty one and report its provenance and age.
"""
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protect_trees as P  # noqa: E402

HISTORY = os.path.join(os.environ.get("APPDATA", r"C:\Users\Administrator\AppData\Roaming"),
                       "Cursor", "User", "History")
TARGET_ROOTS = [
    r"E:\workA\A-skill",
    r"E:\workA\shejiuPro",
    r"E:\AAAAAA\A-skill-work",
    r"E:\AAAAAA\github-skill",
]
EMPTY = (b"", b"\r\n", b"\n")


def norm(path):
    """Case-folded, backslash form, drive uppercased -- Windows paths arrive in
    several shapes (file URI, git ls-files, os.walk) and must compare equal."""
    p = path.replace("/", "\\")
    if len(p) > 1 and p[1] == ":":
        p = p[0].upper() + p[1:]
    return p.lower()


def build_index():
    """norm(resource) -> list of (timestamp_ms, snapshot_path, source_label)."""
    index = {}
    folders = 0
    for name in os.listdir(HISTORY):
        d = os.path.join(HISTORY, name)
        ej = os.path.join(d, "entries.json")
        if not os.path.isfile(ej):
            continue
        try:
            with open(ej, encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        res = meta.get("resource") or ""
        if not res.startswith("file:"):
            continue
        path = urllib.parse.unquote(res[len("file:///"):])
        folders += 1
        snaps = []
        for e in meta.get("entries") or []:
            sid = e.get("id")
            if not sid:
                continue
            sp = os.path.join(d, sid)
            if os.path.isfile(sp):
                snaps.append((e.get("timestamp") or 0, sp, e.get("source") or ""))
        if snaps:
            index.setdefault(norm(path), []).extend(snaps)
    for v in index.values():
        v.sort(reverse=True)                 # newest first
    return index, folders


def best_snapshot(snaps):
    """Newest snapshot that is not itself empty. Returns (ts, path, source, size)."""
    for ts, sp, src in snaps:
        try:
            with open(sp, "rb") as f:
                data = f.read()
        except OSError:
            continue
        if data in EMPTY:
            continue
        return ts, sp, src, len(data)
    return None


def damaged_under(root):
    """Confirmed damage only -- benign and ambiguous buckets are protect_trees' job."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in P.SKIP_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in P.SKIP_EXT:
                continue
            p = os.path.join(dirpath, fn)
            if os.path.islink(p):
                continue
            bad, sz = P.is_damaged(p)
            if not bad:
                continue
            rel = os.path.relpath(p, root)
            if P.is_benign_empty(rel, fn) or fn in P.AMBIGUOUS_EMPTY:
                continue
            out.append((p, sz, os.path.getmtime(p)))
    return out


def main():
    apply = "--apply" in sys.argv
    if not os.path.isdir(HISTORY):
        print("ABORT: no Cursor history at %s" % HISTORY)
        return 1
    index, folders = build_index()
    print("=" * 86)
    print("Cursor local-history recovery%s" % ("  [APPLY]" if apply else "  [dry run]"))
    print("=" * 86)
    print("history folders indexed: %d   distinct resources: %d" % (folders, len(index)))

    planned, no_history, all_empty = [], [], []
    for root in TARGET_ROOTS:
        if not os.path.isdir(root):
            print("\n!! root absent: %s" % root)
            continue
        dmg = damaged_under(root)
        if not dmg:
            continue
        print("\n### %s   (%d confirmed damaged)" % (root, len(dmg)))
        for path, sz, mt in sorted(dmg):
            snaps = index.get(norm(path))
            if not snaps:
                no_history.append(path)
                continue
            best = best_snapshot(snaps)
            if best is None:
                all_empty.append((path, len(snaps)))
                continue
            ts, sp, src, size = best
            planned.append((path, sz, sp, size, ts, src))
            print("   %2dB -> %7dB  %-19s %-9s %s"
                  % (sz, size, time.strftime("%Y-%m-%d %H:%M", time.localtime(ts / 1000)),
                     "[%s]" % (src[:8] or "?"), os.path.relpath(path, root)))

    print("\n" + "=" * 86)
    print("recoverable=%d   no history=%d   history-but-all-snapshots-empty=%d"
          % (len(planned), len(no_history), len(all_empty)))
    if all_empty:
        print("\nsnapshots exist but every one is empty (truncated before Cursor saw it again):")
        for path, n in sorted(all_empty)[:15]:
            print("   %d snapshot(s)  %s" % (n, path))
        if len(all_empty) > 15:
            print("   ... and %d more" % (len(all_empty) - 15))
    if no_history:
        print("\nno history at all (never opened in Cursor, or history pruned): %d file(s)"
              % len(no_history))
        for path in sorted(no_history)[:15]:
            print("   %s" % path)
        if len(no_history) > 15:
            print("   ... and %d more" % (len(no_history) - 15))

    if not apply:
        print("\ndry run only; re-run with --apply")
        return 0

    print("\n--- writing ---")
    done = failed = 0
    for path, cur_sz, sp, size, ts, src in planned:
        # Re-check the destination immediately before writing: something may have
        # restored it since the scan, and overwriting good content is unrecoverable.
        try:
            with open(path, "rb") as f:
                if f.read() not in EMPTY:
                    print("   SKIP (no longer damaged) %s" % path)
                    continue
        except OSError as e:
            print("   SKIP (unreadable: %s) %s" % (e, path))
            failed += 1
            continue
        with open(sp, "rb") as f:
            data = f.read()
        if data in EMPTY:
            print("   SKIP (snapshot went empty) %s" % path)
            failed += 1
            continue
        with open(path, "wb") as f:
            f.write(data)
        ok = os.path.getsize(path) == len(data)
        print("   %s %7dB  %s" % ("WROTE " if ok else "FAILED", len(data), path))
        done += ok
        failed += not ok
    print("\nrestored %d, failed/skipped %d" % (done, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

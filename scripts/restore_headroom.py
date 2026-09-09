r"""Restore headroom-main's truncated files from the installed headroom package.

E:\AAAAAA\A-skill-work\github-skill\headroom-main was hit by the 2026-09-07 21:49
burst. hearoom-mind's venv holds the same code as an installed package, which is a
real recovery source -- but only if the two trees are the same version, so this
script re-proves that on every run instead of trusting a prior result.

Guards, all of which must hold before any write:
  * the destination is empty-damaged (0 bytes, or nothing but a line ending)
  * the source is non-empty
  * the relative path exists in both trees
  * every undamaged .py/.html shared by the two trees is byte-identical after CRLF
    normalisation -- if the versions have diverged, restoring would silently
    install the wrong code, so the script aborts
"""
import hashlib
import os
import sys

MAIN = r"E:\AAAAAA\A-skill-work\github-skill\headroom-main\headroom"
SITE = r"E:\workA\A-skill\hearoom-mind\.venv\Lib\site-packages\headroom"
COMPARE_EXT = (".py", ".html")
EMPTY = (b"", b"\r\n", b"\n")


def norm_bytes(path):
    with open(path, "rb") as f:
        return f.read().replace(b"\r\n", b"\n")


def walk_rel(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            out[os.path.relpath(p, root).replace(os.sep, "/")] = p
    return out


def damaged_files(main):
    """Empty-damaged candidates. __init__.py is included deliberately: it is usually
    empty by design, but providers/cursor/__init__.py was truncated to 0 bytes while
    the installed copy still held 307 bytes of real exports."""
    out = []
    for rel, path in main.items():
        if not rel.endswith(COMPARE_EXT):
            continue
        try:
            with open(path, "rb") as f:
                if f.read() in EMPTY:
                    out.append(rel)
        except OSError:
            continue
    return sorted(out)


def corroborate(main, site, skip):
    """Compare every undamaged shared source file. Returns (identical, differing)."""
    same, diff = 0, []
    for rel, path in sorted(main.items()):
        if rel in skip or not rel.endswith(COMPARE_EXT):
            continue
        other = site.get(rel)
        if other is None:
            diff.append((rel, "absent from the installed package"))
            continue
        if hashlib.md5(norm_bytes(path)).hexdigest() == hashlib.md5(norm_bytes(other)).hexdigest():
            same += 1
        else:
            diff.append((rel, "%dB vs %dB" % (os.path.getsize(path), os.path.getsize(other))))
    return same, diff


def main():
    apply = "--apply" in sys.argv
    if not (os.path.isdir(MAIN) and os.path.isdir(SITE)):
        print("ABORT: expected tree missing")
        return 1
    main_files, site_files = walk_rel(MAIN), walk_rel(SITE)
    damaged = damaged_files(main_files)
    if not damaged:
        print("nothing damaged under headroom-main -- no action")
        return 0

    same, diff = corroborate(main_files, site_files, set(damaged))
    print("=" * 74)
    print("headroom-main restore%s" % ("  [APPLY]" if apply else "  [dry run]"))
    print("=" * 74)
    print("version corroboration: %d undamaged source files identical, %d differing"
          % (same, len(diff)))
    for rel, why in diff[:10]:
        print("   DIFF %s (%s)" % (rel, why))
    if diff or same < 100:
        print("\nABORT: trees are not the same version (or too few files to trust).")
        print("       Restoring from a diverged package would install wrong code.")
        return 1

    planned, refused = [], []
    for rel in damaged:
        src, dst = site_files.get(rel), main_files[rel]
        if src is None:
            refused.append((rel, "not in the installed package"))
            continue
        size = os.path.getsize(src)
        if size <= len(b"\r\n"):
            refused.append((rel, "source is empty too (%dB)" % size))
            continue
        planned.append((rel, dst, src, size))

    print("\n%d file(s) to restore:" % len(planned))
    for rel, _, src, size in planned:
        print("   %2dB -> %7dB  %s" % (os.path.getsize(main_files[rel]), size, rel))
    for rel, why in refused:
        print("   REFUSED %s (%s)" % (rel, why))

    if not apply:
        print("\ndry run only; re-run with --apply")
        return 0

    done = 0
    for rel, dst, src, size in planned:
        with open(src, "rb") as f:
            data = f.read()
        with open(dst, "wb") as f:
            f.write(data)
        ok = os.path.getsize(dst) == size and hashlib.md5(norm_bytes(dst)).hexdigest() \
            == hashlib.md5(norm_bytes(src)).hexdigest()
        print("   %s %s (%dB)" % ("WROTE " if ok else "FAILED", rel, os.path.getsize(dst)))
        done += ok
    print("\nrestored %d/%d" % (done, len(planned)))
    return 0 if done == len(planned) else 1


if __name__ == "__main__":
    sys.exit(main())

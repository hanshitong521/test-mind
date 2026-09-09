"""Recover truncated files from each tree's OWN git HEAD.

Dry run:  python scripts/recover_from_git.py
Apply:    python scripts/recover_from_git.py --apply

protect_trees.py proved the point: a tree under git turns truncation from data
loss into a one-command restore. This is that command, run across every repo
under the at-risk roots.

A file qualifies only when BOTH hold:
  * the worktree copy is empty-damaged (0 bytes, or nothing but a line ending)
  * the HEAD blob for that exact path is non-empty
Everything else -- untracked damage, legitimately empty files, dirty work that
was never committed -- is reported but left alone.
"""
import os
import subprocess
import sys

ROOTS = [r"E:\workA\A-skill", r"E:\AAAAAA\A-skill-work", r"E:\AAAAAA\github-skill",
         r"E:\workA\shejiuPro"]
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
             "dist", "build", ".next", "target", ".uv-cache"}
EMPTY = (b"", b"\r\n", b"\n")
MAX_DEPTH = 4


def find_repos(root):
    """Collect every dir holding a .git, including repos nested inside other repos.

    A nested repo is recorded by its parent only as a gitlink, with none of its
    files, so stopping the walk at the first .git would leave the nested tree
    unscanned and its damage unreportable. Prune .git itself (object stores are
    huge and hold no worktree files) but keep descending.
    """
    out = []
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and d != ".git" and not d.startswith(".")]
        if os.path.isdir(os.path.join(dirpath, ".git")):
            out.append(dirpath)
        if dirpath.count(os.sep) - base_depth >= MAX_DEPTH:
            dirnames[:] = []
    return sorted(out)


def git(repo, *args, **kw):
    return subprocess.run(["git", "-C", repo, "-c", "core.quotepath=false", *args],
                          capture_output=True, **kw)


def head_sizes(repo):
    """path -> HEAD blob size, via one batched cat-file call.

    Parses ls-tree's default `<mode> <type> <sha>\\t<path>` records rather than
    `--format`: a bad --format string fails the call, and a failed call here
    reads as "no candidates" instead of as an error.
    """
    ls = git(repo, "ls-tree", "-r", "-z", "HEAD")
    if ls.returncode != 0:
        return {}
    blobs = []
    for rec in ls.stdout.decode("utf-8", "replace").split("\0"):
        if not rec:
            continue
        header, _, path = rec.partition("\t")
        parts = header.split()
        if len(parts) < 3 or parts[1] != "blob":
            continue                  # gitlink/tree: a directory, not a file
        blobs.append((parts[2], path))
    if not blobs:
        return {}
    inp = "".join(sha + "\n" for sha, _ in blobs).encode()
    bc = subprocess.run(["git", "-C", repo, "cat-file", "--batch-check=%(objectsize)"],
                        input=inp, capture_output=True)
    sizes = bc.stdout.decode("utf-8", "replace").split()
    out = {}
    for (sha, path), size in zip(blobs, sizes):
        try:
            out[path] = int(size)
        except ValueError:
            out[path] = -1            # missing/corrupt object
    return out


def is_damaged(path):
    """True only for the exact truncation signatures: 0 bytes, or a lone line ending.
    The largest signature is 2 bytes, so anything bigger is short-circuited."""
    try:
        if os.path.getsize(path) > 2:
            return False
        with open(path, "rb") as f:
            return f.read() in EMPTY
    except OSError:
        return False


def scan_repo(repo):
    heads = head_sizes(repo)
    if not heads:
        return [], []
    recoverable, unrecoverable = [], []
    for rel, hsize in heads.items():
        if hsize <= len(b"\r\n"):
            continue                  # HEAD itself is empty/tiny -> nothing was lost
        wp = os.path.join(repo, rel.replace("/", os.sep))
        if not os.path.exists(wp):
            continue                  # deleted, not truncated -- out of scope here
        if not is_damaged(wp):
            continue
        recoverable.append((rel, hsize, os.path.getsize(wp)))
    # damage git cannot help with: tracked-but-absent or untracked empties
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != ".git"]
        for fn in filenames:
            wp = os.path.join(dirpath, fn)
            rel = os.path.relpath(wp, repo).replace(os.sep, "/")
            if rel in heads or not is_damaged(wp):
                continue
            unrecoverable.append(rel)
    return recoverable, unrecoverable


def restore(repo, rows):
    """Restore by explicit pathspec list -- never a blanket checkout, which would
    also discard legitimate uncommitted work elsewhere in the repo."""
    spec = os.path.join(repo, ".git", "recover-pathspec")
    with open(spec, "wb") as f:
        f.write(b"\0".join(rel.encode("utf-8") for rel, _, _ in rows))
    r = git(repo, "checkout", "--pathspec-from-file=" + spec, "--pathspec-file-nul")
    try:
        os.remove(spec)
    except OSError:
        pass
    return r.returncode == 0, (r.stdout + r.stderr).decode("utf-8", "replace").strip()[:300]


def main():
    args = sys.argv[1:]
    apply = "--apply" in args
    roots = [a for a in args if not a.startswith("--")] or ROOTS
    print("=" * 78)
    print("git-HEAD recovery sweep%s" % ("  [APPLY MODE]" if apply else "  [dry run]"))
    print("=" * 78)
    tot_rec = tot_unrec = 0
    for root in roots:
        if not os.path.isdir(root):
            print("\n!! root absent: %s" % root)
            continue
        for repo in find_repos(root):
            rec, unrec = scan_repo(repo)
            if not rec and not unrec:
                continue
            print("\n--- %s" % repo)
            print("    recoverable from HEAD: %d   |   damaged but not in HEAD: %d"
                  % (len(rec), len(unrec)))
            for rel, hs, ws in sorted(rec)[:15]:
                print("      %6dB -> %6dB  %s" % (ws, hs, rel))
            if len(rec) > 15:
                print("      ... and %d more" % (len(rec) - 15))
            for rel in sorted(unrec)[:5]:
                print("      [no HEAD]  %s" % rel)
            if len(unrec) > 5:
                print("      ... and %d more untracked/uncommitted" % (len(unrec) - 5))
            tot_rec += len(rec)
            tot_unrec += len(unrec)
            if apply and rec:
                ok, err = restore(repo, rec)
                print("    -> restore %s%s" % ("OK" if ok else "FAILED", "" if ok else ": " + err))
    print("\n" + "=" * 78)
    print("recoverable=%d   not-in-HEAD=%d" % (tot_rec, tot_unrec))
    return 0


if __name__ == "__main__":
    sys.exit(main())

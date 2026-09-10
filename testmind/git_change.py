import os
import subprocess


def analyze_git_change(root, base_sha=None, head_sha=None, include_untracked=True):
    root = root or os.getcwd()
    env = os.environ.copy()
    files = []
    if base_sha and head_sha:
        r = subprocess.run(
            ["git", "diff", "--name-only", f"{base_sha}..{head_sha}"],
            capture_output=True,
            text=True,
            cwd=root,
        )
        files = [x for x in (r.stdout or "").splitlines() if x.strip()]
    else:
        r = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            cwd=root,
        )
        files = [x for x in (r.stdout or "").splitlines() if x.strip()]
    untracked = []
    if include_untracked:
        u = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            cwd=root,
        )
        untracked = [x for x in (u.stdout or "").splitlines() if x.strip()]
    return {
        "changed": files,
        "untracked": untracked,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "git_error": (r.stderr or "").strip() if r.returncode != 0 and not files else "",
    }

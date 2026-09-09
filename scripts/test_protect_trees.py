"""Tests for protect_trees.py -- the permanent anti-truncation fix.

Independent target: a synthetic tree built here, plus the real path names observed
during the 2026-09-07 incident. Every case has a known answer, and the tests are
written to fail both when a matcher is too loose (secrets committed, source code
left out of the baseline) and when it is too tight.
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protect_trees as P  # noqa: E402

# (path, expected is_secret_path). The False cases are real files that the old
# substring patterns wrongly kept out of project-brain-agent's baseline.
SECRET_CASES = [
    ("agent-canvas/src/api/secrets-service.ts", False),
    ("agent-canvas/src/hooks/mutation/use-create-secret.ts", False),
    ("agent-canvas/__tests__/utils/redact-mcp-secrets.test.ts", False),
    ("agent-canvas/src/api/mcp-service/mcp-redacted-credentials.ts", False),
    ("agent-canvas/__tests__/components/settings/acp-credentials-section.test.tsx", False),
    ("agent-canvas/src/components/features/settings/secrets-settings/page.tsx", False),
    ("agent-canvas/.env.sample", False),
    ("agent-canvas/examples/acp-docker/.env.example", False),
    ("README.md", False),
    ("headroom.local.env", True),
    (".env", True),
    (".env.local", True),
    (".cursor/mcp.json", True),
    ("credentials.json", True),
    ("config/id_rsa", True),
    ("config/id_rsa.pub", True),
    ("tls/server.pem", True),
    ("ref/shejiu-environments.local.json", True),
    ("scripts/deploy.key", True),
]

# (relpath, filename, expected is_benign_empty)
BENIGN_CASES = [
    (r"reports\20260830-155328-172e08\sut.log", "sut.log", True),
    (r"reports\20260830-184030-d53800\schema-tests-summary.txt", "schema-tests-summary.txt", True),
    (r"logs\hub.err.log", "hub.err.log", True),
    (r"caveman-main\benchmarks\results\.gitkeep", ".gitkeep", True),
    (r"headroom-main\headroom\py.typed", "py.typed", True),
    (r"8-27\ComfyUI-master\models\clip\put_clip_or_text_encoder_models_here",
     "put_clip_or_text_encoder_models_here", True),
    # __init__.py is NOT benign: headroom-main's providers/cursor/__init__.py was
    # truncated to 0 bytes while the installed copy held 307 bytes of real exports.
    (r"headroom-main\headroom\providers\cursor\__init__.py", "__init__.py", False),
    (r"A-data\README.md", "README.md", False),
    (r"src\main.py", "main.py", False),
    (r"scripts\verify-install.ps1", "verify-install.ps1", False),
]

AMBIGUOUS_CASES = [
    (r"pkg\__init__.py", "__init__.py", True),
    (r"pkg\core.py", "core.py", False),
]


def check(label, cases, fn):
    fails = []
    for case in cases:
        args, want = case[:-1], case[-1]
        got = fn(*args)
        if got != want:
            fails.append("%s%r = %s, want %s" % (label, args, got, want))
    return fails


def build_tree(root):
    """A synthetic tree exercising every scan() bucket."""
    files = {
        "src/real.py": "print('code')\n",           # healthy
        "src/truncated.py": "",                      # confirmed damage (0B)
        "docs/truncated.md": "\r\n",                 # confirmed damage (2B CRLF)
        "pkg/__init__.py": "",                       # ambiguous
        "benchmarks/.gitkeep": "",                   # benign
        "headroom/py.typed": "",                     # benign
        "models/put_checkpoints_here": "",           # benign (ComfyUI marker)
        "reports/run-1/sut.log": "",                 # benign (test output)
        "reports/run-1/summary.txt": "",             # benign (under reports/)
        "notes/legit-empty-looking.txt": "x" * 40,   # healthy
    }
    for rel, body in files.items():
        p = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(body)
    return files


def test_empty_folder_is_not_baselined(tmp):
    """Regression: empty folders used to get a repo whose only tracked file was the
    .gitignore we wrote ourselves -- junk repos in three A-github subfolders."""
    root = os.path.join(tmp, "root")
    os.makedirs(os.path.join(root, "empty-dir"))
    os.makedirs(os.path.join(root, "has-code"))
    with open(os.path.join(root, "has-code", "main.py"), "w", encoding="utf-8") as f:
        f.write("print('hi')\n")

    argv = sys.argv
    sys.argv = ["protect_trees.py", "--apply", root]
    try:
        P.main()
    finally:
        sys.argv = argv

    empty_git = os.path.isdir(os.path.join(root, "empty-dir", ".git"))
    code_git = os.path.isdir(os.path.join(root, "has-code", ".git"))
    fails = []
    if empty_git:
        fails.append("baselined an empty folder (junk repo)")
    if not code_git:
        fails.append("did not baseline the folder that has code")
    leftovers = os.listdir(os.path.join(root, "empty-dir"))
    if leftovers:
        fails.append("left artifacts in the empty folder: %s" % leftovers)
    return fails


def main():
    fails = []
    fails += check("is_secret_path", SECRET_CASES, P.is_secret_path)
    fails += check("is_benign_empty", BENIGN_CASES, P.is_benign_empty)
    fails += check("ambiguous", AMBIGUOUS_CASES, lambda rel, fn: fn in P.AMBIGUOUS_EMPTY)

    # --- is_damaged must key on exact byte signatures, not merely small size
    tmp = tempfile.mkdtemp(prefix="protect-test-")
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            fails += test_empty_folder_is_not_baselined(tmp)

        probes = {"zero": b"", "crlf": b"\r\n", "lf": b"\n", "one_char": b"x",
                  "two_char": b"ab", "three": b"abc"}
        for name, body in probes.items():
            p = os.path.join(tmp, name)
            with open(p, "wb") as f:
                f.write(body)
            bad, sz = P.is_damaged(p)
            want = name in ("zero", "crlf")          # b"\n" is a real 1-byte file, not CRLF damage
            if bad != want:
                fails.append("is_damaged(%s=%r) = %s, want %s" % (name, body, bad, want))

        # --- scan() bucketing on the synthetic tree
        tree = os.path.join(tmp, "tree")
        build_tree(tree)
        total, damaged, ambiguous = P.scan(tree)
        got_d = sorted(rel for rel, _, _ in damaged)
        got_a = sorted(rel for rel, _, _ in ambiguous)
        want_d = sorted([os.path.join("docs", "truncated.md"),
                         os.path.join("src", "truncated.py")])
        want_a = [os.path.join("pkg", "__init__.py")]
        if got_d != want_d:
            fails.append("scan damaged = %s, want %s" % (got_d, want_d))
        if got_a != want_a:
            fails.append("scan ambiguous = %s, want %s" % (got_a, want_a))
        if total != 10:
            fails.append("scan total = %d, want 10" % total)

        # --- baseline() must track the source, hold the secrets, and commit
        for rel, body in {"secret.env": "PASSWORD=hunter2\n",
                          "cfg/mcp.json": '{"password":"hunter2"}',
                          "src/keep-me.ts": "export const a = 1;\n"}.items():
            p = os.path.join(tree, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(body)
        _, dmg2, _ = P.scan(tree)
        P.baseline(tree, dmg2)
        tracked = subprocess.run(["git", "-C", tree, "ls-files"],
                                 capture_output=True).stdout.decode("utf-8", "replace").split()
        if "src/keep-me.ts" not in tracked:
            fails.append("baseline failed to track source file src/keep-me.ts")
        for s in ("secret.env", "cfg/mcp.json"):
            if s in tracked:
                fails.append("baseline COMMITTED A SECRET: %s" % s)
        head = subprocess.run(["git", "-C", tree, "log", "-1", "--format=%s"],
                              capture_output=True).stdout.decode("utf-8", "replace")
        if "protect_trees.py" not in head:
            fails.append("baseline commit lacks the marker; is_our_baseline would be False")
        if not P.is_our_baseline(tree):
            fails.append("is_our_baseline() did not recognise the baseline it just made")
        # the secret must still be on disk, merely untracked
        if not os.path.exists(os.path.join(tree, "secret.env")):
            fails.append("baseline deleted a secret file instead of just not tracking it")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 74)
    n = len(SECRET_CASES) + len(BENIGN_CASES) + len(AMBIGUOUS_CASES) + 6 + 1
    if fails:
        print("FAIL (%d)" % len(fails))
        for f in fails:
            print("  - %s" % f)
        return 1
    print("PASS: %d matcher cases + damage-signature probes + scan bucketing + "
          "a real baseline that tracks source, holds secrets, and is recognisable" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Restore any subset of the removed dangling skill junctions.

Reads docs/dangling-skills-manifest.json and recreates the junctions in
~/.qoder-cn/skills. Only entries whose target now exists are recreated, so this
is safe to run repeatedly.

  python scripts/restore_dangling_skills.py            # list what is restorable
  python scripts/restore_dangling_skills.py --all      # recreate every restorable one
  python scripts/restore_dangling_skills.py name1 name2
"""
import json
import os
import subprocess
import sys

MANIFEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "docs", "dangling-skills-manifest.json")
SKILLS = os.path.expanduser(r"~\.qoder-cn\skills")


def main():
    man = json.load(open(MANIFEST, encoding="utf-8"))
    entries = man["entries"]
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_all = "--all" in sys.argv

    if args:
        entries = [e for e in entries if e["name"] in args]
        missing = set(args) - {e["name"] for e in entries}
        if missing:
            print("not in manifest: %s" % ", ".join(sorted(missing)))
    elif not do_all:
        parked = [e for e in entries if e["recoverable_from"]]
        gone = [e for e in entries if not e["recoverable_from"]]
        print("manifest: %d dangling junctions were removed (%s)" % (man["count"], man["generated"]))
        print("\nrestorable now (%d) -- source survives in the parked dir:" % len(parked))
        for e in parked:
            print("  %-34s %s" % (e["name"], e["recoverable_from"]))
        print("\nNOT restorable (%d) -- source directory is gone:" % len(gone))
        for e in gone:
            print("  %-34s %s" % (e["name"], e["dead_target"]))
        print("\nrun with --all, or pass specific names, to recreate junctions.")
        return 0

    made, skipped, failed = [], [], []
    for e in entries:
        link = os.path.join(SKILLS, e["name"])
        # Prefer the parked copy; fall back to the original target if it came back.
        target = e["recoverable_from"] or e["dead_target"]
        if not os.path.isdir(target):
            skipped.append((e["name"], "target still missing"))
            continue
        if os.path.exists(link) or os.path.islink(link):
            skipped.append((e["name"], "a link already exists"))
            continue
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "New-Item -ItemType Junction -Path '%s' -Target '%s' -ErrorAction Stop | Out-Null"
             % (link, target)],
            capture_output=True)
        if r.returncode == 0 and os.path.isdir(os.path.join(link)):
            made.append(e["name"])
        else:
            failed.append((e["name"], r.stderr.decode("utf-8", "replace").strip()[:120]))

    print("created %d, skipped %d, failed %d" % (len(made), len(skipped), len(failed)))
    for n in made:
        print("  CREATED %s" % n)
    for n, why in skipped:
        print("  skipped %s (%s)" % (n, why))
    for n, why in failed:
        print("  FAILED  %s (%s)" % (n, why))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

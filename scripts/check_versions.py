#!/usr/bin/env python3
"""Fail when an add-on changed but its version did not go up.

Usage:
    python3 scripts/check_versions.py BASE_REF   # e.g. origin/main

Kodi only installs an update when the repository offers a higher version
than the one installed, so a change merged without a version bump would
never reach any device. Run by CI on every pull request and push.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from addons import ADDONS, ROOT, compare_versions, read_addon, read_addon_text  # noqa: E402


def git(*args):
    return subprocess.run(("git",) + args, cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    base = sys.argv[1]
    git("rev-parse", "--verify", base + "^{commit}")
    failed = False
    for addon in ADDONS:
        # tracked changes since base, plus new files not yet added to git
        changed = (git("diff", "--name-only", base, "--", addon).splitlines()
                   + git("ls-files", "--others", "--exclude-standard", "--", addon).splitlines())
        if not changed:
            print("%-22s unchanged" % addon)
            continue
        _, new = read_addon(addon)
        try:
            _, old = read_addon_text(git("show", "%s:%s/addon.xml" % (base, addon)))
        except subprocess.CalledProcessError:
            print("%-22s new add-on, version %s" % (addon, new))
            continue
        if compare_versions(new, old) > 0:
            print("%-22s %s -> %s (%d files changed)" % (addon, old, new, len(changed)))
        else:
            failed = True
            print("%-22s CHANGED but version %s is not above %s: run "
                  "python3 scripts/bump.py %s \"what changed\"" % (addon, new, old, addon))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

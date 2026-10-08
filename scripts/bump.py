#!/usr/bin/env python3
"""Raise an add-on's version and note the change in its changelog.

Usage:
    python3 scripts/bump.py ADDON "what changed" ["another change" ...]
    python3 scripts/bump.py ADDON --to 5.10.150.1 "merged upstream 5.10.150"

Without --to, the last number of the version goes up by one, so our builds
on top of upstream 5.10.143 run 5.10.143.1, 5.10.143.2, ... (Kodi orders
5.10.143.1 above 5.10.143); repository.kodidex goes 1.0.0, 1.0.1, ...
Adds a section to the top of the add-on's changelog.txt.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from addons import ADDONS, ROOT, addon_xml_path, compare_versions, read_addon  # noqa: E402


# Add-ons whose x.y.z comes from upstream: our builds add a fourth number.
UPSTREAM = ("plugin.video.dexhub", "skin.dexhub")


def next_version(addon, version):
    if not re.match(r"^\d+(\.\d+)*$", version):
        sys.exit("cannot auto-bump %r; pass --to" % version)
    parts = version.split(".")
    if addon in UPSTREAM and len(parts) == 3:
        parts.append("1")       # upstream 5.10.143 -> our 5.10.143.1
    else:
        parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("addon", choices=ADDONS)
    parser.add_argument("changes", nargs="+", help="changelog lines")
    parser.add_argument("--to", help="exact new version")
    args = parser.parse_args()

    _, old = read_addon(args.addon)
    new = args.to or next_version(args.addon, old)
    if compare_versions(new, old) <= 0:
        sys.exit("%s is not above the current %s" % (new, old))

    path = addon_xml_path(args.addon)
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    pattern = re.compile(r'(<addon\b[^>]*?\bversion=)(["\'])%s\2' % re.escape(old))
    text, count = pattern.subn(lambda m: "%s%s%s%s" % (m.group(1), m.group(2), new, m.group(2)),
                              text, count=1)
    if count != 1:
        sys.exit("could not find version=%r on <addon> in %s" % (old, path))
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)

    changelog = os.path.join(ROOT, args.addon, "changelog.txt")
    entry = new + "\n" + "".join("- %s\n" % c for c in args.changes) + "\n"
    existing = ""
    if os.path.exists(changelog):
        with open(changelog, encoding="utf-8") as handle:
            existing = handle.read()
    with open(changelog, "w", encoding="utf-8", newline="") as handle:
        handle.write(entry + existing)
    print("%s: %s -> %s" % (args.addon, old, new))


if __name__ == "__main__":
    main()

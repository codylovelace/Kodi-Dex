#!/usr/bin/env python3
"""Fast checks that catch what would break on a Kodi box.

  * every .py parses as Python 3.8 (Kodi 20 Nexus ships 3.8 on some
    platforms; this is ast's best-effort feature_version check)
  * every .xml is well-formed (one broken skin or settings file breaks the
    whole add-on); files in a parts/ folder are fragments the add-on splices
    into other XML (resources/lib/skinui/af3pages.py), so they are checked
    inside a wrapper element
  * every addon.xml declares the id of its folder and a version
"""
import ast
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from addons import ADDONS, ROOT, read_addon  # noqa: E402

PYTHON = (3, 8)


def main():
    errors = []
    counts = {"py": 0, "xml": 0}
    for addon in ADDONS:
        addon_id, version = read_addon(addon)
        if addon_id != addon or not version:
            errors.append("%s/addon.xml: id %r version %r" % (addon, addon_id, version))
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, addon)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in filenames:
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, ROOT)
                if name.endswith(".py"):
                    counts["py"] += 1
                    try:
                        with open(path, encoding="utf-8") as handle:
                            ast.parse(handle.read(), filename=rel, feature_version=PYTHON)
                    except (SyntaxError, UnicodeDecodeError) as exc:
                        errors.append("%s: %s" % (rel, exc))
                elif name.endswith(".xml"):
                    counts["xml"] += 1
                    try:
                        if os.path.basename(dirpath) == "parts":
                            with open(path, encoding="utf-8") as handle:
                                ET.fromstring("<fragment>%s</fragment>" % handle.read())
                        else:
                            ET.parse(path)
                    except (ET.ParseError, UnicodeDecodeError) as exc:
                        errors.append("%s: %s" % (rel, exc))
    for line in errors:
        print("FAIL " + line)
    print("checked %(py)d .py and %(xml)d .xml files" % counts)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()

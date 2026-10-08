"""Shared helpers for the build scripts: the add-ons in this repo, reading
their addon.xml, and Kodi's own version ordering."""
import os
import re
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every add-on folder this repository builds and publishes.
ADDONS = ("plugin.video.dexhub", "skin.dexhub", "repository.kodidex")
REPOSITORY_ADDON = "repository.kodidex"


def addon_xml_path(addon):
    return os.path.join(ROOT, addon, "addon.xml")


def read_addon(addon):
    """(id, version) from <addon>/addon.xml."""
    root = ET.parse(addon_xml_path(addon)).getroot()
    return root.get("id"), root.get("version")


def read_addon_text(text):
    """(id, version) from addon.xml content (e.g. an older git revision)."""
    root = ET.fromstring(text)
    return root.get("id"), root.get("version")


# --- Kodi version ordering -------------------------------------------------
# A port of CAddonVersion (xbmc/addons/AddonVersion.cpp, Kodi 21 "Omega"):
#   [epoch:]upstream[-revision], compared case-insensitively, where '~' sorts
#   before anything (so 1.0~beta1 < 1.0) and extra trailing parts sort after
#   (so 5.10.143.1 > 5.10.143).

_VALID = re.compile(r"^[a-z0-9.+_@~]*$")


def _split(version):
    v = (version or "0.0.0").lower()
    epoch = 0
    if ":" in v:
        head, v = v.split(":", 1)
        m = re.match(r"\s*[+-]?\d+", head)
        epoch = int(m.group(0)) if m else 0
    revision = ""
    if "-" in v:
        v, revision = v.split("-", 1)
        if not _VALID.match(revision):
            revision = ""
    if not _VALID.match(v):
        v = "0.0.0"
    return epoch, v, revision


def _compare_component(a, b):
    i = j = 0
    while i < len(a) and j < len(b):
        while i < len(a) and j < len(b) and not a[i].isdigit() and not b[j].isdigit():
            if a[i] != b[j]:
                if a[i] == "~":
                    return -1
                if b[j] == "~":
                    return 1
                return -1 if a[i] < b[j] else 1
            i += 1
            j += 1
        if i < len(a) and j < len(b) and (not a[i].isdigit() or not b[j].isdigit()):
            if a[i] == "~":
                return -1
            if b[j] == "~":
                return 1
            return -1 if a[i].isdigit() else 1
        # strtol(): here each side is at a digit run or at its end ("" -> 0)
        ni = i
        while ni < len(a) and a[ni].isdigit():
            ni += 1
        nj = j
        while nj < len(b) and b[nj].isdigit():
            nj += 1
        num_a, num_b = int(a[i:ni] or 0), int(b[j:nj] or 0)
        if num_a != num_b:
            return -1 if num_a < num_b else 1
        i, j = ni, nj
    if i >= len(a) and j >= len(b):
        return 0
    if i < len(a):
        return -1 if a[i] == "~" else 1
    return 1 if b[j] == "~" else -1


def compare_versions(a, b):
    """-1, 0 or 1 as Kodi orders add-on versions a and b."""
    ea, ua, ra = _split(a)
    eb, ub, rb = _split(b)
    if ea != eb:
        return -1 if ea < eb else 1
    return _compare_component(ua, ub) or _compare_component(ra, rb)

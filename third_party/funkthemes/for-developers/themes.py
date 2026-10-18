"""Shared theme library for Funk's apps: %LOCALAPPDATA%\\FunkThemes\\<name>.funktheme (JSON, see ui/funktheme.js).

Every app that uses FunkTheme reads and writes the same folder, so a theme made in one shows up in all.
"""
import glob
import json
import os
import re

DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "FunkThemes")
MAX_BYTES = 8 * 1024 * 1024          # a theme with a picture; the editor shrinks pictures to 1920 px


def safe_name(name):
    s = re.sub(r"[^\w\- ]+", "", str(name)).strip()[:60]
    return s or "theme"


def _valid(t):
    return isinstance(t, dict) and t.get("funkTheme") == 1 and t.get("name")


def library():
    out = []
    for p in sorted(glob.glob(os.path.join(DIR, "*.funktheme")), key=os.path.getmtime):
        try:
            if os.path.getsize(p) > MAX_BYTES:
                continue
            with open(p, encoding="utf-8") as f:
                t = json.load(f)
            if _valid(t):
                out.append(t)
        except Exception:
            continue
    return out


def write(path, theme):
    data = json.dumps(theme, separators=(",", ":"))
    if len(data) > MAX_BYTES:
        raise ValueError("That theme is too big (the picture is over 8 MB).")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)


def save(theme):
    if not _valid(theme):
        raise ValueError("Not a theme.")
    os.makedirs(DIR, exist_ok=True)
    write(os.path.join(DIR, safe_name(theme["name"]) + ".funktheme"), theme)


def delete(name):
    p = os.path.join(DIR, safe_name(name) + ".funktheme")
    if os.path.exists(p):
        os.remove(p)

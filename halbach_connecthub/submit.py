"""`halbach submit`: drop a folder, a file or some text into the hub's inbox.

This is the safe door for chats and other tools. It only copies files into the inbox, applies the
never-publish list on the way in, and cannot publish anything or switch anything on: the hub lists
the submission as a switched-off module and the user reviews it. Stdlib only.
"""

import argparse
import json
import os
import secrets
import shutil
import sys
import time
from pathlib import Path

from .store import (INBOX_MAX_TOTAL_BYTES, MAX_NAME, ValidationError, _CTRL_RE, collect_files, home_dir, slugify,
                    validate_static_source)

MAX_STDIN_BYTES = 1024 * 1024


def clean(text, limit):
    return _CTRL_RE.sub(" ", text or "").strip()[:limit]


def _strip_bom(path):
    """Remove a leading UTF-8 byte-order mark (Windows tools add one; it breaks the first heading)."""
    data = Path(path).read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        Path(path).write_bytes(data[3:])


def submit(source=None, name=None, desc="", home=None, text=None):
    """Copy a submission into <home>/inbox and return the final folder.

    source: a folder or a file; a lone .md file becomes index.md so the site renders it as a doc.
    text:   Markdown text instead of a source; written as index.md.
    """
    if (source is None) == (text is None):
        raise ValidationError("Give either a folder/file or --stdin text, not both")
    home = Path(home) if home else home_dir()
    inbox = home / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)

    tmp = inbox / (".tmp-" + secrets.token_hex(4))        # dot-folders are ignored by the scanner
    tmp.mkdir()
    try:
        if text is not None:
            data = text.lstrip(chr(0xFEFF)).encode("utf-8")      # a leading byte-order mark would break the first heading
            if not data.strip():
                raise ValidationError("The text is empty")
            if len(data) > MAX_STDIN_BYTES:
                raise ValidationError("Text is over 1 MB; put it in a file or folder instead")
            (tmp / "index.md").write_bytes(data)
            default_name = "Notes"
        else:
            src = Path(validate_static_source(str(source)))
            files, problems = collect_files(src)
            if problems:
                raise ValidationError(problems[0])
            if sum(f[2] for f in files) > INBOX_MAX_TOTAL_BYTES:
                raise ValidationError("Over %d MB in total" % (INBOX_MAX_TOTAL_BYTES // 1048576))
            default_name = src.stem if src.is_file() else src.name
            if src.is_file():
                is_md = src.suffix.lower() in (".md", ".markdown")
                shutil.copy2(src, tmp / ("index.md" if is_md else src.name))
                if is_md:
                    _strip_bom(tmp / "index.md")
            else:
                for path, rel, _ in files:                 # only files that pass the never-publish list
                    dest = tmp / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, dest)
                    if rel.lower().endswith((".md", ".markdown")):
                        _strip_bom(dest)
        nm = clean(name, MAX_NAME) or clean(default_name.replace("-", " ").replace("_", " "), MAX_NAME).title() or "Submission"
        meta = {"name": nm, "desc": clean(desc, 200), "submitted": int(time.time()), "by": "halbach submit"}
        (tmp / "module.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")

        base, n = slugify(nm), 1
        final = inbox / base
        while final.exists():
            n += 1
            final = inbox / ("%s-%d" % (base, n))
        os.replace(str(tmp), str(final))                    # appears all at once, never half-written
        return final
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def main(argv):
    ap = argparse.ArgumentParser(prog="halbach submit",
                                 description="Send a folder, file or text to Halbach ConnectHub for review.")
    ap.add_argument("source", nargs="?", help="folder or file to submit (a lone .md file becomes a page)")
    ap.add_argument("--stdin", action="store_true", help="read Markdown text from standard input instead")
    ap.add_argument("--name", help="name shown on the site")
    ap.add_argument("--desc", default="", help="one-line description")
    ap.add_argument("--home", help="state folder (default: the usual one)")
    a = ap.parse_args(argv)
    try:
        if a.stdin:
            text = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1).decode("utf-8-sig", errors="replace")
            final = submit(text=text, name=a.name, desc=a.desc, home=a.home)
        elif a.source:
            final = submit(source=a.source, name=a.name, desc=a.desc, home=a.home)
        else:
            ap.error("give a folder or file, or use --stdin")
    except ValidationError as e:
        print("Not submitted: %s" % e, file=sys.stderr)
        return 2
    print("Submitted: %s" % final)
    print("It is switched OFF. Open Halbach ConnectHub, review it, switch it on and press Publish.")
    return 0

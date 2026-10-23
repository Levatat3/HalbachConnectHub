"""Checks an area's own stylesheet (tier 2 of "areas are sub-sites").

The rule is: anything the owner cannot read is refused. A stylesheet is not rewritten into something safer; it either
passes as written or is refused with a reason the sender can act on. Nothing obfuscated or encrypted gets through:
no backslash escapes outside quoted text, no long encoded strings, no packed one-line files, no data: addresses, no
outside addresses at all (a picture may only be one of the area's own files), no script-like functions.

check(text, files, slug) -> Result(problems, warnings, css). With a slug the returned css has its picture addresses
turned into the area's own absolute addresses, ready for the builder. Stdlib only.
"""
import re

MAX_CSS_BYTES = 100 * 1024
MAX_LINE = 4000             # a line this long is packed, not written
WARN_LINE = 800
MAX_STRING = 300            # longer quoted text looks like encoded data
MAX_TOKEN = 120             # an unbroken run with no spaces or punctuation this long looks like encoded data
MAX_COMMENT = 3000
MAX_DEPTH = 6
OK_AT = {"media", "supports", "keyframes", "-webkit-keyframes", "layer", "container", "font-face", "property"}
IMAGE_URL = re.compile(r"^files/[A-Za-z0-9_()+,@~/. \-]+\.(?:png|jpe?g|gif|webp)$", re.I)
BAD_CHARS = re.compile("[​-‏‪-‮⁦-⁩﻿\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
FORBIDDEN_FN = re.compile(r"(?<![a-z0-9_-])(image-set|-webkit-image-set|image|src|cross-fade|-webkit-cross-fade|element|-moz-element|paint|attr|expression)\s*\(", re.I)
FORBIDDEN_WORDS = re.compile(r"-moz-binding|(?<![a-z0-9_-])behavior\s*:|javascript\s*:|vbscript\s*:|(?<![a-z0-9_-])data\s*:", re.I)
HEX_ESCAPE = re.compile(r"\\[0-9a-fA-F]{1,6} ?")
URL_CALL = re.compile(r"url\(\s*(?:\x01(\d+)\x02|([^)\s\x01]*))\s*\)", re.I)


class Result:
    def __init__(self):
        self.problems, self.warnings, self.css = [], [], ""

    @property
    def ok(self):
        return not self.problems


def _mask(text, problems):
    """Split the stylesheet into code and quoted text. Returns (code with each string replaced by a marker, the strings).

    Comments are dropped (the owner reads the file, so they are fine, but a comment cannot hide code). Any backslash
    outside quoted text, and any quoted text that is not plain, is a problem."""
    code, strings, i, n = [], [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                problems.append("a comment is never closed")
                break
            if j - i > MAX_COMMENT:
                problems.append("a comment is longer than %d characters (long hidden text is not allowed)" % MAX_COMMENT)
            code.append(" ")
            i = j + 2
        elif c in "\"'":
            j, buf = i + 1, []
            while j < n and text[j] != c:
                if text[j] == "\n":
                    problems.append("a quoted text is not closed on its line")
                    break
                if text[j] == "\\":
                    m = HEX_ESCAPE.match(text, j)
                    if m:
                        buf.append(m.group(0))
                        j = m.end()
                        continue
                    if j + 1 < n and text[j + 1] in "\"'\\":
                        buf.append(text[j:j + 2])
                        j += 2
                        continue
                    problems.append("a backslash in quoted text is only allowed as a character code such as \\201C")
                    j += 1
                    continue
                buf.append(text[j])
                j += 1
            else:
                if j >= n:
                    problems.append("a quoted text is never closed")
            body = "".join(buf)
            if len(body) > MAX_STRING:
                problems.append("a quoted text is longer than %d characters (long strings look like encoded data)" % MAX_STRING)
            strings.append([c, body])
            code.append("\x01%d\x02" % (len(strings) - 1))
            i = j + 1
        elif c == "\\":
            problems.append("a backslash outside quoted text is not allowed (it can hide what the CSS says)")
            i += 1
        else:
            if ord(c) > 127:
                problems.append("a non-ASCII character outside quoted text and comments is not allowed")
            code.append(c)
            i += 1
    return "".join(code), strings


def check(text, files=None, slug=None, url_base=""):
    """Check one stylesheet. files = the set of "files/..." names that exist in the bundle (None = do not check they exist)."""
    res = Result()
    pr, wr = res.problems, res.warnings
    if not isinstance(text, str):
        pr.append("the stylesheet is not text")
        return res
    if len(text.encode("utf-8", "replace")) > MAX_CSS_BYTES:
        pr.append("the stylesheet is larger than %d KB" % (MAX_CSS_BYTES // 1024))
        return res
    if BAD_CHARS.search(text):
        pr.append("the stylesheet contains hidden or control characters")
        return res
    longest = max((len(x) for x in text.splitlines()), default=0)
    if longest > MAX_LINE:
        pr.append("a line is %d characters long; write it out over several lines so it can be reviewed (the limit is %d)" % (longest, MAX_LINE))
    elif longest > WARN_LINE:
        wr.append("the stylesheet has a very long line (%d characters), which is hard to review" % longest)
    code, strings = _mask(text, pr)
    if "<" in code:
        pr.append("'<' is not allowed outside quoted text")
    depth = deepest = 0
    parens = 0
    for ch in code:
        if ch == "{":
            depth += 1
            deepest = max(deepest, depth)
        elif ch == "}":
            depth -= 1
            if depth < 0:
                break
        elif ch == "(":
            parens += 1
        elif ch == ")":
            parens -= 1
    if depth != 0 or parens != 0:
        pr.append("the stylesheet has unbalanced brackets")
    elif deepest > MAX_DEPTH:
        pr.append("rules are nested more than %d levels deep" % MAX_DEPTH)
    for name in sorted(set(m.lower() for m in re.findall(r"@([A-Za-z-]+)", code))):
        if name not in OK_AT:
            pr.append("the @%s rule is not allowed (allowed: %s)" % (name, ", ".join("@" + a for a in sorted(OK_AT))))
    if FORBIDDEN_WORDS.search(code):
        pr.append("the stylesheet uses a script, binding or data: address, which is not allowed")
    m = FORBIDDEN_FN.search(code)
    if m:
        pr.append("the function %s() is not allowed" % m.group(1).lower())
    if re.search(r"[^\s{};:,()\x01\x02]{%d,}" % MAX_TOKEN, code):
        pr.append("a run of %d or more characters with no spaces or punctuation looks like encoded data" % MAX_TOKEN)
    # pictures: url() may only name one of the area's own picture files
    calls = list(URL_CALL.finditer(code))
    if len(re.findall(r"url\(", code, re.I)) != len(calls):
        pr.append("a url() could not be read; write it as url(\"files/name.png\")")
    replaced = {}
    for m in calls:
        if m.group(1) is not None:
            quote, raw = strings[int(m.group(1))]
        else:
            quote, raw = '"', m.group(2)
        if "\\" in raw or not IMAGE_URL.match(raw) or any(seg in ("", ".", "..") for seg in raw.split("/")):
            pr.append("url(%s) is not allowed: only a picture from this area's files folder (url(\"files/name.png\")) can be used" % _short(raw))
            continue
        if files is not None and raw not in files:
            pr.append("url(%s) names a picture that is not in the bundle" % _short(raw))
            continue
        replaced[m.span()] = raw
    if re.search(r"(?<![a-z0-9_-])position\s*:\s*(?:fixed|sticky)", code, re.I):
        wr.append("uses position: fixed or sticky, which can pin things over the page")
    m = re.search(r"z-index\s*:\s*(\d+)", code, re.I)
    if m and int(m.group(1)) >= 1000:
        wr.append("uses a very high z-index (%s), which can cover parts of the page" % m.group(1))
    if pr:
        return res
    base = (url_base + "/" + (slug + "/" if slug else "")) if slug is not None else ""     # slug "" = the root of a preview
    out, last = [], 0
    for (a, b), raw in sorted(replaced.items()):
        out.append(code[last:a])
        out.append('url("%s%s")' % (base, raw))
        last = b
    out.append(code[last:])
    css = "".join(out)
    css = re.sub(r"\x01(\d+)\x02", lambda mm: strings[int(mm.group(1))][0] + strings[int(mm.group(1))][1] + strings[int(mm.group(1))][0], css)
    res.css = css.strip() + "\n"
    return res


def _short(s):
    return "".join(c if 32 <= ord(c) < 127 else "?" for c in s)[:40]

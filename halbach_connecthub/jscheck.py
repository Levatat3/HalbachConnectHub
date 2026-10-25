"""Checks a script from an area's own pages (tier 3 of "areas are sub-sites").

The rule is the same as for stylesheets: anything the owner cannot read is refused. A script is not rewritten; it passes as
written or is refused with a reason the sender can act on. This is a readability and honesty gate, not the wall: what actually
contains an area's pages is the browser sandbox and the policy header the builder puts on them (no storage, no outside
addresses, no forms). What this check adds is that the owner reviews code that can be read, not code that hides what it does:

  - no packed or minified one-line files, no long encoded strings, no strings full of character codes, no `_0x1234` style names;
  - no eval, no building code from text, no decoding hidden text, no modules (plain scripts only);
  - no network features other than reading the area's own files, no outside addresses written into the script, no code that
    sends the visitor to another address.

check(text) -> Result(problems, warnings). Stdlib only.
"""
import re

MAX_JS_BYTES = 300 * 1024
MAX_LINE = 3000             # a line this long is packed, not written
WARN_LINE = 600
MAX_STRING = 300            # a quoted string longer than this looks like encoded data
MAX_TEMPLATE = 2000         # a template (backtick) string may hold a block of markup
MAX_COMMENT = 3000
MAX_CHARCODES = 5           # String.fromCharCode more often than this looks like hidden text
MAX_ESCAPES = 12            # \x41 / A escapes inside strings, per string
BAD_CHARS = re.compile("[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

BANNED = [
    (re.compile(r"\beval\b"), "eval is not allowed"),
    (re.compile(r"\bnew\s+Function\b|(?<![\w.$])Function\s*\("), "building code from text (Function) is not allowed"),
    (re.compile(r"(?<![\w.$])atob\s*\(|(?<![\w.$])unescape\s*\("), "decoding hidden text (atob, unescape) is not allowed"),
    (re.compile(r"\b(?:WebSocket|EventSource|XMLHttpRequest|RTCPeerConnection|SharedWorker)\b"),
     "network features are not allowed (read your own files with fetch)"),
    (re.compile(r"\bsendBeacon\b|\bimportScripts\b|\bserviceWorker\b|\bnew\s+Worker\b"), "workers, service workers and beacons are not allowed"),
    (re.compile(r"\bdocument\s*\.\s*write(?:ln)?\s*\("), "document.write is not allowed; build the page with DOM methods"),
    (re.compile(r"(?m)^\s*(?:import|export)\s*[\w{*'\"]|\bimport\s*\("), "modules are not supported: write plain script files"),
    (re.compile(r"\bset(?:Timeout|Interval)\s*\(\s*[\"'`]"), "a string given to setTimeout or setInterval runs as code and is not allowed"),
    (re.compile(r"\[\s*[\"']constructor[\"']\s*\]|\.\s*constructor\s*\.\s*constructor"), "reaching Function through constructor is not allowed"),
    (re.compile(r"(?<![\w$.])(?:(?:window|document|self|top|parent|globalThis)\s*\.\s*)?location\s*(?:\.\s*(?:href|assign|replace)\s*)?(?:=(?!=)|\()"),
     "sending the visitor to another address from a script is not allowed (put a link in the page instead)"),
    (re.compile(r"\bwindow\s*\.\s*open\s*\(|(?<![\w$.])open\s*\(|\bdocument\s*\.\s*open\s*\("), "opening windows from a script is not allowed (a function of your own named open( is refused too: give it another name)"),
    (re.compile(r"\b(?:top|parent)\s*\.\s*(?:location|document)\b"), "reaching outside the page is not allowed"),
]
HEXNAME = re.compile(r"\b_0x[0-9a-fA-F]{3,}\b")
OUTSIDE_ADDRESS = re.compile(r"(?:\b(?:https?|wss?|ftp):\s*(?:/|\\/){2})|^\s*//[A-Za-z0-9]", re.I)
JAVASCRIPT_URL = re.compile(r"\bjavascript\s*:", re.I)
CHARCODES = re.compile(r"\bfromCharCode\b|\bfromCodePoint\b")
ESCAPES = re.compile(r"\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}|\\u\{[0-9a-fA-F]+\}")
BASE64ISH = re.compile(r"[A-Za-z0-9+/=_-]{120,}")
STORAGE = re.compile(r"\b(?:localStorage|sessionStorage|indexedDB)\b|document\s*\.\s*cookie")
HTML_FROM_TEXT = re.compile(r"\.\s*(?:innerHTML|outerHTML)\s*(?:\+?=)|\binsertAdjacentHTML\b")
MODALS = re.compile(r"(?<![\w$.])(?:alert|confirm|prompt)\s*\(|\bwindow\s*\.\s*(?:alert|confirm|prompt)\s*\(")
REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^")
REGEX_KEYWORDS = {"return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "else", "do", "yield", "await"}


class Result:
    def __init__(self):
        self.problems, self.warnings = [], []

    @property
    def ok(self):
        return not self.problems


def _lex(text, problems):
    """Split the script into code and quoted text. Returns (code with every string, template and regex replaced by a marker,
    [(kind, body)]). Comments are dropped (the owner reads the file, but a comment cannot hide code)."""
    code, strings, i, n = [], [], 0, len(text)
    last_word = ""

    def prev_sig():
        for ch in reversed(code):
            if ch.strip():
                return ch
        return ""

    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            if j - i > MAX_COMMENT:
                problems.append("a comment is longer than %d characters (long hidden text is not allowed)" % MAX_COMMENT)
            code.append(" ")
            i = j
        elif text.startswith("/*", i):
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
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j:j + 2])
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            else:
                if j >= n:
                    problems.append("a quoted text is never closed")
            strings.append(("quote", "".join(buf)))
            code.append("\x01%d\x02" % (len(strings) - 1))
            i = j + 1
        elif c == "`":
            j, buf, depth = i + 1, [], 0
            while j < n:
                ch = text[j]
                if ch == "\\" and j + 1 < n:
                    buf.append(text[j:j + 2])
                    j += 2
                    continue
                if ch == "$" and text.startswith("${", j):
                    depth += 1
                    buf.append("${")
                    j += 2
                    continue
                if ch == "}" and depth:
                    depth -= 1
                elif ch == "`" and not depth:
                    break
                buf.append(ch)
                j += 1
            else:
                problems.append("a template text is never closed")
            strings.append(("template", "".join(buf)))
            code.append("\x01%d\x02" % (len(strings) - 1))
            i = j + 1
        elif c == "/":
            before = prev_sig()
            tail = "".join(code[-12:]).rstrip()
            word = re.search(r"([A-Za-z]+)$", tail)
            if before == "" or before in REGEX_AFTER or (word and word.group(1) in REGEX_KEYWORDS):
                j, in_class, buf = i + 1, False, []
                while j < n and text[j] != "\n":
                    ch = text[j]
                    if ch == "\\" and j + 1 < n:
                        buf.append(text[j:j + 2])
                        j += 2
                        continue
                    if ch == "[":
                        in_class = True
                    elif ch == "]":
                        in_class = False
                    elif ch == "/" and not in_class:
                        break
                    buf.append(ch)
                    j += 1
                if j >= n or text[j] != "/":
                    problems.append("a pattern (regular expression) is not closed on its line")
                    code.append(c)
                    i += 1
                    continue
                j += 1
                while j < n and text[j].isalpha():
                    j += 1
                strings.append(("regex", "".join(buf)))
                code.append("\x01%d\x02" % (len(strings) - 1))
                i = j
            else:
                code.append(c)
                i += 1
        else:
            if ord(c) > 127:
                problems.append("a non-ASCII character outside quoted text and comments is not allowed")
            code.append(c)
            i += 1
    return "".join(code), strings


def check(text):
    """Check one script. Returns a Result."""
    res = Result()
    pr, wr = res.problems, res.warnings
    if not isinstance(text, str):
        pr.append("the script is not text")
        return res
    if len(text.encode("utf-8", "replace")) > MAX_JS_BYTES:
        pr.append("the script is larger than %d KB" % (MAX_JS_BYTES // 1024))
        return res
    if BAD_CHARS.search(text):
        pr.append("the script contains hidden or control characters")
        return res
    longest = max((len(x) for x in text.splitlines()), default=0)
    if longest > MAX_LINE:
        pr.append("a line is %d characters long; write it out over several lines so it can be reviewed (the limit is %d)" % (longest, MAX_LINE))
    elif longest > WARN_LINE:
        wr.append("the script has a very long line (%d characters), which is hard to review" % longest)
    code, strings = _lex(text, pr)
    for rx, why in BANNED:
        if rx.search(text):
            pr.append(why)
    if HEXNAME.search(code):
        pr.append("names like _0x1a2b are how obfuscated code looks; use readable names")
    if len(CHARCODES.findall(text)) > MAX_CHARCODES:
        pr.append("text built from character codes (fromCharCode) more than %d times looks hidden" % MAX_CHARCODES)
    for kind, body in strings:
        if kind == "regex":
            continue
        limit = MAX_TEMPLATE if kind == "template" else MAX_STRING
        if len(body) > limit:
            pr.append("a quoted text is longer than %d characters (long strings look like encoded data)" % limit)
        if BASE64ISH.search(body):
            pr.append("a quoted text holds a long run of letters and digits with no spaces, which looks like encoded data")
        if len(ESCAPES.findall(body)) > MAX_ESCAPES:
            pr.append("a quoted text is mostly character codes (\\x41, \\u0041), which hides what it says")
        if OUTSIDE_ADDRESS.search(body):
            pr.append("outside addresses are not allowed in a script (a link goes in the page as <a href>)")
        if JAVASCRIPT_URL.search(body):
            pr.append("a javascript: address is not allowed")
    if STORAGE.search(code):
        wr.append("uses browser storage or cookies; these pages run in a sandbox where they are blocked")
    if MODALS.search(code):
        wr.append("uses alert, confirm or prompt, which are blocked in a sandbox")
    if HTML_FROM_TEXT.search(code):
        wr.append("builds HTML from text (innerHTML); make sure it never includes anything a visitor typed")
    # deduplicate while keeping order (the same rule can fire for many strings)
    seen = set()
    res.problems = [p for p in pr if not (p in seen or seen.add(p))]
    return res

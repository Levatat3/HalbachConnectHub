import unittest

from halbach_connecthub import jscheck

BS = chr(92)

GOOD = '''
// a tiny app
"use strict";
const el = document.getElementById("r");
let n = 0;
function tick() { n += 1; el.textContent = "count " + n / 2; }
document.addEventListener("click", tick);
const re = /ab+c[/]/g;
fetch("data/items.json").then(r => r.json()).then(d => { el.textContent = `items: ${d.length}`; });
class Thing { constructor(x) { this.x = x; } }
const home = window.location.href;
if (window.location.search == "?a") { n = 2; }
'''


def problems(text):
    return jscheck.check(text).problems


class TestReadableCodePasses(unittest.TestCase):
    def test_plain_readable_code_passes_and_reading_the_address_is_fine(self):
        r = jscheck.check(GOOD)
        self.assertEqual((r.problems, r.warnings), ([], []))

    def test_a_regular_expression_with_a_slash_in_a_class_is_not_mistaken_for_a_comment(self):
        self.assertEqual(problems('var re = /[/]+x/; var y = 4 / 2; var z = a / b / c;'), [])

    def test_words_inside_comments_and_strings_do_not_trigger_the_address_or_length_rules_wrongly(self):
        self.assertEqual(problems('// see the docs\nvar s = "hello world";'), [])


class TestObfuscationIsRefused(unittest.TestCase):
    def test_packed_one_line_files_are_refused(self):
        self.assertTrue(any("line is" in p for p in problems("a=1;" * 900)))

    def test_long_strings_and_encoded_runs_are_refused(self):
        self.assertTrue(any("longer than" in p for p in problems('var s = "%s";' % ("a b " * 100))))
        self.assertTrue(any("encoded data" in p for p in problems('var s = "%s";' % ("Ab1" * 50))))

    def test_strings_full_of_character_codes_are_refused(self):
        s = "".join(BS + "x4" + str(i % 10) for i in range(20))
        self.assertTrue(any("character codes" in p for p in problems('var s = "%s";' % s)))
        self.assertTrue(any("fromCharCode" in p for p in problems("String.fromCharCode(1);" * 6)))

    def test_obfuscator_style_names_are_refused(self):
        self.assertTrue(any("_0x" in p for p in problems("var _0x1a2b = 1;")))

    def test_hidden_and_control_characters_and_lookalike_names_are_refused(self):
        self.assertTrue(any("hidden" in p for p in problems("var a = 1;" + chr(0x200B))))
        self.assertTrue(any("non-ASCII" in p for p in problems("var " + chr(0x430) + "bc = 1;")))

    def test_a_script_that_is_too_big_is_refused(self):
        self.assertTrue(any("larger than" in p for p in problems("// x\n" * 70000)))


class TestRunningTextAsCodeIsRefused(unittest.TestCase):
    def test_eval_function_atob_and_string_timers(self):
        for src, word in (('eval("1");', "eval"), ('var f = new Function("return 1");', "Function"), ('x = atob("aGk=");', "decoding"),
                          ('setTimeout("go()", 5);', "string"), ('this["constructor"]("x");', "constructor")):
            self.assertTrue(any(word in p for p in problems(src)), src)

    def test_modules_and_document_write_are_refused(self):
        self.assertTrue(any("modules" in p for p in problems('import x from "./y.js";')))
        self.assertTrue(any("modules" in p for p in problems('var m = import("./y.js");')))
        self.assertTrue(any("document.write" in p for p in problems('document.write("x");')))


class TestLeavingThePageIsRefused(unittest.TestCase):
    def test_network_features_and_workers_are_refused(self):
        for src in ('new WebSocket("x");', "new XMLHttpRequest();", "navigator.sendBeacon(1);", 'new Worker("w.js");', "navigator.serviceWorker.register(1);"):
            self.assertTrue(problems(src), src)

    def test_outside_addresses_in_a_script_are_refused(self):
        for src in ('var u = "https://evil.example/x";', 'var u = "//cdn.example/x.js";', "var u = `http://a.b/c`;"):
            self.assertTrue(any("outside addresses" in p for p in problems(src)), src)
        self.assertTrue(any("javascript:" in p for p in problems('var u = "javascript:alert(1)";')))

    def test_sending_the_visitor_elsewhere_is_refused_but_reading_the_address_is_not(self):
        for src in ('window.location.href = "x";', 'location = "x";', 'document.location.assign("x");', 'location.replace("x");',
                    'window.open("x");', 'open("x");', "top.location.x = 1;", "parent.document.x();"):
            self.assertTrue(problems(src), src)
        self.assertEqual(problems("var q = window.location.search; if (location.hash == 'a') {}"), [])


class TestWarnings(unittest.TestCase):
    def test_things_that_do_not_work_in_the_sandbox_are_warned_about(self):
        w = jscheck.check('localStorage.x = 1; alert(1); el.innerHTML = x;').warnings
        self.assertEqual(len(w), 3)


if __name__ == "__main__":
    unittest.main()

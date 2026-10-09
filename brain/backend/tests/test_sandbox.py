"""Проверяем, что песочница ДЕЙСТВИТЕЛЬНО изолирует код (это не имитация)."""
import textwrap

from app.sandbox import Sandbox

sb = Sandbox()


def run(code: str, tests: str, **kw):
    return sb.run_tests(code=textwrap.dedent(code), tests=textwrap.dedent(tests), dependencies={}, **kw)


GOOD_CODE = "def run(inp):\n    return {'sum': sum(inp['xs'])}\n"
GOOD_TESTS = """
import unittest, capability
class T(unittest.TestCase):
    def test_sum(self): self.assertEqual(capability.run({'xs':[1,2]})['sum'], 3)
    def test_fail(self): self.assertEqual(capability.run({'xs':[1]})['sum'], 2)
"""


def test_reports_pass_and_fail():
    r = run(GOOD_CODE, GOOD_TESTS)
    assert r.finished and r.report["total"] == 2 and r.report["passed"] == 1
    assert [t["status"] for t in r.report["tests"]] == ["failed", "passed"] or r.report["passed"] == 1


def _security_test(body: str):
    tests = f"""
import unittest, capability
class T(unittest.TestCase):
    def test_x(self):
{textwrap.indent(textwrap.dedent(body), '        ')}
"""
    return run("def run(inp):\n    return {}\n", tests)


def test_cannot_read_outside_workdir():
    r = _security_test("with self.assertRaises(PermissionError):\n    open(r'C:\\Windows\\win.ini').read()" )
    assert r.report["passed"] == 1, r.report


def test_cannot_write_outside_workdir():
    r = _security_test("import os\nwith self.assertRaises(PermissionError):\n    open(os.path.join(os.path.dirname(os.getcwd()), 'x.txt'), 'w')\nwith self.assertRaises(PermissionError):\n    open('../escape.txt', 'w')")
    assert r.report["passed"] == 1, r.report


def test_can_write_inside_workdir():
    r = _security_test("open('ok.txt','w').write('hi')\nself.assertEqual(open('ok.txt').read(), 'hi')")
    assert r.report["passed"] == 1, r.report


def test_no_subprocess():
    r = _security_test("import os\nwith self.assertRaises(PermissionError):\n    os.system('echo hi')")
    assert r.report["passed"] == 1, r.report


def test_no_network():
    r = _security_test("import socket\ns = socket.socket()\nwith self.assertRaises(PermissionError):\n    s.connect(('1.1.1.1', 80))")
    assert r.report["passed"] == 1, r.report


def test_no_secrets_in_environment():
    import os
    os.environ["OPENAI_API_KEY"] = "sk-secret-do-not-leak"
    try:
        r = _security_test("import os\nself.assertNotIn('OPENAI_API_KEY', os.environ)\nself.assertFalse(any('secret' in v for v in os.environ.values()))")
    finally:
        os.environ["OPENAI_API_KEY"] = ""
    assert r.report["passed"] == 1, r.report


def test_timeout_kills_process():
    from app import config
    r = run("def run(inp):\n    return {}\n",
            "import unittest, time\nclass T(unittest.TestCase):\n    def test_hang(self):\n        time.sleep(60)\n", timeout=3)
    assert r.killed_reason == "timeout" and not r.finished


def test_memory_limit_kills_process():
    from app import config
    old = config.SANDBOX_MEMORY_MB
    config.SANDBOX_MEMORY_MB = 150
    try:
        r = run("def run(inp):\n    return {}\n",
                "import unittest\nclass T(unittest.TestCase):\n    def test_eat(self):\n        x = bytearray(900*1024*1024)\n        for i in range(0, len(x), 4096): x[i] = 1\n")
    finally:
        config.SANDBOX_MEMORY_MB = old
    assert r.killed_reason == "memory", (r.killed_reason, r.report, r.stderr[-300:])


def test_allowed_libraries_work_under_hook():
    """pypdf и reportlab должны работать внутри песочницы: создаём PDF и читаем его обратно."""
    tests = """
import unittest, io
class T(unittest.TestCase):
    def test_pdf_roundtrip(self):
        from reportlab.pdfgen import canvas
        from pypdf import PdfReader
        c = canvas.Canvas('t.pdf'); c.drawString(72, 700, 'Hello Frankenstein 123'); c.save()
        self.assertIn('Hello Frankenstein', PdfReader('t.pdf').pages[0].extract_text())
    def test_stdlib_misc(self):
        import json, csv, re, decimal, datetime, statistics, uuid, tempfile, collections, unicodedata
        with tempfile.TemporaryDirectory() as d:
            open(d + '/a.txt', 'w').write('x')
"""
    r = run("def run(inp):\n    return {}\n", tests)
    assert r.report["passed"] == 2, (r.report, r.stderr[-500:])


def test_dependency_modules_are_importable():
    r = sb.run_tests(code="import helper\ndef run(inp):\n    return {'v': helper.run({})['v'] + 1}\n",
                     tests="import unittest, capability\nclass T(unittest.TestCase):\n    def test_a(self): self.assertEqual(capability.run({})['v'], 42)\n",
                     dependencies={"helper": "def run(inp):\n    return {'v': 41}\n"})
    assert r.report["passed"] == 1, r.report


def test_network_organ_reaches_http_only_when_declared():
    """Орган с network=true ходит в сеть (здесь — локальный HTTP-сервер вместо интернета), без флага — заблокирован."""
    import http.server, json as _json, threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = _json.dumps({"current": {"temperature_2m": 12.5}}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/v1/forecast"
    tests = f"""
import unittest, json, urllib.request
class T(unittest.TestCase):
    def test_fetch(self):
        data = json.load(urllib.request.urlopen(urllib.request.Request("{url}", headers={{"User-Agent": "t"}}), timeout=5))
        self.assertEqual(data["current"]["temperature_2m"], 12.5)
"""
    try:
        allowed = sb.run_tests(code="def run(inp):\n    return {}\n", tests=tests, dependencies={}, network=True)
        blocked = sb.run_tests(code="def run(inp):\n    return {}\n", tests=tests, dependencies={}, network=False)
    finally:
        srv.shutdown()
    assert allowed.report["passed"] == 1, allowed.report
    assert blocked.report["passed"] == 0 and "network access is disabled" in blocked.report["tests"][0]["message"]


def test_network_prompt_guides_the_builder():
    from app import prompts
    assert "urllib.request" in prompts.CAPABILITY_CONTRACT and "NO API key" in prompts.CAPABILITY_CONTRACT
    on = prompts.plan_user("weather", [], {"total": 0, "detailed": [], "others": []}, [], [], None, network_allowed=True)
    off = prompts.plan_user("weather", [], {"total": 0, "detailed": [], "others": []}, [], [], None, network_allowed=False)
    assert "ALLOWED" in on and "network" in on and "DISABLED" in off

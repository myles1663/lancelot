import unittest
from sandbox import SandboxExecutor


class TestSandboxImportBlocking(unittest.TestCase):

    def setUp(self):
        self.sandbox = SandboxExecutor()

    def test_import_os_blocked(self):
        result = self.sandbox.execute("import os")
        self.assertFalse(result["success"])
        self.assertIn("Blocked import", result["error"])

    def test_from_os_import_blocked(self):
        result = self.sandbox.execute("from os import system")
        self.assertFalse(result["success"])
        self.assertIn("Blocked import", result["error"])

    def test_dunder_import_blocked(self):
        result = self.sandbox.execute("__import__('os')")
        self.assertFalse(result["success"])
        self.assertIn("Blocked call", result["error"])

    def test_import_subprocess_blocked(self):
        result = self.sandbox.execute("import subprocess")
        self.assertFalse(result["success"])
        self.assertIn("Blocked import", result["error"])

    def test_import_socket_blocked(self):
        result = self.sandbox.execute("import socket")
        self.assertFalse(result["success"])
        self.assertIn("Blocked import", result["error"])

    def test_from_shutil_blocked(self):
        result = self.sandbox.execute("from shutil import rmtree")
        self.assertFalse(result["success"])
        self.assertIn("Blocked import", result["error"])

    def test_multiline_import_blocked(self):
        code = "from \\\n    os \\\n    import system"
        result = self.sandbox.execute(code)
        self.assertFalse(result["success"])
        self.assertIn("Blocked import", result["error"])


class TestSandboxAttrBlocking(unittest.TestCase):

    def setUp(self):
        self.sandbox = SandboxExecutor()

    def test_dunder_class_access_blocked(self):
        result = self.sandbox.execute("x = ''.__class__")
        self.assertFalse(result["success"])
        self.assertIn("Blocked attribute", result["error"])

    def test_dunder_subclasses_blocked(self):
        result = self.sandbox.execute("x = str.__subclasses__()")
        self.assertFalse(result["success"])
        self.assertIn("Blocked attribute", result["error"])

    def test_dunder_globals_blocked(self):
        result = self.sandbox.execute("x = (lambda: 0).__globals__")
        self.assertFalse(result["success"])
        self.assertIn("Blocked attribute", result["error"])

    def test_dunder_builtins_blocked(self):
        result = self.sandbox.execute("x = {}.__class__.__bases__")
        self.assertFalse(result["success"])
        # Either __class__ or __bases__ should be caught
        self.assertIn("Blocked attribute", result["error"])

    def test_getattr_with_dunder_blocked(self):
        result = self.sandbox.execute("getattr('', '__class__')")
        self.assertFalse(result["success"])
        self.assertIn("Blocked", result["error"])


class TestSandboxAllowedCode(unittest.TestCase):

    def setUp(self):
        self.sandbox = SandboxExecutor()

    def test_math_operations(self):
        result = self.sandbox.execute("print(2 + 3 * 4)")
        self.assertTrue(result["success"])
        self.assertIn("14", result["output"])

    def test_list_comprehension(self):
        result = self.sandbox.execute("print([x*2 for x in range(5)])")
        self.assertTrue(result["success"])
        self.assertIn("[0, 2, 4, 6, 8]", result["output"])

    def test_string_operations(self):
        result = self.sandbox.execute("print('hello'.upper())")
        self.assertTrue(result["success"])
        self.assertIn("HELLO", result["output"])

    def test_dict_operations(self):
        result = self.sandbox.execute("d = {'a': 1}; print(d['a'])")
        self.assertTrue(result["success"])
        self.assertIn("1", result["output"])

    def test_injected_globals_work(self):
        # Safe modules can be provided via injected_globals
        import json
        result = self.sandbox.execute(
            "print(json.dumps({'a': 1}))",
            injected_globals={"json": json},
        )
        self.assertTrue(result["success"])
        self.assertIn('{"a": 1}', result["output"])

    def test_functions_and_loops(self):
        code = """
def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)
print(factorial(5))
"""
        result = self.sandbox.execute(code)
        self.assertTrue(result["success"])
        self.assertIn("120", result["output"])


class TestSandboxBuiltins(unittest.TestCase):

    def setUp(self):
        self.sandbox = SandboxExecutor()

    def test_eval_blocked(self):
        result = self.sandbox.execute("eval('1+1')")
        self.assertFalse(result["success"])
        self.assertIn("Blocked call", result["error"])

    def test_exec_blocked(self):
        result = self.sandbox.execute("exec('print(1)')")
        self.assertFalse(result["success"])
        self.assertIn("Blocked call", result["error"])

    def test_compile_blocked(self):
        result = self.sandbox.execute("compile('1+1', '<string>', 'eval')")
        self.assertFalse(result["success"])
        self.assertIn("Blocked call", result["error"])

    def test_open_blocked(self):
        result = self.sandbox.execute("open('/etc/passwd')")
        self.assertFalse(result["success"])
        self.assertIn("Blocked call", result["error"])

    def test_globals_blocked(self):
        result = self.sandbox.execute("globals()")
        self.assertFalse(result["success"])
        self.assertIn("Blocked call", result["error"])


class TestSandboxLimits(unittest.TestCase):

    def setUp(self):
        self.sandbox = SandboxExecutor()

    def test_oversized_code_rejected(self):
        code = "x = 1\n" * 20000
        result = self.sandbox.execute(code)
        self.assertFalse(result["success"])
        self.assertIn("maximum length", result["error"])

    def test_timeout_still_works(self):
        code = "while True: pass"
        result = self.sandbox.execute(code, timeout=1)
        self.assertFalse(result["success"])
        self.assertIn("timed out", result["error"])

    def test_syntax_error_caught(self):
        result = self.sandbox.execute("def foo(")
        self.assertFalse(result["success"])
        self.assertIn("Syntax error", result["error"])


if __name__ == "__main__":
    unittest.main()

import contextlib
import io
import ast
import multiprocessing as mp
import threading
from typing import Optional


class SandboxExecutor:
    """Restricted Python execution environment for generated code."""

    MAX_CODE_LENGTH = 10000

    BLOCKED_IMPORTS = [
        "os", "sys", "subprocess", "shutil", "socket", "ctypes",
        "importlib", "pickle", "shelve", "signal", "multiprocessing",
        "threading", "webbrowser", "http.server", "xmlrpc",
        "code", "codeop", "compile", "compileall",
    ]

    BLOCKED_DUNDER_ATTRS = {
        "__class__", "__subclasses__", "__globals__", "__builtins__",
        "__import__", "__loader__", "__spec__", "__code__", "__func__",
        "__self__", "__module__", "__dict__", "__bases__", "__mro__",
        "__init_subclass__", "__set_name__",
    }

    BLOCKED_CALLS = {
        "__import__", "eval", "exec", "compile", "globals", "locals",
        "vars", "dir", "open", "input", "breakpoint",
    }

    ALLOWED_BUILTINS = {
        "print": print,
        "len": len,
        "range": range,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "dict": dict,
        "list": list,
        "tuple": tuple,
        "set": set,
        "isinstance": isinstance,
        "enumerate": enumerate,
        "zip": zip,
        "map": map,
        "filter": filter,
        "sorted": sorted,
        "reversed": reversed,
        "min": min,
        "max": max,
        "sum": sum,
        "abs": abs,
        "round": round,
        "repr": repr,
        "hasattr": hasattr,
        "True": True,
        "False": False,
        "None": None,
    }

    def __init__(self):
        pass

    def _validate_ast(self, code: str) -> Optional[str]:
        """AST-based code validation. Returns error message or None if safe."""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return f"Syntax error: {e}"

        for node in ast.walk(tree):
            # Block import statements for blocked modules
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_module = alias.name.split(".")[0]
                    if top_module in self.BLOCKED_IMPORTS:
                        return f"Blocked import: '{top_module}' is not allowed in sandbox"

            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top_module = node.module.split(".")[0]
                    if top_module in self.BLOCKED_IMPORTS:
                        return f"Blocked import: '{top_module}' is not allowed in sandbox"

            # Block dangerous function calls
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in self.BLOCKED_CALLS:
                    return f"Blocked call: '{func.id}()' is not allowed in sandbox"
                # Block getattr/setattr with dunder string args
                if isinstance(func, ast.Name) and func.id in ("getattr", "setattr"):
                    if len(node.args) >= 2:
                        arg = node.args[1]
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            if arg.value.startswith("__") and arg.value.endswith("__"):
                                return f"Blocked: '{func.id}' with dunder attribute '{arg.value}'"

            # Block access to dunder attributes
            elif isinstance(node, ast.Attribute):
                if node.attr in self.BLOCKED_DUNDER_ATTRS:
                    return f"Blocked attribute access: '{node.attr}' is not allowed in sandbox"

        return None

    def execute(self, code: str, timeout: int = 5, injected_globals: dict = None) -> dict:
        """
        Executes Python code in a restricted environment.

        Args:
            code: Python source code to execute.
            timeout: Maximum execution time in seconds.
            injected_globals: Additional globals to inject (e.g., mock HTTP clients).

        Returns:
            {"success": bool, "output": str, "error": str}
        """
        # Check code length
        if len(code) > self.MAX_CODE_LENGTH:
            return {
                "success": False,
                "output": "",
                "error": f"Code exceeds maximum length of {self.MAX_CODE_LENGTH} characters",
            }

        # AST-based validation
        ast_error = self._validate_ast(code)
        if ast_error:
            return {"success": False, "output": "", "error": ast_error}

        result = {"success": False, "output": "", "error": ""}

        # Use a separate process when we can so timed-out code can be terminated
        # instead of leaking a live worker thread forever.
        if injected_globals is None:
            return _execute_in_process(code, timeout)

        # Fallback for injected mock/module globals that are not safely serializable.
        sandbox_globals = {"__builtins__": dict(self.ALLOWED_BUILTINS)}
        sandbox_globals.update(injected_globals)

        captured_output = io.StringIO()
        execution_error = [None]

        def _run():
            try:
                with contextlib.redirect_stdout(captured_output):
                    exec(code, sandbox_globals)
            except Exception as e:
                execution_error[0] = str(e)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            result["error"] = f"Execution timed out after {timeout} seconds"
            return result

        result["output"] = captured_output.getvalue()

        if execution_error[0]:
            result["error"] = execution_error[0]
        else:
            result["success"] = True

        return result


def _sandbox_process_main(code: str, result_queue: mp.Queue) -> None:
    """Execute sandboxed code in an isolated worker process."""
    sandbox_globals = {"__builtins__": dict(SandboxExecutor.ALLOWED_BUILTINS)}
    captured_output = io.StringIO()
    result = {"success": False, "output": "", "error": ""}

    try:
        with contextlib.redirect_stdout(captured_output):
            exec(code, sandbox_globals)
    except Exception as exc:
        result["error"] = str(exc)
    else:
        result["success"] = True

    result["output"] = captured_output.getvalue()
    result_queue.put(result)


def _execute_in_process(code: str, timeout: int) -> dict:
    """Run sandboxed code in a child process so timeout enforcement is real."""
    result_queue: mp.Queue = mp.Queue()
    process = mp.Process(target=_sandbox_process_main, args=(code, result_queue))
    process.start()
    process.join(timeout=timeout)

    if process.is_alive():
        process.terminate()
        process.join(timeout=1)
        return {
            "success": False,
            "output": "",
            "error": f"Execution timed out after {timeout} seconds",
        }

    if not result_queue.empty():
        return result_queue.get()

    return {
        "success": False,
        "output": "",
        "error": "Sandbox worker exited without returning a result",
    }

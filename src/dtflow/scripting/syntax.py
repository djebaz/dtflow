"""Parse-only resource policy and diagnostics; never installs a script host.

The reserved-thread entry is used only in the disposable syntax child. Its
process-local stack-size setting must not be applied in the desktop process.
Acorn stays pinned; script execution limits belong to ScriptEngine instead.
"""

import threading

PARSER_MEMORY_BYTES = 64 * 1024 * 1024
PARSER_STACK_BYTES = 2 * 1024 * 1024
NATIVE_STACK_BYTES = 8 * 1024 * 1024
FAILURE_PREFIX = "Parser could not complete: "


def is_parser_failure(message: str) -> bool:
    """Recognize current and historical resource failures without inventing a line."""
    if message.startswith(FAILURE_PREFIX):
        return True
    text = message.strip().lower()
    if text.startswith(("rangeerror:", "internalerror:")):
        return any(part in text for part in ("call stack size exceeded", "stack overflow", "out of memory"))
    return text.startswith((
        "maximum call stack size exceeded", "stack overflow", "script is nested too deeply to analyze",
        "the script did not finish parsing in time", "the syntax check process ended unexpectedly",
    ))


def parser_failure(reason: str) -> str:
    return FAILURE_PREFIX + reason + " Syntax remains unverified; the script was not executed."


def _parse(source: str) -> str | None:
    # Create and use the Context on the same reserved native thread.
    import quickjs

    from dtflow.scripting.engine import acorn_source

    initializing = True
    try:
        context = quickjs.Context()
        context.set_memory_limit(PARSER_MEMORY_BYTES)
        context.set_max_stack_size(PARSER_STACK_BYTES)
        context.eval(acorn_source())
        initializing = False
        context.set("__source", source)
        context.eval(
            'acorn.parse(__source, {ecmaVersion: "latest", sourceType: "script", '
            "allowReturnOutsideFunction: true, allowHashBang: false}); true"
        )
        return None
    except quickjs.StackOverflow:
        return parser_failure("the bounded parser stack limit was reached.")
    except quickjs.JSException as exc:
        lines = str(exc).splitlines()
        message = lines[0] if lines else ""
        if not initializing and message.startswith("SyntaxError:"):
            return message
        if is_parser_failure(message):
            return parser_failure("the bounded parser stack limit was reached.")
        return parser_failure("parser initialization or a resource/internal limit prevented analysis.")
    except MemoryError:
        return parser_failure("the bounded parser memory limit was reached.")


def parse_in_reserved_thread(source: str) -> str | None:
    """Parse in the child with native headroom above the guarded JS stack limit.

    The parent still owns the process deadline and kills the whole child on a
    timeout. No fallback to an inadequately sized native stack is permitted.
    """
    results: list[str | None] = []

    def run():
        try:
            results.append(_parse(source))
        except Exception:
            results.append(parser_failure("the parser worker encountered an internal error."))

    thread = threading.Thread(target=run, name="dtflow-acorn", daemon=True)
    try:
        previous = threading.stack_size()
        try:
            threading.stack_size(NATIVE_STACK_BYTES)
            thread.start()
        finally:
            threading.stack_size(previous)
    except (RuntimeError, ValueError, OSError):
        if thread.is_alive():
            thread.join()
        return parser_failure("a bounded native parser thread could not be reserved.")
    thread.join()
    return results[0] if results else parser_failure("the parser worker ended without a result.")

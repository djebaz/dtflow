"""Owned capture/privacy/record policy, shared with the future Qt-free reader.

No Qt, provider, persistence, JSON Schema runtime dependency, or file I/O.
"""

import html
import json
import logging
import math
import os
import re
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from time import monotonic_ns

from dtflow.diagnostics import ATTEMPT

RECORD_VERSION = 1
MAX_RECORD_BYTES = 1_048_576
QUEUE_RECORDS = 1024
QUEUE_BYTES = 8 * 1024 * 1024
MAX_DEPTH = 16
MAX_NODES = 4096
INSTRUMENTATION_VERSION = "gallery-jsonl-v1"
AGGREGATION_POLICY = "source-64-or-10s-bounded64-legacy1dp-v1"
SECRET_ENVIRONMENT = ("DRAWTHINGS_API_KEY", "DRAWTHINGS_SHARED_SECRET")
EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
SESSION_NAME = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_HOME = str(Path.home())
_SOURCE_ROOT = str(Path(__file__).parent.parent).replace("\\", "/") + "/"
_PRIVATE_KEY = re.compile(
    r"(?i)(authorization|credential|password|secret|token|api[_-]?key|"
    r"prompt|base64|payload|prepared_asset|cloud_reference|(?:^|_)(?:path|directory)(?:$|_))"
)
_PATTERNS = (
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [redacted]"),
    (
        re.compile(
            r"""(?i)\b(authorization|api[_-]?key|token|secret|password|shared[_-]?secret)(["']?\s*[:=]\s*)\S+"""
        ),
        r"\1\2[redacted]",
    ),
    (re.compile(r"(?i)\b(?:positive[_ ]?|negative[_ ]?)?prompt\s*[:=].*"), "prompt=[redacted]"),
    (re.compile(r"[A-Za-z0-9+/=_-]{200,}"), "[redacted-payload]"),
    (re.compile(r"""(?i)\bfile:/+[^\s"'<>|;]*"""), "[redacted-path]"),
    # Unquoted paths with spaces are ambiguous: a path ends at its last
    # extension-bearing segment when no separator follows in the clause,
    # otherwise the remainder of that clause is redacted conservatively rather
    # than leaking trailing directory words. A slash inside MIME types, ratios,
    # reviewed model IDs, URLs or spaced arithmetic is not a path.
    (
        re.compile(
            r"""(?:(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\|(?<![\w:%/])/(?![/\s])|(?<!\w)(?:~|\.{1,2})[\\/])"""
            r"""(?:[^\r\n"'<>|;]*?\.[A-Za-z0-9]{1,8}(?=[\s,)\]]|$)(?![^\r\n"'<>|;]*[\\/])|[^\r\n"'<>|;]*)"""
        ),
        "[redacted-path]",
    ),
)
# Identifiers, numbers and field names cannot match any pattern above.
_PLAIN = re.compile(r"[A-Za-z0-9_.-]{0,64}")


class CaptureRejected(ValueError):
    """An observation cannot safely enter the bounded pipeline."""

    def __init__(self, reason: str, size: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.size = size


def sanitize(text: str, environ=None) -> str:
    """Second-line secret/path protection; producer projections remain mandatory."""
    environ = os.environ if environ is None else environ
    for name in SECRET_ENVIRONMENT:
        secret = environ.get(name, "").strip()
        if len(secret) >= 4:
            text = text.replace(secret, "[redacted]")
    if _PLAIN.fullmatch(text):
        return text
    if len(_HOME) > 1:
        text = text.replace(_HOME, "~")
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def utc_timestamp(seconds: float | None = None) -> str:
    moment = datetime.now(UTC) if seconds is None else datetime.fromtimestamp(seconds, UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def api_error_data(*, message=None, body=None, headers=(), private=(), truncated=False) -> dict:
    """Project bounded response primitives; never format arbitrary provider objects.

    This supplements, rather than bypasses, normal record capture. Incomplete
    text is omitted: a cut-off credential cannot safely be exact-match redacted.
    """
    private_key = re.compile(
        r"(?i)(authorization|credential|password|secret|token|api[_-]?key|prompt|base64|payload|blob|"
        r"image|tensor|content|signed|url|uri|reference|remote_id|user_id|userid|nonce|sha256|checksum|file|path|^key$)"
    )
    hidden = {value for value in private if isinstance(value, str) and value}
    hidden.update(json.dumps(value, ensure_ascii=True)[1:-1] for value in tuple(hidden))
    hidden = tuple(sorted(hidden, key=len, reverse=True))
    pairs = tuple(islice(headers.items() if hasattr(headers, "items") else headers, 64))
    media_type = next(
        (
            v.split(";", 1)[0].strip().lower()
            for k, v in pairs
            if isinstance(k, str) and k.lower() == "content-type" and isinstance(v, str)
        ),
        "",
    )
    nodes = 0
    limited = bool(truncated)

    def text(value):
        nonlocal limited
        if len(value) > 8192:
            limited = True
            return "[omitted: text exceeds capture limit]"
        value = html.unescape(value)
        value = value.encode("utf-8", "replace").decode("utf-8")
        value = re.sub(
            r"(?is)<([\w:.-]*(?:token|secret|password|authorization|key|prompt|blob|base64|image|tensor|url|uri|reference|resource|path|file)[\w:.-]*)\b[^>]*>.*?(?:</\1\s*>|$)",
            "[redacted]",
            value,
        )
        for secret in hidden:
            value = value.replace(secret, "[redacted]")
        value = re.sub(
            r"""(?i)(["']?[\w-]*(?:token|secret|password|authorization|key|prompt|blob|base64|url|uri|reference|file|path|user[_-]?id|nonce|checksum|sha256)[\w-]*["']?\s*[:=]\s*)(?:["'][^"']*(?:["']|$)|[^,\s}]+)""",
            r"\1[redacted]",
            value,
        )
        # Keep field labels intact until the key/value rule above has hidden their values.
        # Also hide unissued/unknown compound credential-shaped atoms, but not "token expired".
        value = re.sub(
            r"(?i)\b(?=[A-Za-z0-9_-]*[-_])(?:[A-Za-z0-9]+[-_])*(?:token|secret|password|credential|api[_-]key)(?:[-_][A-Za-z0-9]+)*\b",
            "[redacted]",
            value,
        )
        value = re.sub(r"https?://[^\s<>\"']+", "[redacted-url]", value, flags=re.IGNORECASE)
        value = re.sub(r"\bdk_[A-Za-z0-9_-]+", "[redacted-key]", value)
        value = re.sub(r"[A-Za-z0-9_+/=-]{64,}", "[redacted-payload]", value)
        value = sanitize(re.sub(r"<[^>]*>", " ", value))
        # Redact before shortening, including values that straddle the display boundary.
        if len(value) > 2048:
            limited = True
            suffix = " [truncated]"
            value = value[: 2048 - len(suffix)] + suffix
        return value

    def value(item, depth=0):
        nonlocal nodes, limited
        nodes += 1
        if nodes > 128 or depth > 6:
            limited = True
            return "[omitted: structure exceeds capture limit]"
        if item is None or type(item) in (bool, int):
            return item if type(item) is not int or item.bit_length() <= 128 else "[omitted integer]"
        if type(item) is float:
            return item if math.isfinite(item) else "[omitted non-finite number]"
        if type(item) is str:
            return text(item)
        if type(item) is dict:
            result = {}
            for index, (key, child) in enumerate(item.items()):
                if index >= 32 or nodes >= 128:
                    limited = True
                    break
                if type(key) is not str:
                    continue
                # Dynamic filename/hash keys can themselves be private.
                safe_key = text(key)
                if private_key.search(key) or safe_key != key or len(key) > 128:
                    result[f"redacted_field_{index}"] = "[redacted]"
                else:
                    result[key] = value(child, depth + 1)
            return result
        if type(item) in (list, tuple):
            if len(item) > 16:
                limited = True
            return [value(child, depth + 1) for child in item[:16]]
        return "[omitted non-text detail]"

    result = {}
    if (
        body is not None
        and media_type
        and not (media_type.startswith("text/") or media_type.endswith("json") or media_type.endswith("xml"))
    ):
        body = "[omitted binary response]"
    if type(body) is bytes:
        if len(body) > 8192 or truncated:
            body = "[omitted: response exceeds capture limit]"
            limited = True
        else:
            try:
                if any(byte < 32 and byte not in (9, 10, 13) for byte in body):
                    raise UnicodeError
                body = body.decode("utf-8")
            except UnicodeError:
                body = "[omitted binary response]"
    if type(body) is str and len(body) <= 8192:
        try:
            body = json.loads(body)
        except (ValueError, RecursionError):
            pass
    if body is not None:
        safe_body = value(body)
        encoded = json.dumps(safe_body, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 8192:
            result["response_body"] = "[omitted: projected response exceeds capture limit]"
            limited = True
        else:
            result["response_body"] = encoded
        if message is None and type(body) is dict:
            error = body.get("error")
            message = error.get("message") if type(error) is dict else error if type(error) is str else None
            message = message or body.get("message") or body.get("description")
        elif message is None and type(body) is str and not limited and not body.startswith("[omitted"):
            message = body
    if type(message) is str and message.strip():
        result["server_message"] = text(message)
    allowed = {"content-type", "server", "date", "retry-after", "x-request-id", "x-correlation-id", "cf-ray"}
    for index, (key, item) in enumerate(pairs):
        if index >= 64:
            break
        if type(key) is str and key.lower() in allowed and type(item) is str:
            result["header_" + key.lower().replace("-", "_")] = text(item)
    result["response_truncated"] = limited
    return result


class _Snapshot:
    def __init__(self, reserve=None):
        self.nodes = 0
        self.bytes = 0
        self.sanitize_strings = True
        # Refresh per observation, never cache credentials across auth changes.
        self.environ = {name: os.environ.get(name, "") for name in SECRET_ENVIRONMENT}
        self.reserve = reserve

    def charge(self, size: int) -> None:
        if self.bytes + size > MAX_RECORD_BYTES:
            raise CaptureRejected("capture_limit", self.bytes + size)
        if self.reserve is not None:
            self.reserve(size)
        self.bytes += size

    def value(self, value, depth: int = 0):
        self.nodes += 1
        if self.nodes > MAX_NODES or depth > MAX_DEPTH:
            raise CaptureRejected("capture_limit")
        self.charge(64)
        if value is None or type(value) is bool:
            return value
        if isinstance(value, str):
            # Four bytes/character plus the Python string is a conservative
            # admission estimate. It may reject before final UTF-8 encoding.
            self.charge(4 * len(value))
            text = str.__str__(value)
            return sanitize(text, self.environ) if self.sanitize_strings else text
        if type(value) is int:
            if value.bit_length() > 128:
                raise CaptureRejected("capture_limit")
            return value
        if type(value) is float:
            if not math.isfinite(value):
                raise CaptureRejected("unsupported_value")
            return value
        if type(value) in (list, tuple):
            if len(value) > MAX_NODES:
                raise CaptureRejected("capture_limit")
            return [self.value(item, depth + 1) for item in value]
        if type(value) is dict:
            if len(value) > MAX_NODES:
                raise CaptureRejected("capture_limit")
            result = {}
            for key, item in value.items():
                if type(key) is not str or len(key) > 200:
                    raise CaptureRejected("unsupported_value")
                safe_key = self.value(key, depth + 1)
                if safe_key in result:
                    raise CaptureRejected("unsupported_value")
                result[safe_key] = "[redacted]" if _PRIVATE_KEY.search(key) else self.value(item, depth + 1)
            return result
        raise CaptureRejected("unsupported_value")


def _exception(error: BaseException, budget: _Snapshot, depth: int = 0) -> dict:
    """Capture chains and every frame without locals or source-code snippets.

    Unsupported groups/deep traces are rejected, never silently cut short.
    Avoid retaining traceback/frame/exception objects across the queue.
    """
    if depth >= MAX_DEPTH or isinstance(error, BaseExceptionGroup):
        raise CaptureRejected("capture_limit")
    name = type(error).__name__
    message = budget.value(str(error))
    parts = []
    previous = error.__cause__
    if previous is None and not error.__suppress_context__:
        previous = error.__context__
    if previous is not None:
        parts.append(_exception(previous, budget, depth + 1)["traceback"])
        parts.append("\nThe above exception caused or preceded the following exception:\n")
    parts.append("Traceback (most recent call last):\n")
    frame = error.__traceback__
    count = 0
    while frame is not None:
        count += 1
        if count > MAX_NODES:
            raise CaptureRejected("capture_limit")
        code = frame.tb_frame.f_code
        filename = code.co_filename.replace("\\", "/")
        if filename.startswith(_SOURCE_ROOT):
            location = filename[len(_SOURCE_ROOT) :]
        elif "/site-packages/" in filename:
            location = "site-packages/" + filename.split("/site-packages/", 1)[1]
        else:
            location = "<external>"
        parts.append(budget.value(f'  File "{location}", line {frame.tb_lineno}, in {code.co_name}\n'))
        frame = frame.tb_next
    parts.append(f"{name}: {message}\n")
    trace = "".join(parts)
    budget.charge(4 * len(trace))
    return {"type": name, "message": message, "traceback": trace}


def capture(record: logging.LogRecord, *, reserve=None) -> tuple[dict, int]:
    budget = _Snapshot(reserve)
    name = getattr(record, "dtflow_event", "logging.message")
    if type(name) is not str or len(name) > 180 or not EVENT_NAME.fullmatch(name):
        raise CaptureRejected("unsupported_value")
    if name in {"session.started", "session.closed", "diagnostics.loss", "diagnostics.record_rejected"}:
        # Integrity controls are listener-owned, never ordinary producer events.
        raise CaptureRejected("unsupported_value")
    kind = getattr(record, "dtflow_kind", "event")
    if type(kind) is not str or kind not in ("event", "measurement", "aggregate"):
        raise CaptureRejected("unsupported_value")
    # Never call getMessage()/repr on arbitrary arguments or object graphs.
    budget.sanitize_strings = False
    template = budget.value(record.msg)
    if not isinstance(template, str):
        raise CaptureRejected("unsupported_value")
    before_args = budget.bytes
    args = budget.value(record.args)
    args_bytes = budget.bytes - before_args
    budget.sanitize_strings = True
    widths = 0
    conversions_count = 0
    if args:
        conversions = re.finditer(
            r"%(?:\([^)]{1,200}\))?[-+0 #]*(\d+|\*)?(?:\.(\d+|\*))?[hlL]?[diouxXeEfFgGcrsa%]",
            template,
        )
        end = 0
        for match in conversions:
            if "%" in template[end : match.start()]:
                raise CaptureRejected("unsupported_value")
            for part in match.groups():
                if part == "*":
                    raise CaptureRejected("unsupported_value")
                if part and (len(part) > 7 or int(part) > MAX_RECORD_BYTES):
                    raise CaptureRejected("capture_limit")
                widths += int(part) if part else 0
            conversions_count += match.group()[-1] != "%"
            end = match.end()
        if "%" in template[end:]:
            raise CaptureRejected("unsupported_value")
    try:
        if args:
            # Reserve the expansion before %-formatting can allocate it: each
            # argument appears once (its snapshot charge already covers str and
            # escaped repr), plus padding/precision and numeric digits per
            # conversion. Snapshots hold only native primitives, never arbitrary repr.
            budget.charge(4 * len(template) + args_bytes + 4 * widths + 512 * conversions_count)
        message = budget.value(template % (tuple(args) if isinstance(args, list) else args) if args else template)
    except CaptureRejected:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        raise CaptureRejected("unsupported_value") from exc
    result = {
        "v": RECORD_VERSION,
        "ts": getattr(record, "dtflow_source_ts", None) or utc_timestamp(record.created),
        "level": record.levelname,
        "component": budget.value(record.name),
        "event": name,
    }
    if kind != "event":
        result["kind"] = kind
    data = budget.value(getattr(record, "dtflow_data", {}))
    if type(data) is not dict:
        raise CaptureRejected("unsupported_value")
    if data or not message:
        result["data"] = data
    if message:
        result["msg"] = message
    context = budget.value(getattr(record, "dtflow_context", {}))
    if type(context) is not dict:
        raise CaptureRejected("unsupported_value")
    # Upstream summary populations can span worker contexts; their GUI flush
    # context must not invent a causal attempt association.
    attempt = "-" if name == "gallery.source_summary" else ATTEMPT.get()
    if attempt and attempt != "-":
        context["attempt_id"] = budget.value(attempt)
    if context:
        result["ctx"] = context
    if kind in ("measurement", "aggregate"):
        result["mono_ns"] = getattr(record, "dtflow_mono_ns", monotonic_ns())
    agg = getattr(record, "dtflow_aggregation", None)
    if kind == "aggregate":
        if type(agg) is not dict:
            raise CaptureRejected("unsupported_value")
        result["agg"] = budget.value(agg)
    elif agg is not None:
        raise CaptureRejected("unsupported_value")
    if record.exc_info and record.exc_info[1] is not None:
        result["error"] = _exception(record.exc_info[1], budget)
    validate_record(result, persisted=False)
    return result, budget.bytes + 1024


def validate_record(record: dict, *, persisted: bool = True) -> None:
    """Central v1 shape/critical-field enforcement, not a completeness certificate."""
    if type(record) is not dict:
        raise CaptureRejected("unsupported_value")
    required = {"v", "ts", "level", "component", "event"} | ({"seq"} if persisted else set())
    allowed = required | {"seq", "kind", "msg", "data", "mono_ns", "ctx", "error", "agg"}
    if (
        not required <= record.keys()
        or not record.keys() <= allowed
        or type(record["v"]) is not int
        or record["v"] != RECORD_VERSION
    ):
        raise CaptureRejected("unsupported_value")
    if persisted and (type(record["seq"]) is not int or record["seq"] < 1):
        raise CaptureRejected("unsupported_value")
    if type(record["ts"]) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", record["ts"]):
        raise CaptureRejected("unsupported_value")
    try:
        datetime.fromisoformat(record["ts"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise CaptureRejected("unsupported_value") from exc
    if type(record["component"]) is not str or not 1 <= len(record["component"]) <= 200:
        raise CaptureRejected("unsupported_value")
    if type(record["event"]) is not str or len(record["event"]) > 180 or not EVENT_NAME.fullmatch(record["event"]):
        raise CaptureRejected("unsupported_value")
    if not ("msg" in record or "data" in record) or ("msg" in record and type(record["msg"]) is not str):
        raise CaptureRejected("unsupported_value")
    if type(record.get("data", {})) is not dict or type(record.get("ctx", {})) is not dict:
        raise CaptureRejected("unsupported_value")
    for key in (
        "backend",
        "job_id",
        "attempt_id",
        "operation_id",
        "queue_id",
        "item_id",
        "benchmark_run_id",
        "trial_id",
        "action_id",
    ):
        if key in record.get("ctx", {}) and type(record["ctx"][key]) is not str:
            raise CaptureRejected("unsupported_value")
    if "mono_ns" in record and (type(record["mono_ns"]) is not int or record["mono_ns"] < 0):
        raise CaptureRejected("unsupported_value")
    if record.get("kind", "event") not in ("event", "measurement", "aggregate"):
        raise CaptureRejected("unsupported_value")
    if record["level"] not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise CaptureRejected("unsupported_value")
    name = record["event"]
    data = record.get("data", {})
    fields = {
        "gallery.gui_gap": {
            "elapsed_ms": (int, float),
            "mode": (str,),
            "mounted": (int,),
            "query_busy": (bool,),
            "preparation_busy": (bool,),
        },
        "gallery.query_done": {
            "elapsed_ms": (int, float),
            "returned": (int,),
            "source": (str,),
            "error": (str, type(None)),
        },
        "gallery.scroll_intent": {"direction": (str,), "source": (str,), "offset": (int,), "mounted": (int,)},
    }.get(name, {})
    controls = {
        "session.started": {
            "session_id": (str,),
            "version": (str,),
            "source_commit": (str,),
            "build_state": (str,),
            "runtime": (str,),
            "platform": (str,),
            "dev": (bool,),
            "instrumentation_version": (str,),
            "schema_version": (int,),
            "queue_max_records": (int,),
            "queue_max_bytes": (int,),
            "max_record_bytes": (int,),
            "aggregation_policy": (str,),
        },
        "session.closed": {
            key: (int,)
            for key in (
                "observed",
                "admitted",
                "rejected",
                "dropped",
                "written_records",
                "aggregate_source_samples",
                "writer_failures",
                "serialization_failures",
            )
        },
        "diagnostics.loss": {"reason": (str,), "count": (int,), "affects_measurements": (bool,)},
        "diagnostics.record_rejected": {
            "original_event": (str,),
            "reason": (str,),
            "limit_bytes": (int,),
            "original_content_available": (bool,),
        },
    }.get(name, {})
    for key, types in controls.items():
        if key not in data or type(data[key]) not in types or (type(data[key]) is int and data[key] < 0):
            raise CaptureRejected("unsupported_value")
    if controls and record.get("kind", "event") != "event":
        raise CaptureRejected("unsupported_value")
    if name == "session.started" and (
        not SESSION_NAME.fullmatch(data["session_id"])
        or data["schema_version"] != RECORD_VERSION
        or data["build_state"] not in ("clean", "dirty", "unknown")
    ):
        raise CaptureRejected("unsupported_value")
    if name == "diagnostics.record_rejected" and data["original_content_available"] is not False:
        raise CaptureRejected("unsupported_value")
    if name == "diagnostics.record_rejected" and data["reason"] not in {
        "record_too_large",
        "capture_too_large",
        "unsupported_value",
        "serialization_error",
        "other",
    }:
        raise CaptureRejected("unsupported_value")
    if name == "diagnostics.loss" and data["reason"] not in {
        "queue_records",
        "queue_bytes",
        "capture_limit",
        "writer_error",
        "serialization_error",
        "shutdown_timeout",
        "other",
    }:
        raise CaptureRejected("unsupported_value")
    if "error" in record:
        error = record["error"]
        if (
            type(error) is not dict
            or set(error) != {"type", "message", "traceback"}
            or any(type(value) is not str for value in error.values())
        ):
            raise CaptureRejected("unsupported_value")
    for key, types in fields.items():
        if key not in data or type(data[key]) not in types:
            raise CaptureRejected("unsupported_value")
        if type(data[key]) in (int, float) and (not math.isfinite(data[key]) or data[key] < 0):
            raise CaptureRejected("unsupported_value")
    if fields and (record.get("kind") != "measurement" or type(record.get("mono_ns")) is not int):
        raise CaptureRejected("unsupported_value")
    if name == "gallery.query_done" and data["source"] not in ("live", "snapshot", "unknown"):
        raise CaptureRejected("unsupported_value")
    if name == "gallery.scroll_intent" and data["direction"] not in ("forward", "backward"):
        raise CaptureRejected("unsupported_value")
    if record.get("kind") == "aggregate":
        agg = record.get("agg", {})
        if (
            type(agg) is not dict
            or not {"samples", "window_start", "window_end", "metrics"} <= agg.keys()
            or not agg["metrics"]
            or type(agg["samples"]) is not int
            or agg["samples"] < 1
        ):
            raise CaptureRejected("unsupported_value")
        if not agg.keys() <= {"samples", "window_start", "window_end", "metrics", "dimensions"}:
            raise CaptureRejected("unsupported_value")
        dimensions = agg.get("dimensions", {})
        if type(dimensions) is not dict or any(
            type(value) not in (str, int, float, bool) for value in dimensions.values()
        ):
            raise CaptureRejected("unsupported_value")
        bounds = []
        for stamp in (agg["window_start"], agg["window_end"]):
            if type(stamp) is not str:
                raise CaptureRejected("unsupported_value")
            try:
                parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError as exc:
                raise CaptureRejected("unsupported_value") from exc
            if parsed.tzinfo is None:
                raise CaptureRejected("unsupported_value")
            bounds.append(parsed)
        if bounds[0] > bounds[1]:
            raise CaptureRejected("unsupported_value")
        if type(agg["metrics"]) is not dict:
            raise CaptureRejected("unsupported_value")
        for key, metric in agg["metrics"].items():
            if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or type(metric) is not dict:
                raise CaptureRejected("unsupported_value")
            if not {"count", "mean", "max", "total"} <= metric.keys() or not metric.keys() <= {
                "count",
                "mean",
                "max",
                "total",
                "p95",
            }:
                raise CaptureRejected("unsupported_value")
            if type(metric["count"]) is not int or metric["count"] < 1:
                raise CaptureRejected("unsupported_value")
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in metric.values()):
                raise CaptureRejected("unsupported_value")
    elif "agg" in record:
        raise CaptureRejected("unsupported_value")

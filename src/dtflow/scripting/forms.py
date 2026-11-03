"""Bounded, Qt-free requestFromUser contracts for the pinned upstream descriptors.

Sections contribute nested arrays (not flattened inputs). Display-only components
contribute nothing. Selectable images return the selected source's integer index.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from collections.abc import Callable

MAX_FORM_BYTES = 1_000_000
MAX_ANSWER_BYTES = 6_000_000
MAX_FIELDS = 512
MAX_DEPTH = 12
MAX_TEXT = 64_000
MAX_OPTIONS = 256
MAX_IMAGES = 16
KINDS = frozenset(
    {
        "switch",
        "slider",
        "doubleSlider",
        "textField",
        "comboBox",
        "segmented",
        "menu",
        "size",
        "imageField",
        "directory",
        "image",
        "section",
        "plainText",
        "markdown",
    }
)


def bounded_json(value, limit: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("Script form data must be finite, bounded JSON.") from exc
    if len(text) > limit:
        raise ValueError("Script form data exceeds its size limit.")
    return text


def number(value) -> bool:
    return type(value) in (int, float) and abs(value) <= 1_000_000_000 and math.isfinite(value)


def image_bytes(source: str) -> bytes:
    """Only embedded raster images; no URLs, SVG, filesystem paths or HTML."""
    if not isinstance(source, str) or len(source) > MAX_ANSWER_BYTES:
        raise ValueError("Image source exceeds the form image limit.")
    match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", source)
    if match is None:
        raise ValueError("Form images must be embedded PNG, JPEG or WebP data, not paths or URLs.")
    try:
        data = base64.b64decode(match[2], validate=True)
    except ValueError as exc:
        raise ValueError("Invalid embedded form image.") from exc
    signatures = {
        "png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "jpeg": data.startswith(b"\xff\xd8\xff"),
        "webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP",
    }
    if not signatures[match[1]]:
        raise ValueError("Form image content does not match its raster type.")
    return data


def input_token(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"dtflow-input:[0-9a-f]{32}", value) is not None


def image_sources(spec: dict) -> list[str]:
    source = spec.get("src", "")
    return source if isinstance(source, list) else [source]


def decimals(spec: dict) -> int:
    value_type = spec.get("valueType") or {}
    return value_type.get("fractional", 2)


def limits(spec: dict) -> tuple[float, float]:
    return spec.get("minValue", 0), spec.get("maxValue", 1)


def validate_components(components) -> None:
    bounded_json(components, MAX_FORM_BYTES)
    count = 0

    def visit(fields, depth):
        nonlocal count
        if not isinstance(fields, list) or depth > MAX_DEPTH:
            raise ValueError("Script form sections must be bounded arrays.")
        for spec in fields:
            count += 1
            if (
                count > MAX_FIELDS
                or not isinstance(spec, dict)
                or not isinstance(spec.get("type"), str)
                or spec["type"] not in KINDS
            ):
                raise ValueError("Script form has too many fields or an unsupported component.")
            kind = spec["type"]
            if "multiple" in spec:
                multiple = spec["multiple"]
                if kind not in ("imageField", "directory") or type(multiple) is not int or not 1 <= multiple <= 4096:
                    raise ValueError("Only imageField/directory accept a pixel multiple, an integer within 1..4096.")
            for key in ("title", "detail", "placeholder"):
                value = spec.get(key)
                if value is not None and (not isinstance(value, str) or len(value) > MAX_TEXT):
                    raise ValueError("Script form captions must be bounded text.")
            for key in ("multiline", "multiSelect", "selectable", "isOn"):
                if key in spec and spec[key] is not None and type(spec[key]) is not bool:
                    raise ValueError("Script form switches must be boolean.")
            height = spec.get("height")
            if height is not None and (not number(height) or not 1 <= height <= 4096):
                raise ValueError("Script form height must be within 1..4096.")
            if kind == "section":
                visit(spec.get("views") or [], depth + 1)
            elif kind in ("plainText", "markdown", "textField"):
                if not isinstance(spec.get("value", ""), str) or len(spec.get("value", "")) > MAX_TEXT:
                    raise ValueError("Script form text exceeds its limit.")
            elif kind in ("comboBox", "menu", "segmented"):
                options = spec.get("options")
                if (
                    not isinstance(options, list)
                    or not 1 <= len(options) <= MAX_OPTIONS
                    or any(not isinstance(x, str) or len(x) > 4096 for x in options)
                ):
                    raise ValueError("Script form options must be a nonempty bounded text array.")
            elif kind in ("slider", "doubleSlider"):
                low, high = limits(spec)
                if not number(low) or not number(high) or low >= high:
                    raise ValueError("Script slider bounds must be finite and increasing.")
                value_type = spec.get("valueType") or {}
                if not isinstance(value_type, dict) or set(value_type) - {"fractional", "percent", "scale"}:
                    raise ValueError("Unknown script slider value type.")
                if len(value_type) > 1:
                    raise ValueError("Script slider must have one value type.")
                if any(type(value_type[key]) is not bool for key in ("percent", "scale") if key in value_type):
                    raise ValueError("Script slider percent/scale flags must be boolean.")
                if "fractional" in value_type and (
                    type(value_type["fractional"]) is not int or not 0 <= value_type["fractional"] <= 6
                ):
                    raise ValueError("Script slider precision must be within 0..6.")
            elif kind == "size":
                low, high = spec.get("minValue", 64), spec.get("maxValue", 8192)
                if type(low) is not int or type(high) is not int or not 1 <= low <= high <= 1_000_000:
                    raise ValueError("Script size bounds must be positive bounded integers.")
            elif kind == "image":
                sources = image_sources(spec)
                if not 1 <= len(sources) <= MAX_IMAGES:
                    raise ValueError("Too many script form images.")
                for source in sources:
                    image_bytes(source)

    visit(components, 0)
    # Invalid defaults must not be silently clamped by Qt.
    validate_answers(components, initial_answers(components), check_components=False)


def contributes(spec: dict) -> bool:
    return spec["type"] not in ("plainText", "markdown") and (spec["type"] != "image" or bool(spec.get("selectable")))


def initial_answers(components: list[dict]) -> list:
    answers = []
    for spec in components:
        kind = spec["type"]
        if not contributes(spec):
            continue
        if kind == "section":
            value = initial_answers(spec.get("views") or [])
        elif kind == "switch":
            value = spec.get("isOn", False)
        elif kind in ("menu", "segmented"):
            value = spec.get("index", 0)
        elif kind == "size":
            value = [spec.get("width"), spec.get("height")]
        elif kind == "imageField":
            value = []
        elif kind == "directory":
            value = ""
        elif kind == "image":
            value = 0
        else:
            value = spec.get("value", "" if kind in ("textField", "comboBox") else 0)
        answers.append(value)
    return answers


def validate_answers(components, answers, *, origin: Callable | None = None, check_components=True) -> None:
    if check_components:
        validate_components(components)
    bounded_json(answers, MAX_ANSWER_BYTES)

    def visit(fields, values, path=()):
        fields = [spec for spec in fields if contributes(spec)]
        if not isinstance(values, list) or len(values) != len(fields):
            raise ValueError("Expected one ordered answer per input or section (sections contain nested arrays).")
        for index, (spec, value) in enumerate(zip(fields, values, strict=True)):
            kind = spec["type"]
            here = (*path, index)
            ok = True
            if kind == "section":
                visit(spec.get("views") or [], value, here)
            elif kind == "switch":
                ok = type(value) is bool
            elif kind in ("slider", "doubleSlider"):
                low, high = limits(spec)
                numbers = value if kind == "doubleSlider" and isinstance(value, list) else [value]
                ok = len(numbers) == (2 if kind == "doubleSlider" else 1) and all(
                    number(x) and low <= x <= high for x in numbers
                )
                if ok and kind == "doubleSlider":
                    ok = numbers[0] <= numbers[1]
            elif kind == "textField":
                ok = isinstance(value, str) and len(value) <= MAX_TEXT
            elif kind == "comboBox":
                ok = isinstance(value, str) and len(value) <= MAX_TEXT
            elif kind in ("menu", "segmented", "image"):
                options = image_sources(spec) if kind == "image" else spec["options"]
                ok = type(value) is int and 0 <= value < len(options)
            elif kind == "size":
                low, high = spec.get("minValue", 64), spec.get("maxValue", 8192)
                ok = (
                    isinstance(value, list)
                    and len(value) == 2
                    and all(type(x) is int and low <= x <= high for x in value)
                )
            elif kind == "imageField":
                ok = isinstance(value, list) and len(value) <= (MAX_IMAGES if spec.get("multiSelect") else 1)
                if ok:
                    for source in value:
                        if input_token(source):
                            if origin is not None:
                                origin(here, kind, source)
                        else:
                            image_bytes(source)
            elif kind == "directory":
                ok = value == "" or input_token(value)
                if ok and value and origin is not None:
                    origin(here, kind, value)
            if not ok:
                raise ValueError(f"Invalid {kind} answer at field {'.'.join(map(str, here))}.")

    visit(components, answers)


def form_key(components: list) -> str:
    return hashlib.sha256(bounded_json(components, MAX_FORM_BYTES).encode("ascii")).hexdigest()


def recallable_answers(components: list, answers: list) -> list:
    """Keep ordinary answers, but require new consent/file selection for every run."""
    result = []
    for spec, value in zip((s for s in components if contributes(s)), answers, strict=True):
        kind = spec["type"]
        if kind == "section":
            value = recallable_answers(spec.get("views") or [], value)
        elif kind == "imageField":
            value = []
        elif kind == "directory":
            value = ""
        result.append(value)
    return result

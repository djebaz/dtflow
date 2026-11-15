"""Error hierarchy of the LoRA converter.

``ConversionError`` keeps the name the old script used, so existing callers still catch it.
``str(error)`` is the plain message followed by the hint, exactly the text the old
single-file converter printed. The UI can show ``user_message`` and ``hint`` separately.
"""

from __future__ import annotations

COLAB_HINT = (
    "Use the Colab notebook instead: tools/colab/dtflow_lora_minimal_convert.ipynb "
    "(it supports every Draw Things family)."
)


class ConversionError(Exception):
    """Raised for anything the converter refuses to do."""

    code = "convert_failed"

    def __init__(self, user_message: str, *, hint: str | None = None) -> None:
        self.user_message = user_message
        self.hint = hint
        super().__init__(user_message if hint is None else f"{user_message} {hint}")


class UnsupportedFamilyError(ConversionError):
    """Unknown family key, or a family that is not ported."""

    code = "unsupported_family"


class FamilyDataError(ConversionError):
    """``data/families.json`` is malformed or disagrees with the registered runners."""

    code = "family_data"


class ExperimentalFamilyError(ConversionError):
    """The family is experimental and ``allow_experimental`` was not set."""

    code = "experimental_family"


class UnsupportedLoraFormatError(ConversionError):
    """LoHa, LoKr, bias, diff, DoRA, unknown keys, duplicate or incomplete modules."""

    code = "unsupported_format"

    def __init__(self, user_message: str, *, hint: str | None = None, alpha_value_count: int | None = None) -> None:
        self.alpha_value_count = alpha_value_count
        super().__init__(user_message, hint=hint)


class UnmatchedModulesError(ConversionError):
    """Modules that have no place in the chosen family (wrong family or model size)."""

    code = "unmatched_modules"

    def __init__(self, user_message: str, *, modules: tuple[str, ...] = (), hint: str | None = None) -> None:
        self.modules = modules
        super().__init__(user_message, hint=hint)


class ShapeMismatchError(ConversionError):
    """Tensor shapes do not fit the family."""

    code = "shape_mismatch"


class SourceReadError(ConversionError):
    """Not a safetensors file, truncated or unreadable."""

    code = "source_unreadable"


class DestinationError(ConversionError):
    """Destination exists, is not writable or cannot be replaced."""

    code = "destination"


class ConversionCancelled(ConversionError):
    """Cancel was requested. Not a failure for the UI; no output is left behind."""

    code = "cancelled"


class DependencyError(ConversionError):
    """A dependency (numpy) is too old."""

    code = "dependency"

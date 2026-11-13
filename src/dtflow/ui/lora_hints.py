"""Read-only LoRA metadata hints from the window's worker-loaded local projections.

Hover never reads files, resolves receipts, uploads or changes a prompt. Each prompt supplies its own selection.
"""

import html
import re
from collections.abc import Callable, Sequence

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QWidget

from dtflow.core.models import LoRASelection
from dtflow.services.lora_convert.source_metadata import detail_model
from dtflow.services.lora_naming import display_label
from dtflow.ui.source_details_view import show_fading_tooltip
from dtflow.ui.theme import load_tokens

MAX_HINT_GROUPS = 8
MAX_HINT_LORAS = 16
PROMPT_TAGS = 8


def _phrase(value: str) -> str:
    return next((part.strip() for part in re.split(r"[,;\n]", value) if part.strip()), "")


class HintLibrary:
    """Window-owned presentation projection, not a receipt/availability or dispatch authority."""

    def __init__(self):
        self.entries: list[dict] = []
        self.records: dict[tuple[str, int], dict] = {}

    def _find(self, identifier: str):
        matches = [
            entry
            for entry in self.entries
            if identifier.casefold()
            in {
                str(entry.get("name", "")).casefold(),
                str(self.records.get((entry.get("sha256"), entry.get("size")), {}).get("managed_identifier", "")).casefold(),
            }
        ]
        identities = {(e.get("sha256"), e.get("size")) for e in matches}
        if len(identities) != 1:
            return None, None  # never guess between content identities or imported names
        identity = next(iter(identities))
        return matches[-1], self.records.get(identity)

    def card(self, selection: LoRASelection, *, detailed: bool = False) -> str:
        tokens = load_tokens()
        escape = html.escape

        def text(value: str, *, strong=False) -> str:
            color = tokens["color.text" if strong else "color.secondary"]
            weight = tokens["type.weightEmphasis" if strong else "type.weightBody"]
            return f'<span style="color:{color};font-weight:{weight}">{escape(value)}</span>'

        entry, saved = self._find(selection.identifier)
        source = (entry or {}).get("source_metadata")
        model = detail_model(source if isinstance(source, dict) else None)
        fields = source.get("fields", {}) if isinstance(source, dict) else {}
        trigger = _phrase(str(saved.get("trigger_words", ""))) if saved is not None else ""
        rows = []
        if trigger:
            rows.append(text("Saved trigger: ") + text(trigger, strong=True))
        elif saved is not None:
            rows.append(text("Saved trigger: none (cleared or not set)"))
        declared = _phrase(str(fields.get("modelspec.trigger_phrase", "")))
        if declared and declared != trigger:
            rows.append(text("File-declared trigger: ") + text(declared, strong=True))
        classes = fields.get("ss_class_tokens")
        if isinstance(classes, str) and classes:
            rows.append(text("Training/class phrase: ") + text(classes, strong=True))
        usage = fields.get("modelspec.usage_hint")
        if isinstance(usage, str) and usage:
            rows.append(text("Usage hint: ") + text(usage, strong=True))
        tags = model.tag_counts
        if tags:
            shown = tags if detailed else tags[:PROMPT_TAGS]
            rows.append(
                text("Training tags (not necessarily triggers): ")
                + text(" · ".join(f"{tag} ({count})" for tag, count in shown), strong=True)
                + (text(f" · showing {len(shown)} of {len(tags)} retained tags") if len(shown) < len(tags) else "")
            )
        if not rows:
            rows.append(text("No locally retained trigger/training metadata for this LoRA."))
        body = "<br>".join(rows)
        if detailed and model:
            secondary = [
                text(label + ": ") + text(value, strong=True)
                for label, value in model.facts
                if label not in {"Trigger phrase", "Class tokens", "Usage hint"}
            ]
            if model.note:
                secondary.append(text(model.note))
            body += "<br><br>" + "<br>".join(secondary) if secondary else ""
        heading = (
            text(display_label(selection.identifier), strong=True)
            + text(f"  ·  weight {selection.weight:g}")
        )
        return (
            f'<table width="100%" cellspacing="0" cellpadding="6" style="border:1px solid {tokens["color.border"]}">'
            f'<tr><td bgcolor="{tokens["color.sectionHeader"]}">{heading}</td></tr>'
            f'<tr><td>{body}</td></tr></table>'
        )

    def tooltip(self, groups, *, detailed=False) -> str:
        tokens = load_tokens()
        sections = []
        for title, selected in groups[:MAX_HINT_GROUPS]:
            selected = tuple(selected)
            if not selected:
                continue
            cards = [self.card(item, detailed=detailed) for item in selected[:MAX_HINT_LORAS]]
            if len(selected) > MAX_HINT_LORAS:
                cards.append(html.escape(f"{len(selected) - MAX_HINT_LORAS} more LoRAs not shown."))
            sections.append(
                f'<p style="font-weight:{tokens["type.weightEmphasis"]};color:{tokens["color.text"]}">'
                + html.escape(title)
                + "</p>"
                + "<br>".join(cards)
            )
        if len(groups) > MAX_HINT_GROUPS:
            sections.append(html.escape(f"Showing the first {MAX_HINT_GROUPS} of {len(groups)} LoRA sets."))
        if not sections:
            return ""
        return (
            f'<div style="font-size:{tokens["type.small"]};white-space:pre-wrap">'
            + "<br>".join(sections)
            + "</div>"
        )


def library(widget: QWidget) -> HintLibrary | None:
    return getattr(widget.window(), "_lora_hint_library", None)


class LoRAHover(QObject):
    """Fetch current selection at hover time; parent filters and viewport filters die with the input/row."""

    def __init__(self, widget: QWidget, groups: Callable, *, detailed=False, descendants=False):
        super().__init__(widget)
        self._widget, self._groups, self._detailed = widget, groups, detailed
        watched = [widget]
        if descendants:
            watched.extend(widget.findChildren(QWidget))
        elif hasattr(widget, "viewport"):
            watched.append(widget.viewport())
        for item in watched:
            item.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.ToolTip:
            # Keep existing per-control hints (e.g. family, weight) available in the LoRA selector.
            if watched is not self._widget and isinstance(watched, QWidget) and watched.toolTip():
                return False
            source = library(self._widget)
            if source is not None:
                tip = source.tooltip(self._groups(), detailed=self._detailed)
                if tip:
                    show_fading_tooltip(event.globalPos(), tip, self._widget)
                    event.accept()
                    return True
        return False


def bind_prompt_hint(widget: QWidget, selected: Callable[[], Sequence[LoRASelection]], *, title="Applied LoRAs"):
    return LoRAHover(widget, lambda: [(title, tuple(selected()))])

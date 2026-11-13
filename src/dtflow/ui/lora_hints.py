"""Read-only LoRA hints from the window-owned local metadata projection.

Prompt panels and picker hover cards only display saved data; neither reads providers nor changes prompts.
"""

import html
import re
from pathlib import Path
from collections.abc import Callable, Sequence

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFrame, QLabel, QTextBrowser, QToolButton, QToolTip, QVBoxLayout, QWidget
from shiboken6 import isValid

from dtflow.core.models import LoRASelection
from dtflow.services.lora_convert.source_metadata import detail_model
from dtflow.services.lora_naming import display_label
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
                str(
                    self.records.get((entry.get("sha256"), entry.get("size")), {}).get("managed_identifier", "")
                ).casefold(),
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
            body += "<br><br>" + "<br>".join(secondary) if secondary else ""
        heading = text(display_label(selection.identifier), strong=True) + text(f"  ·  weight {selection.weight:g}")
        return (
            f'<table width="100%" cellspacing="0" cellpadding="6" style="border:1px solid {tokens["color.border"]}">'
            f'<tr><td bgcolor="{tokens["color.sectionHeader"]}">{heading}</td></tr>'
            f"<tr><td>{body}</td></tr></table>"
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
        return f'<div style="font-size:{tokens["type.small"]};white-space:pre-wrap">' + "<br>".join(sections) + "</div>"


def library(widget: QWidget) -> HintLibrary | None:
    return getattr(widget.window(), "_lora_hint_library", None)


class PromptHintOverlay(QObject):
    """One icon-button-controlled, scrollable LoRA hint panel above an existing inspector or canvas.

    The host owns the geometry. On Generate/Queues it is the right inspector scroll viewport,
    which sits below the persistent Images navigation button; in Workflows it is the canvas host.
    """

    def __init__(self, host: QWidget, *, on_toggle: Callable[[], None] | None = None):
        super().__init__(host)
        self.host = host
        self._button: QToolButton | None = None
        self._selected: Callable[[], Sequence[LoRASelection]] | None = None
        self._title = ""
        self._on_toggle = on_toggle
        self.frame = QFrame(host)
        self.frame.setObjectName("promptHintsOverlay")
        self.frame.setProperty("role", "sectionCard")
        self.frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        column = QVBoxLayout(self.frame)
        column.setContentsMargins(12, 12, 12, 12)
        column.setSpacing(8)
        heading = QLabel("LoRA prompt hints", self.frame)
        heading.setProperty("textRole", "section")
        column.addWidget(heading)
        self.content = QTextBrowser(self.frame)
        self.content.setObjectName("promptHintsContent")
        self.content.setOpenExternalLinks(False)
        self.content.setOpenLinks(False)
        self.content.setFrameShape(QFrame.Shape.NoFrame)
        column.addWidget(self.content, 1)
        self.frame.hide()
        host.installEventFilter(self)

    @property
    def open(self) -> bool:
        return self._button is not None

    def bind(
        self, button: QToolButton, selected: Callable[[], Sequence[LoRASelection]], *, title: str = "Applied LoRAs"
    ) -> None:
        """Attach a fresh or persistent icon control; switching controls closes the old one."""
        button.setText("")
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.setFixedSize(30, 30)
        button.setIcon(QIcon(str(Path(__file__).parents[1] / "resources" / "images" / "gallery" / "info.svg")))
        button.setIconSize(QSize(16, 16))
        button.setToolTip("Show LoRA prompt hints")
        button.setAccessibleName("Show LoRA prompt hints")

        def toggled(checked: bool) -> None:
            if not checked:
                if self._button is button:
                    self.close()
                return
            self.close()
            self._button, self._selected, self._title = button, selected, title
            button.setToolTip("Hide LoRA prompt hints")
            button.setAccessibleName("Hide LoRA prompt hints")
            self.refresh()
            self.frame.setGeometry(self.host.rect())
            self.frame.show()
            self.frame.raise_()
            if self._on_toggle:
                self._on_toggle()

        button.toggled.connect(toggled)

    def refresh(self) -> None:
        """Reproject current LoRA selections and locally retained metadata while the panel is visible."""
        if self._selected is None:
            return
        selected = tuple(self._selected())
        source = library(self.host)
        bar = self.content.verticalScrollBar()
        scroll = bar.value()
        self._set_content(selected, source)
        bar.setValue(min(scroll, bar.maximum()))

    def _set_content(self, selected, source) -> None:
        if not selected:
            self.content.setHtml("<p>No LoRAs selected for this prompt.</p>")
        elif source is None:
            self.content.setHtml("<p>Local LoRA metadata is unavailable.</p>")
        else:
            self.content.setHtml(source.tooltip([(self._title, selected)], detailed=True))

    def close(self) -> None:
        button = self._button
        self._button = None
        self._selected = None
        self.frame.hide()
        if button is not None and isValid(button):
            button.setChecked(False)
            button.setToolTip("Show LoRA prompt hints")
            button.setAccessibleName("Show LoRA prompt hints")
        if button is not None and self._on_toggle:
            self._on_toggle()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        if watched is self.host and event.type() in {QEvent.Type.Resize, QEvent.Type.Show}:
            self.frame.setGeometry(self.host.rect())
        return False


class LoRAHover(QObject):
    """Fetch current selection at hover time; parent filters and viewport filters die with the input/row."""

    def __init__(
        self, widget: QWidget, groups: Callable, *, detailed=False, descendants=False, lead: Callable | None = None
    ):
        super().__init__(widget)
        self._widget, self._groups, self._detailed, self._lead = widget, groups, detailed, lead
        watched = [widget]
        if descendants:
            watched.extend(widget.findChildren(QWidget))
        elif hasattr(widget, "viewport"):
            watched.append(widget.viewport())
        for item in watched:
            item.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.ToolTip:
            # Keep any other per-control hint (e.g. weight) available in the LoRA selector.
            if watched is not self._widget and isinstance(watched, QWidget) and watched.toolTip():
                return False
            source = library(self._widget)
            if source is not None:
                tip = source.tooltip(self._groups(), detailed=self._detailed)
                lead = self._lead() if self._lead is not None else ""
                if lead:
                    tip = f"<p style='white-space:pre-wrap;margin:0 0 6px 0'>{html.escape(lead)}</p>" + tip
                if tip:
                    QToolTip.showText(event.globalPos(), tip, self._widget)
                    event.accept()
                    return True
        return False

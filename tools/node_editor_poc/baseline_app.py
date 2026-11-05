"""Essentials-only baseline for the node-editor POC (#223): same shell, fixture and packaging, no canvas library.

Purpose: the size and start-up reference for the candidate EXEs. It draws the fixture graph with plain
``QGraphicsScene`` items (selectable, movable, linked to the same inspector) and saves/reopens through the same
ComfyUI-format adapter. It is not an editor: no connecting, adding, deleting or undo/redo.
"""

from __future__ import annotations

import sys

import poc_shell as shell
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView

import poc_contract as contract

PORT_COLORS = {contract.IMAGE: "color.focus", contract.MASK: "color.warning"}


class NodeItem(QGraphicsRectItem):
    def __init__(self, window: BaselineWindow, node: contract.GraphNode):
        super().__init__(0, 0, *node.size)
        self.window, self.node = window, node
        self.spec = contract.CATALOG[node.type]
        self.setPos(*node.pos)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

    def port(self, slot: int, is_input: bool) -> QPointF:
        x = 0 if is_input else self.rect().width()
        return self.scenePos() + QPointF(x, 42 + 22 * slot)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.window.update_links()
        return super().itemChange(change, value)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        rect = self.rect()
        radius = float(shell.TOKENS["size.panelRadius"].removesuffix("px"))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(shell.token_color("color.surface"))
        painter.setPen(QPen(shell.token_color("color.focus" if self.isSelected() else "color.borderStrong"), 1.5))
        painter.drawRoundedRect(rect, radius, radius)
        painter.fillRect(QRectF(1, 1, rect.width() - 2, 27), shell.token_color("color.sectionHeader"))
        painter.setPen(shell.token_color("color.title"))
        painter.setFont(shell.token_font("type.body", "type.weightEmphasis"))
        painter.drawText(QRectF(0, 0, rect.width(), 28), Qt.AlignmentFlag.AlignCenter, self.spec.title)
        for is_input, ports in ((True, self.spec.inputs), (False, self.spec.outputs)):
            for slot, port in enumerate(ports):
                center = QPointF(0 if is_input else rect.width(), 42 + 22 * slot)
                painter.setBrush(shell.token_color(PORT_COLORS[port.type]))
                painter.setPen(QPen(shell.token_color("color.canvas"), 1))
                painter.drawEllipse(center, 6, 6)
        ports = max(len(self.spec.inputs), len(self.spec.outputs), 1)
        painter.setPen(shell.token_color("color.secondary"))
        painter.setFont(shell.token_font("type.small"))
        text = QRectF(10, 28 + 22 * ports, rect.width() - 20, 60)
        painter.drawText(text, Qt.TextFlag.TextWordWrap, contract.summary(self.node.type, self.node.params))
        thumbnail = self.window.thumbnail_for(self.node.type)
        if thumbnail is not None:
            painter.drawPixmap(
                QRectF(rect.width() / 2 - 44, text.bottom() + 4, 88, 88), thumbnail, QRectF(thumbnail.rect())
            )


class BaselineWindow(shell.PocWindow):
    CANDIDATE = "baseline (pyside6-essentials only)"
    SUPPORTS_EDITING = False

    def build_canvas(self):
        self.gscene = QGraphicsScene()
        self.gscene.setBackgroundBrush(QBrush(shell.token_color("color.canvas")))
        self.gscene.selectionChanged.connect(self._selection_changed)
        self.view = QGraphicsView(self.gscene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.items: dict[int, NodeItem] = {}
        self.link_items: list[tuple[contract.GraphLink, QGraphicsPathItem]] = []
        return self.view

    def canvas_focus_widget(self):
        return self.view

    def load_graph(self, graph: contract.Graph) -> None:
        self.gscene.clear()
        self.graph = graph
        self.items = {node.id: NodeItem(self, node) for node in graph.nodes}
        for item in self.items.values():
            self.gscene.addItem(item)
        self.link_items = []
        for link in graph.links:
            path = QGraphicsPathItem()
            path.setPen(QPen(shell.token_color(PORT_COLORS[link.type]), 2))
            path.setZValue(-1)
            self.gscene.addItem(path)
            self.link_items.append((link, path))
        self.update_links()
        QTimer.singleShot(0, self.fit_view)

    def update_links(self) -> None:
        for link, path_item in self.link_items:
            start = self.items[link.origin_id].port(link.origin_slot, is_input=False)
            end = self.items[link.target_id].port(link.target_slot, is_input=True)
            path = QPainterPath(start)
            dx = max(60.0, abs(end.x() - start.x()) / 2)
            path.cubicTo(start + QPointF(dx, 0), end - QPointF(dx, 0), end)
            path_item.setPath(path)

    def current_graph(self) -> contract.Graph:
        for item in self.items.values():
            item.node.pos = (item.pos().x(), item.pos().y())
        return self.graph

    def set_node_param(self, node_id: int, name: str, value: object) -> None:
        self.items[node_id].node.params[name] = value
        self.items[node_id].update()

    def add_node(self, node_type: str) -> None:
        self.show_status("The baseline does not edit graphs.", error=True)

    def fit_view(self) -> None:
        if self.items:
            rect = self.gscene.itemsBoundingRect().adjusted(-60, -60, 60, 60)
            self.view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def _selection_changed(self) -> None:
        selected = [item for item in self.gscene.selectedItems() if isinstance(item, NodeItem)]
        if len(selected) == 1:
            node = selected[0].node
            self.inspector.show_node(node.id, node.type, node.params, self.thumbnail_for(node.type))
        else:
            self.inspector.clear("Select one node on the canvas." if not selected else f"{len(selected)} selected.")


if __name__ == "__main__":
    sys.exit(shell.run(BaselineWindow))

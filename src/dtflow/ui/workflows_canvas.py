"""Workflows node canvas (#223, audit 10.8 step 6): the accepted nodeeditor-qtpy POC on ``dtflow.core.workflows``.

The library owns the scene, nodes, sockets, edges, selection and undo/redo; a small view subclass gives plain-wheel
zoom and empty-background left-drag panning (Shift-drag keeps rectangle selection). DTFlow owns the node catalogue,
typed IMAGE/MASK ports, the cycle rule, parameters, theme tokens and run-status presentation. The library's own JSON
session format is only its internal undo snapshot: files always go through ``load_workflow``/``dump_workflow``.
"""

from __future__ import annotations

import os
import re
from collections import OrderedDict
from collections.abc import Callable
from functools import cache

os.environ.setdefault("QT_API", "pyside6")  # qtpy: the only binding DTFlow ships

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsSimpleTextItem, QGraphicsView, QLabel, QSizePolicy, QVBoxLayout, QWidget

from nodeeditor.node_content_widget import QDMNodeContentWidget
from nodeeditor.node_edge import EDGE_TYPE_BEZIER, Edge
from nodeeditor.node_edge_dragging import EdgeDragging
from nodeeditor.node_editor_widget import NodeEditorWidget
from nodeeditor.node_graphics_node import QDMGraphicsNode
from nodeeditor.node_graphics_socket import QDMGraphicsSocket
from nodeeditor.node_graphics_view import MODE_NOOP, QDMGraphicsView
from nodeeditor.node_node import Node
from nodeeditor.node_scene import Scene
from nodeeditor.node_scene_history import SceneHistory
from nodeeditor.node_socket import LEFT_TOP, RIGHT_TOP

from dtflow.core import workflows as wf
from dtflow.ui.theme import load_tokens

SOCKET_TYPES = {wf.IMAGE: 0, wf.MASK: 1}
SOCKET_NAMES = {value: key for key, value in SOCKET_TYPES.items()}
NODE_WIDTH = 240
THUMB_SIZE = 96
# Fit never zooms out below this scale, so 10 pt node text stays about 10 px or larger; the wheel still zooms further.
MIN_FIT_SCALE = 0.75
# Step state -> token for the node outline and status pill (DESIGN.md: amber warns, red fails, never colour alone).
STATE_TOKENS = {
    "running": "color.focus",
    "paused": "color.warning",
    "failed": "color.error",
    "unknown": "color.unknown",
    "skipped": "color.disabled",
    "stopped": "color.disabled",
    "cancelled": "color.disabled",
    "succeeded": "color.success",
    "pending": "color.borderStrong",
}


@cache
def _tokens() -> dict[str, str]:
    return load_tokens()


def token_color(name: str) -> QColor:
    value = _tokens()[name]
    match = re.fullmatch(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)", value)
    return QColor(*map(int, match.groups())) if match else QColor(value)


def token_font(size_token: str = "type.body", weight_token: str = "type.weightBody") -> QFont:
    tokens = _tokens()
    font = QFont(tokens["type.family"])
    font.setPointSizeF(float(tokens[size_token].removesuffix("pt")))
    font.setWeight(QFont.Weight(int(tokens[weight_token])))
    return font


class DTFHistory(SceneHistory):
    def storeHistory(self, desc, setModified=False, *args, **kwargs):
        if self.is_restoring_history:
            return False
        # Keep selection callbacks, but do not make selection an undoable edit.
        if not setModified and desc in {"Selection Changed", "Deselected Everything"}:
            if self.history_stack:
                self.history_stack[self.history_current_step]["merge_key"] = None
            return False
        return super().storeHistory(desc, setModified, *args, **kwargs)


class DTFEdge(Edge):
    def __init__(self, scene, start_socket=None, end_socket=None, *args, comfy_id=0, **kwargs):
        self.comfy_id = comfy_id
        super().__init__(scene, start_socket, end_socket, *args, **kwargs)
        if start_socket is not None and end_socket is not None and not self.comfy_id:
            self.comfy_id = scene.next_link_id()

    def serialize(self):
        data = super().serialize()
        data["comfy_id"] = self.comfy_id
        return data

    def deserialize(self, data, hashmap=None, restore_id=True, *args, **kwargs):
        result = super().deserialize(data, {} if hashmap is None else hashmap, restore_id, *args, **kwargs)
        if result:
            self.comfy_id = data["comfy_id"] if restore_id else self.scene.next_link_id()
            self.scene.last_link_id = max(self.scene.last_link_id, self.comfy_id)
        return result


class DTFScene(Scene):
    historyClass = DTFHistory

    def __init__(self):
        self.last_link_id = 0
        self.canvas: WorkflowCanvas | None = None
        super().__init__()

    def next_link_id(self):
        self.last_link_id += 1
        return self.last_link_id

    def getEdgeClass(self):
        return DTFEdge


class DTFEdgeDragging(EdgeDragging):
    def edgeDragEnd(self, item):
        # Validate before the library's rejection print/keep-dragging path; a prior valid input link stays.
        if isinstance(item, QDMGraphicsSocket) and self.drag_edge is not None and self.drag_start_socket is not None:
            if not self.drag_edge.validateEdge(self.drag_start_socket, item.socket):
                self.cancelDragEdge()
                return True  # consume the gesture; do not start another edge
        return super().edgeDragEnd(item)


class DTFGraphicsView(QDMGraphicsView):
    """Plain-wheel zoom and empty-background left-drag navigation."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dragging = DTFEdgeDragging(self)
        self._panning = False
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta:
            # Use the real scale: fitInView does not update the library's zoom counter.
            current = abs(self.transform().m11()) or 1.0
            target = max(0.05, min(3.0, current * 1.2 ** (max(-1200, min(1200, delta)) / 120)))
            self.scale(target / current, target / current)
        event.accept()

    def leftMouseButtonPress(self, event) -> None:
        if (
            self.mode == MODE_NOOP
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and self.itemAt(event.pos()) is None
        ):
            self._panning = True
            self._previous_drag_mode = self.dragMode()
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            QGraphicsView.mousePressEvent(self, event)
            return
        super().leftMouseButtonPress(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning:
            QGraphicsView.mouseMoveEvent(self, event)
            return
        super().mouseMoveEvent(event)

    def leftMouseButtonRelease(self, event) -> None:
        if self._panning:
            QGraphicsView.mouseReleaseEvent(self, event)
            self.setDragMode(self._previous_drag_mode)
            self._panning = False
            self.grScene.scene.onItemsDeselected()
            return
        super().leftMouseButtonRelease(event)

    def _request_add(self, view_pos: QPoint) -> None:
        canvas = self.grScene.scene.canvas
        if canvas is not None:
            canvas.on_add_request(self.mapToScene(view_pos), view_pos)

    def mouseDoubleClickEvent(self, event) -> None:
        # Double-click on empty canvas: the searchable add-node picker at that point.
        if event.button() == Qt.MouseButton.LeftButton and self.itemAt(event.pos()) is None:
            self._request_add(event.pos())
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def event(self, event) -> bool:
        # Tab would move keyboard focus away; on the canvas it opens the add-node picker (at the pointer if inside).
        if (
            event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Tab
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
        ):
            pointer = self.viewport().mapFromGlobal(self.cursor().pos())
            inside = self.viewport().rect().contains(pointer)
            self._request_add(pointer if inside else self.viewport().rect().center())
            return True
        return super().event(event)

    def mousePressEvent(self, event) -> None:
        canvas = self.grScene.scene.canvas
        if canvas is not None:
            canvas.on_pressed()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_F and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            canvas = self.grScene.scene.canvas
            if canvas is not None:
                canvas.fit_view()
                event.accept()
                return
        super().keyPressEvent(event)


class DTFEditorWidget(NodeEditorWidget):
    Scene_class = DTFScene
    GraphicsView_class = DTFGraphicsView


class DTFGraphicsNode(QDMGraphicsNode):
    """Library node item with geometry and colours from DTFlow tokens; the outline follows the run state."""

    run_state: str | None = None

    def paint(self, painter, option, widget=None) -> None:
        # Upstream paints hover before checking selection. Use its selected path
        # for selected+hovered nodes, without changing hover event handling.
        hovered = self.hovered
        selected_pen, default_pen = self._pen_selected, self._pen_default
        try:
            if self.run_state in STATE_TOKENS and self.run_state != "pending":
                self._pen_default = QPen(token_color(STATE_TOKENS[self.run_state]), 2.0)
            if self.isSelected():
                if hovered or self._was_moved:
                    self._pen_selected = self._pen_selected_active
                self.hovered = False
            super().paint(painter, option, widget)
        finally:
            self.hovered = hovered
            self._pen_selected, self._pen_default = selected_pen, default_pen

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self.update()  # repaint after upstream clears its movement flag

    def initSizes(self) -> None:
        super().initSizes()
        spec = self.node.SPEC
        ports = max(len(spec.inputs), len(spec.outputs), 1)
        self.width = NODE_WIDTH
        self.title_height = 28
        self.edge_roundness = float(_tokens()["size.panelRadius"].removesuffix("px"))
        self.height = self.title_height + 22 * ports + 88 + (THUMB_SIZE + 4 if spec.thumbnail else 0)

    def initAssets(self) -> None:
        super().initAssets()
        self._title_color = token_color("color.title")
        self._title_font = token_font("type.body", "type.weightEmphasis")
        self._color = token_color("color.borderStrong")
        self._color_selected = token_color("color.focus")
        self._color_hovered = token_color("color.inputBorderHover")
        self._pen_default = QPen(self._color, 1.5)
        self._pen_selected = QPen(self._color_selected, 2.0)
        self._pen_selected_active = QPen(self._color_selected.lighter(110), 2.0)
        self._pen_hovered = QPen(self._color_hovered, 2.5)
        self._brush_title = QBrush(token_color("color.sectionHeader"))
        self._brush_background = QBrush(token_color("color.surface"))


class DTFNodeContent(QDMNodeContentWidget):
    """Embedded content: the node's settings summary, its run status pill and (image nodes) the latest output."""

    def initUI(self) -> None:
        self.setObjectName("dtfNodeContent")
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 22 * max(len(self.node.SPEC.inputs), len(self.node.SPEC.outputs), 1) - 8, 0, 0)
        box.setSpacing(4)
        self.summary = QLabel()
        self.summary.setObjectName("dtfNodeSummary")
        self.summary.setWordWrap(True)
        self.summary.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        box.addWidget(self.summary)
        self.status = QLabel()
        self.status.setObjectName("dtfNodeStatus")
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.status.hide()
        box.addWidget(self.status, 0, Qt.AlignmentFlag.AlignLeft)
        self.thumb = None
        if self.node.SPEC.thumbnail:
            self.thumb = QLabel()
            self.thumb.setObjectName("dtfNodeThumb")
            self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.thumb.setFixedHeight(THUMB_SIZE)
            self.thumb.hide()
            box.addWidget(self.thumb)
        box.addStretch(1)

    def refresh(self) -> None:
        self.summary.setText(wf.summary(self.node.SPEC.type, self.node.params))

    def set_status(self, state: str | None, text: str = "", tooltip: str = "") -> None:
        self.status.setVisible(bool(state))
        self.status.setText(text)
        self.status.setToolTip(tooltip)
        self.status.setProperty("workflowState", state or "")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def set_thumbnail(self, pixmap: QPixmap | None) -> None:
        if self.thumb is None:
            return
        if pixmap is None or pixmap.isNull():
            self.thumb.clear()
            self.thumb.hide()
            return
        self.thumb.setPixmap(
            pixmap.scaled(
                THUMB_SIZE * 2,
                THUMB_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.thumb.show()

    # Content state rides in the library's undo snapshots; it is never the saved file format.
    def serialize(self) -> OrderedDict:
        return OrderedDict([("comfy_id", self.node.comfy_id), ("params", wf.copy_params(self.node.params))])

    def deserialize(self, data: dict, hashmap: dict | None = None, restore_id: bool = True) -> bool:
        self.node.comfy_id = data["comfy_id"]
        self.node.params = wf.copy_params(data["params"])
        self.node.grNode.title = f"{self.node.SPEC.title} #{self.node.comfy_id}"
        self.refresh()
        return True


class DTFNode(Node):
    GraphicsNode_class = DTFGraphicsNode
    NodeContent_class = DTFNodeContent
    SPEC: wf.NodeSpec

    def __init__(self, scene, comfy_id: int = 0):
        self.comfy_id = comfy_id
        self.params = self.SPEC.defaults()
        super().__init__(
            scene,
            f"{self.SPEC.title} #{comfy_id}",
            inputs=[SOCKET_TYPES[p.type] for p in self.SPEC.inputs],
            outputs=[SOCKET_TYPES[p.type] for p in self.SPEC.outputs],
            input_text=[p.name for p in self.SPEC.inputs],
            output_text=[p.name for p in self.SPEC.outputs],
        )
        self.content.refresh()
        # Upstream draws socket text clipped inside the tiny connector. Put readable
        # labels beside it instead, parented to the socket so they follow its geometry/LOD.
        self.port_labels = []
        for socket, port in zip(self.inputs + self.outputs, self.SPEC.inputs + self.SPEC.outputs, strict=True):
            socket.grSocket.setText("")
            label = QGraphicsSimpleTextItem(port.name, socket.grSocket)
            label.setFont(token_font("type.body"))
            label.setBrush(QBrush(token_color("color.text")))
            label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            width, height = label.boundingRect().width(), label.boundingRect().height()
            label.setPos(12 if socket.is_input else -12 - width, -height / 2)
            label.setToolTip(f"{port.name}: {port.type}")
            socket.grSocket.setToolTip(f"{port.name}: {port.type}")
            self.port_labels.append(label)

    def initSettings(self) -> None:
        super().initSettings()
        self.input_socket_position = LEFT_TOP
        self.output_socket_position = RIGHT_TOP
        self.input_multi_edged = False  # one link per input; dropping a new one replaces it
        self.output_multi_edged = True  # IMAGE fans out (Generate feeds detector and inpaint)
        self.input_socket_shapes = {SOCKET_TYPES[wf.MASK]: "square"}
        self.output_socket_shapes = {SOCKET_TYPES[wf.MASK]: "square"}

    def serialize(self) -> OrderedDict:
        data = super().serialize()
        data["dtf_type"] = self.SPEC.type
        return data


NODE_CLASSES: dict[str, type[DTFNode]] = {
    spec.type: type(spec.type + "Node", (DTFNode,), {"SPEC": spec}) for spec in wf.CATALOG.values()
}


def _edge_validator(start, end) -> bool:
    """Typed-port rule (IMAGE->IMAGE, MASK->MASK), output->input only, no self-links, no cycles."""
    scene = start.node.scene
    canvas = getattr(scene, "canvas", None)
    if not isinstance(canvas, WorkflowCanvas):
        return True  # not a Workflows scene

    def reject(message: str) -> bool:
        canvas.on_rejected(f"Connection refused: {message}")
        return False

    if start.is_output == end.is_output:
        return reject("connect an output to an input.")
    out, inp = (start, end) if start.is_output else (end, start)
    if out.node is inp.node:
        return reject("a node cannot feed itself.")
    if out.socket_type != inp.socket_type:
        out_name, in_name = SOCKET_NAMES[out.socket_type], SOCKET_NAMES[inp.socket_type]
        return reject(f"{out_name} output cannot feed {in_name} input '{inp.node.SPEC.inputs[inp.index].name}'.")
    if wf.would_cycle(canvas.current_graph(), out.node.comfy_id, inp.node.comfy_id):
        return reject("that link would create a cycle; workflows must be acyclic.")
    return True


Edge.registerEdgeValidator(_edge_validator)


class WorkflowCanvas(QObject):
    """One editor widget plus the Graph <-> scene adapter and run-status presentation."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.editor = DTFEditorWidget(parent)
        self.editor.setObjectName("workflowCanvas")
        self.scene: DTFScene = self.editor.scene
        self.scene.canvas = self
        self.view: DTFGraphicsView = self.editor.view
        self.view.setAccessibleName("Workflow canvas")
        self.workflow_id = ""
        self.view_state = None
        self.frontend_version = None
        self._loading = False
        self.on_selection: Callable[[int | None, int], None] = lambda node_id, count: None
        self.on_changed: Callable[[], None] = lambda: None
        self.on_rejected: Callable[[str], None] = lambda message: None
        self.on_restored: Callable[[], None] = lambda: None  # undo/redo replaced node objects and values
        # Double-click on empty canvas or Tab: (scene point, view point) for the add-node picker.
        self.on_add_request: Callable[[QPointF, QPoint], None] = lambda scene_pos, view_pos: None
        self.on_pressed: Callable[[], None] = lambda: None
        self.scene.setNodeClassSelector(lambda data: NODE_CLASSES[data["dtf_type"]])
        self._apply_canvas_theme()
        self.scene.addItemSelectedListener(self._selection_changed)
        self.scene.addItemsDeselectedListener(self._selection_changed)
        self.scene.history.addHistoryRestoredListener(self._history_restored)
        self.scene.history.addHistoryStoredListener(self._history_stored)

    @property
    def widget(self) -> QWidget:
        return self.editor

    def _apply_canvas_theme(self) -> None:
        image, mask = token_color("color.focus"), token_color("color.warning")
        self.scene.setSocketColors(
            outline=token_color("color.canvas"),
            highlight=token_color("color.selectionText"),
            type_colors=[image, mask],
        )
        self.scene.setEdgeColors(
            default=token_color("color.secondary"),
            selected=token_color("color.onAccent"),
            hovered=token_color("color.accentHover"),
            dragging=token_color("color.glyphNeutral"),
        )
        gr = self.scene.grScene
        gr.setBackgroundColor(token_color("color.canvas"))
        # setGridColorLight() prints a debug line in 0.8.2, so set the pens directly.
        gr._color_light, gr._color_dark = token_color("color.itemSurface"), token_color("color.panelEdge")
        gr._color_light.setAlphaF(0.25)
        gr._color_dark.setAlphaF(0.30)
        gr._pen_light.setColor(gr._color_light)
        gr._pen_dark.setColor(gr._color_dark)
        gr._pen_dark.setWidth(1)
        gr._pen_state.setColor(token_color("color.secondary"))
        gr._font_state = token_font("type.title", "type.weightTitle")

    # --- graph <-> scene ------------------------------------------------------------------------------------------
    def load_graph(self, graph: wf.Graph, *, fit: bool = True) -> None:
        self._loading = True
        try:
            self.scene.clear()
            self.view_state = graph.view
            self.workflow_id = graph.workflow_id
            self.frontend_version = graph.frontend_version
            self.scene.last_link_id = max(graph.last_link_id, max((link.id for link in graph.links), default=0))
            nodes = {}
            for item in graph.nodes:
                node = NODE_CLASSES[item.type](self.scene, item.id)
                node.params = wf.copy_params(item.params)
                node.setPos(*item.pos)
                node.content.refresh()
                nodes[item.id] = node
            for link in graph.links:
                DTFEdge(
                    self.scene,
                    nodes[link.origin_id].outputs[link.origin_slot],
                    nodes[link.target_id].inputs[link.target_slot],
                    edge_type=EDGE_TYPE_BEZIER,
                    comfy_id=link.id,
                )
            self._recolor_edges()
            self.scene.history.clear()
            self.scene.history.storeInitialHistoryStamp()
            self.scene.has_been_modified = False
        finally:
            self._loading = False
        self._selection_changed()
        if fit:
            QTimer.singleShot(0, self.fit_view)  # after the view has its real size

    def current_graph(self) -> wf.Graph:
        graph = wf.Graph(
            workflow_id=self.workflow_id,
            view=self.view_state,
            last_link_id=self.scene.last_link_id,
            frontend_version=self.frontend_version,
        )
        for node in self.scene.nodes:
            pos = node.grNode.scenePos()
            graph.nodes.append(
                wf.GraphNode(
                    node.comfy_id,
                    node.SPEC.type,
                    (pos.x(), pos.y()),
                    wf.copy_params(node.params),
                    (float(node.grNode.width), float(node.grNode.height)),
                )
            )
        for edge in self.scene.edges:
            out, inp = edge.start_socket, edge.end_socket
            if out is None or inp is None:
                continue
            if out.is_input:
                out, inp = inp, out
            graph.links.append(
                wf.GraphLink(
                    edge.comfy_id,
                    out.node.comfy_id,
                    out.index,
                    inp.node.comfy_id,
                    inp.index,
                    SOCKET_NAMES[out.socket_type],
                )
            )
        return graph

    def node(self, comfy_id: int) -> DTFNode | None:
        return next((node for node in self.scene.nodes if node.comfy_id == comfy_id), None)

    def node_params(self, comfy_id: int) -> dict | None:
        node = self.node(comfy_id)
        return None if node is None else node.params

    @property
    def modified(self) -> bool:
        return bool(self.scene.has_been_modified)

    def mark_saved(self) -> None:
        self.scene.has_been_modified = False

    # --- editing --------------------------------------------------------------------------------------------------
    def set_node_param(self, node_id: int, name: str, value: object) -> None:
        node = self.node(node_id)
        if node is None:
            return
        node.params[name] = value
        node.content.refresh()
        # Typing bursts in one field coalesce into one undo step.
        self.scene.history.storeHistory(f"Edit {name}", setModified=True, merge_key=f"param:{node_id}:{name}")

    def add_node(self, node_type: str, at: QPointF | None = None) -> int:
        """Add a node centred on the view, or with its top-left corner at the scene point ``at``."""
        next_id = max((node.comfy_id for node in self.scene.nodes), default=0) + 1
        node = NODE_CLASSES[node_type](self.scene, next_id)
        if at is not None:
            node.setPos(at.x(), at.y())
        else:
            center = self.view.mapToScene(self.view.viewport().rect().center())
            node.setPos(center.x() - NODE_WIDTH / 2, center.y() - 80)
        self.scene.history.storeHistory(f"Add {node.SPEC.title}", setModified=True)
        self.select_node(next_id)
        return next_id

    def select_node(self, node_id: int | None) -> None:
        self.scene.grScene.clearSelection()
        node = self.node(node_id) if node_id is not None else None
        if node is not None:
            node.grNode.setSelected(True)
        self._selection_changed()

    def selected_node_id(self) -> int | None:
        nodes = self.scene.getSelectedNodes()
        return nodes[0].comfy_id if len(nodes) == 1 else None

    def undo(self) -> None:
        self.scene.undo()

    def redo(self) -> None:
        self.scene.redo()

    def delete_selected(self) -> None:
        self.view.deleteSelected()
        self._selection_changed()

    def fit_view(self) -> None:
        """Fit the workflow, but never below ``MIN_FIT_SCALE``: a larger workflow then shows from its top-left."""
        if not self.scene.nodes:
            return
        rect = self.scene.grScene.itemsBoundingRect().adjusted(-60, -60, 60, 60)
        self.view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        if abs(self.view.transform().m11()) >= MIN_FIT_SCALE:
            return
        self.view.resetTransform()
        self.view.scale(MIN_FIT_SCALE, MIN_FIT_SCALE)
        viewport = self.view.viewport().rect()
        half_width, half_height = viewport.width() / MIN_FIT_SCALE / 2, viewport.height() / MIN_FIT_SCALE / 2
        self.view.centerOn(
            min(rect.center().x(), rect.left() + half_width), min(rect.center().y(), rect.top() + half_height)
        )

    def center_on(self, node_id: int, *, reset_zoom: bool = False) -> None:
        node = self.node(node_id)
        if node is None:
            return
        if reset_zoom:
            self.view.resetTransform()
        self.view.centerOn(node.grNode)

    # --- run status -----------------------------------------------------------------------------------------------
    def set_status(self, node_id: int, state: str | None, text: str = "", tooltip: str = "") -> None:
        node = self.node(node_id)
        if node is None:
            return
        node.grNode.run_state = state
        node.content.set_status(state, text, tooltip)
        node.grNode.update()

    def set_thumbnail(self, node_id: int, pixmap: QPixmap | None) -> None:
        node = self.node(node_id)
        if node is not None:
            node.content.set_thumbnail(pixmap)

    def clear_status(self) -> None:
        for node in self.scene.nodes:
            node.grNode.run_state = None
            node.content.set_status(None)
            node.content.set_thumbnail(None)
            node.grNode.update()

    # --- selection, history ---------------------------------------------------------------------------------------
    def _selection_changed(self) -> None:
        if self._loading:
            return
        nodes = self.scene.getSelectedNodes()
        self.on_selection(nodes[0].comfy_id if len(nodes) == 1 else None, len(nodes))

    def _history_restored(self) -> None:
        self._recolor_edges()
        self.on_restored()
        self._selection_changed()
        self.on_changed()

    def _history_stored(self) -> None:
        self._recolor_edges()
        if not self._loading:
            self.on_changed()

    def _recolor_edges(self) -> None:
        for edge in self.scene.edges:
            if edge.start_socket is not None and edge.end_socket is not None:
                edge.grEdge.setColorFromSockets()  # IMAGE/MASK links take their socket colour

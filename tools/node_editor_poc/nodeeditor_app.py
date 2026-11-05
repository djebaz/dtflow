"""Node-editor POC candidate: nodeeditor-qtpy 0.8.2 on pyside6-essentials (#223, audit 10.8 step 6).

Canvas, nodes, sockets, edges, selection and undo/redo come from the library, with a small view subclass for
plain-wheel zoom and empty-background left-drag panning. DTFlow-side code owns
node types, typed-port rules, parameters, the inspector, theme tokens and the ComfyUI-format adapter
(``poc_contract``); the library's own JSON session format is used only internally for its undo snapshots and is
never written to disk. No executor, providers, models or generation.

Run from the repository root (see README.md):  uv run ... python tools/node_editor_poc/nodeeditor_app.py
"""

from __future__ import annotations

import sys
from collections import OrderedDict

import poc_shell as shell  # first: sets the start-up clock and loads tokens before the library import
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QPen
from PySide6.QtWidgets import QGraphicsSimpleTextItem, QGraphicsView, QLabel, QVBoxLayout

import poc_contract as contract
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

SOCKET_TYPES = {contract.IMAGE: 0, contract.MASK: 1}
SOCKET_NAMES = {value: key for key, value in SOCKET_TYPES.items()}
NODE_WIDTH = 230


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
        super().__init__()

    def next_link_id(self):
        self.last_link_id += 1
        return self.last_link_id

    def getEdgeClass(self):
        return DTFEdge


class DTFEdgeDragging(EdgeDragging):
    def edgeDragEnd(self, item):
        # Validate before the library's rejection print/keep-dragging path.
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
            current = abs(self.transform().m11())
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


class DTFEditorWidget(NodeEditorWidget):
    Scene_class = DTFScene
    GraphicsView_class = DTFGraphicsView


class DTFGraphicsNode(QDMGraphicsNode):
    """Library node item with geometry and colours from DTFlow tokens."""

    def initSizes(self) -> None:
        super().initSizes()
        spec = self.node.SPEC
        ports = max(len(spec.inputs), len(spec.outputs), 1)
        self.width = NODE_WIDTH
        self.title_height = 28
        self.edge_roundness = float(shell.TOKENS["size.panelRadius"].removesuffix("px"))
        self.height = self.title_height + 22 * ports + 70 + (96 if spec.thumbnail else 0)

    def initAssets(self) -> None:
        super().initAssets()
        self._title_color = shell.token_color("color.title")
        self._title_font = shell.token_font("type.body", "type.weightEmphasis")
        self._color = shell.token_color("color.borderStrong")
        self._color_selected = shell.token_color("color.focus")
        self._color_hovered = shell.token_color("color.inputBorderHover")
        self._pen_default = QPen(self._color, 1.5)
        self._pen_selected = QPen(self._color_selected, 2.0)
        self._pen_hovered = QPen(self._color_hovered, 2.5)
        self._brush_title = QBrush(shell.token_color("color.sectionHeader"))
        self._brush_background = QBrush(shell.token_color("color.surface"))


class DTFNodeContent(QDMNodeContentWidget):
    """Embedded content: summary of node-owned settings, plus the fixture thumbnail where relevant."""

    def initUI(self) -> None:
        self.setObjectName("dtfNodeContent")
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 22 * max(len(self.node.SPEC.inputs), len(self.node.SPEC.outputs), 1) - 8, 0, 0)
        box.setSpacing(4)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        box.addWidget(self.summary)
        self.thumb = None
        if self.node.SPEC.thumbnail:
            self.thumb = QLabel()
            self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(self.thumb)
        box.addStretch(1)

    def refresh(self) -> None:
        self.summary.setText(contract.summary(self.node.SPEC.type, self.node.params))
        if self.thumb is not None and self.thumb.pixmap().isNull():
            self.thumb.setPixmap(shell.fixture_thumbnail().scaled(88, 88, Qt.AspectRatioMode.KeepAspectRatio))

    # Content state rides in the library's undo snapshots; it is never the saved file format.
    def serialize(self) -> OrderedDict:
        return OrderedDict([("comfy_id", self.node.comfy_id), ("params", contract.copy_params(self.node.params))])

    def deserialize(self, data: dict, hashmap: dict | None = None, restore_id: bool = True) -> bool:
        self.node.comfy_id = data["comfy_id"]
        self.node.params = contract.copy_params(data["params"])
        self.refresh()
        return True


class DTFNode(Node):
    GraphicsNode_class = DTFGraphicsNode
    NodeContent_class = DTFNodeContent
    SPEC: contract.NodeSpec

    def __init__(self, scene, comfy_id: int = 0):
        self.comfy_id = comfy_id
        self.params = self.SPEC.defaults()
        super().__init__(
            scene,
            self.SPEC.title,
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
            label.setFont(shell.token_font())
            label.setBrush(QBrush(shell.token_color("color.text")))
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
        self.input_socket_shapes = {SOCKET_TYPES[contract.MASK]: "square"}
        self.output_socket_shapes = {SOCKET_TYPES[contract.MASK]: "square"}

    def serialize(self) -> OrderedDict:
        data = super().serialize()
        data["dtf_type"] = self.SPEC.type
        return data


NODE_CLASSES: dict[str, type[DTFNode]] = {
    spec.type: type(spec.type + "Node", (DTFNode,), {"SPEC": spec}) for spec in contract.CATALOG.values()
}


def _rejection_sink(message: str) -> None:  # replaced by the window
    pass


REJECT = [_rejection_sink]


def dtf_edge_validator(start, end) -> bool:
    """Typed-port rule (IMAGE->IMAGE, MASK->MASK), output->input only, no self-links, no cycles."""
    if start.is_output == end.is_output:
        return _reject("Connect an output to an input.")
    out, inp = (start, end) if start.is_output else (end, start)
    if out.node is inp.node:
        return _reject("A node cannot feed itself.")
    if out.socket_type != inp.socket_type:
        out_name, in_name = SOCKET_NAMES[out.socket_type], SOCKET_NAMES[inp.socket_type]
        return _reject(f"{out_name} output cannot feed {in_name} input '{inp.node.SPEC.inputs[inp.index].name}'.")
    if out.node in out.node.scene.descendants(inp.node):
        return _reject("That link would create a cycle; workflows must be acyclic.")
    return True


def _reject(message: str) -> bool:
    REJECT[0](message)
    return False


Edge.registerEdgeValidator(dtf_edge_validator)


class NodeEditorWindow(shell.PocWindow):
    CANDIDATE = "nodeeditor-qtpy 0.8.2"

    def build_canvas(self):
        self.editor = DTFEditorWidget()
        self.scene = self.editor.scene
        self.view = self.editor.view
        self.scene.setNodeClassSelector(lambda data: NODE_CLASSES[data["dtf_type"]])
        self._apply_canvas_theme()
        self.scene.addItemSelectedListener(self._selection_changed)
        self.scene.addItemsDeselectedListener(self._selection_changed)
        self.scene.history.addHistoryRestoredListener(self._history_restored)
        self.scene.history.addHistoryStoredListener(self._recolor_edges)
        REJECT[0] = lambda message: self.show_status(f"Connection refused: {message}", error=True)
        return self.editor

    def canvas_focus_widget(self):
        return self.view

    def _apply_canvas_theme(self) -> None:
        image, mask = shell.token_color("color.focus"), shell.token_color("color.warning")
        self.scene.setSocketColors(
            outline=shell.token_color("color.canvas"),
            highlight=shell.token_color("color.selectionText"),
            type_colors=[image, mask],
        )
        self.scene.setEdgeColors(
            default=shell.token_color("color.secondary"),
            selected=shell.token_color("color.onAccent"),
            hovered=shell.token_color("color.accentHover"),
            dragging=shell.token_color("color.glyphNeutral"),
        )
        gr = self.scene.grScene
        gr.setBackgroundColor(shell.token_color("color.canvas"))
        # setGridColorLight() prints a debug line in 0.8.2, so set the pens directly.
        gr._color_light, gr._color_dark = shell.token_color("color.itemSurface"), shell.token_color("color.panelEdge")
        gr._pen_light.setColor(gr._color_light)
        gr._pen_dark.setColor(gr._color_dark)
        gr._pen_state.setColor(shell.token_color("color.secondary"))
        gr._font_state = shell.token_font("type.title", "type.weightTitle")

    # --- graph <-> scene ----------------------------------------------------------------------------
    def load_graph(self, graph: contract.Graph) -> None:
        self.scene.clear()
        self.view_state = graph.view
        self.workflow_id = graph.workflow_id
        self.scene.last_link_id = max(graph.last_link_id, max((link.id for link in graph.links), default=0))
        nodes = {}
        for item in graph.nodes:
            node = NODE_CLASSES[item.type](self.scene, item.id)
            node.params = contract.copy_params(item.params)
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
        QTimer.singleShot(0, self.fit_view)  # after the view has its real size

    def current_graph(self) -> contract.Graph:
        graph = contract.Graph(workflow_id=self.workflow_id, view=self.view_state, last_link_id=self.scene.last_link_id)
        for node in self.scene.nodes:
            pos = node.grNode.scenePos()
            graph.nodes.append(
                contract.GraphNode(
                    node.comfy_id,
                    node.SPEC.type,
                    (pos.x(), pos.y()),
                    contract.copy_params(node.params),
                    (node.grNode.width, node.grNode.height),
                )
            )
        for edge in self.scene.edges:
            out, inp = edge.start_socket, edge.end_socket
            if out is None or inp is None:
                continue
            if out.is_input:
                out, inp = inp, out
            graph.links.append(
                contract.GraphLink(
                    edge.comfy_id,
                    out.node.comfy_id,
                    out.index,
                    inp.node.comfy_id,
                    inp.index,
                    SOCKET_NAMES[out.socket_type],
                )
            )
        return graph

    def _node(self, comfy_id: int):
        return next(node for node in self.scene.nodes if node.comfy_id == comfy_id)

    # --- editing ------------------------------------------------------------------------------------
    def set_node_param(self, node_id: int, name: str, value: object) -> None:
        node = self._node(node_id)
        node.params[name] = value
        node.content.refresh()
        # Typing bursts in one field coalesce into one undo step.
        self.scene.history.storeHistory(f"Edit {name}", setModified=True, merge_key=f"param:{node_id}:{name}")

    def add_node(self, node_type: str) -> None:
        next_id = max((node.comfy_id for node in self.scene.nodes), default=0) + 1
        node = NODE_CLASSES[node_type](self.scene, next_id)
        center = self.view.mapToScene(self.view.viewport().rect().center())
        node.setPos(center.x() - NODE_WIDTH / 2, center.y() - 80)
        self.scene.history.storeHistory(f"Add {node.SPEC.title}", setModified=True)

    def undo(self) -> None:
        self.scene.undo()

    def redo(self) -> None:
        self.scene.redo()

    def delete_selected(self) -> None:
        self.view.deleteSelected()
        self._selection_changed()

    def fit_view(self) -> None:
        if self.scene.nodes:
            rect = self.scene.grScene.itemsBoundingRect().adjusted(-60, -60, 60, 60)
            self.view.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    # --- selection, history -------------------------------------------------------------------------
    def _selection_changed(self) -> None:
        nodes = self.scene.getSelectedNodes()
        if len(nodes) == 1:
            node = nodes[0]
            self.inspector.show_node(node.comfy_id, node.SPEC.type, node.params, self.thumbnail_for(node.SPEC.type))
        else:
            self.inspector.clear("Select one node on the canvas." if not nodes else f"{len(nodes)} nodes selected.")

    def _history_restored(self) -> None:
        self._recolor_edges()
        self._selection_changed()

    def _recolor_edges(self) -> None:
        for edge in self.scene.edges:
            if edge.start_socket is not None and edge.end_socket is not None:
                edge.grEdge.setColorFromSockets()  # IMAGE/MASK links take their socket colour


if __name__ == "__main__":
    sys.exit(shell.run(NodeEditorWindow))

"""Headless node catalogue and strict ComfyUI-format adapter for the node-editor POC (#223).

POC only. This is a stand-in for the step 1/2 contract in the #223 audit (section 10.8), written so the
canvas candidates have something real to load and save. It is not the product contract: node names follow
the audit, but parameter lists, ranges, sampler names and placeholder model ids are illustrative.

File rules (audit 10.7 and owner decision D1):
- ComfyUI saved-workflow JSON v1.0 shape: ``version`` 1, ``state``, ``nodes``, object ``links``.
- ``widgets_values`` is an array in a fixed per-type order (``NodeSpec.params``). Internally the editor
  uses named parameter dicts; arrays exist only at the file boundary.
- Import is all-or-nothing: any unknown node, key, value, port, link or contract version rejects the
  whole file with one reason. Nothing is repaired, dropped or guessed.
- ``extra.dtflow.contract_version`` versions the DTFlow parameter order, not ComfyUI's ``version``.

No Qt import here, so both canvas candidates and the Essentials-only baseline share it unchanged.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field

CONTRACT_VERSION = 1
IMAGE = "IMAGE"
MASK = "MASK"
PORT_TYPES = (IMAGE, MASK)

# Illustrative choices for configuration controls. Not DTFlow's model, LoRA or sampler services.
PLACEHOLDER_CHECKPOINTS = (
    "placeholder_checkpoint_a.ckpt",
    "placeholder_checkpoint_b.ckpt",
    "placeholder_checkpoint_c.ckpt",
)
PLACEHOLDER_LORAS = ("placeholder_lora_a.safetensors", "placeholder_lora_b.safetensors")
SAMPLERS = ("DPM++ 2M Karras", "Euler A", "DDIM", "UniPC", "DPM++ SDE Karras")
SEED_MAX = 4294967295


class WorkflowError(ValueError):
    """The whole file is rejected; ``str(error)`` is the single actionable reason."""


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # checkpoint | loras | text | string | int | float | choice
    default: object
    label: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class Port:
    name: str
    type: str


@dataclass(frozen=True)
class NodeSpec:
    type: str
    title: str
    inputs: tuple[Port, ...]
    outputs: tuple[Port, ...]
    params: tuple[Param, ...]
    thumbnail: bool = False

    def defaults(self) -> dict[str, object]:
        return {param.name: _copy(param.default) for param in self.params}


def _model_params(checkpoint: str, strength: bool) -> tuple[Param, ...]:
    params = [
        Param("checkpoint", "checkpoint", checkpoint, "Checkpoint", choices=PLACEHOLDER_CHECKPOINTS),
        Param("loras", "loras", [], "LoRAs", minimum=-5.0, maximum=5.0, step=0.05, choices=PLACEHOLDER_LORAS),
        Param("prompt", "text", "", "Prompt"),
        Param("negative_prompt", "text", "", "Negative prompt"),
        Param("seed", "int", 0, "Seed", 0, SEED_MAX, 1),
        Param("steps", "int", 20, "Steps", 1, 150, 1),
        Param("guidance", "float", 4.5, "Guidance", 0.0, 50.0, 0.1),
        Param("sampler", "choice", SAMPLERS[0], "Sampler", choices=SAMPLERS),
    ]
    if strength:
        params.append(Param("strength", "float", 0.6, "Strength", 0.0, 1.0, 0.01))
    return tuple(params)


CATALOG: dict[str, NodeSpec] = {
    spec.type: spec
    for spec in (
        NodeSpec(
            "DTFGenerate",
            "Generate",
            (),
            (Port("IMAGE", IMAGE),),
            _model_params(PLACEHOLDER_CHECKPOINTS[0], strength=False)
            + (
                Param("width", "int", 1024, "Width", 64, 4096, 64),
                Param("height", "int", 1024, "Height", 64, 4096, 64),
            ),
            thumbnail=True,
        ),
        NodeSpec(
            "DTFDetectFaceMask",
            "Detect face mask",
            (Port("image", IMAGE),),
            (Port("MASK", MASK),),
            (
                Param("mask_region", "choice", "face_skin", "Mask region", choices=("face_skin", "face_skin_and_hair")),
                Param("person", "choice", "strongest", "Person", choices=("strongest", "all")),
            ),
        ),
        NodeSpec(
            "DTFInpaint",
            "Inpaint",
            (Port("image", IMAGE), Port("mask", MASK)),
            (Port("IMAGE", IMAGE),),
            _model_params(PLACEHOLDER_CHECKPOINTS[1], strength=True),
        ),
        NodeSpec(
            "DTFImg2Img",
            "Img2img refine",
            (Port("image", IMAGE),),
            (Port("IMAGE", IMAGE),),
            _model_params(PLACEHOLDER_CHECKPOINTS[2], strength=True),
        ),
        NodeSpec(
            "DTFSaveImage",
            "Save image",
            (Port("image", IMAGE),),
            (),
            (Param("filename_prefix", "string", "dtflow_workflow", "Filename prefix"),),
            thumbnail=True,
        ),
    )
}


@dataclass
class GraphNode:
    id: int
    type: str
    pos: tuple[float, float]
    params: dict[str, object]
    size: tuple[float, float] = (240.0, 160.0)


@dataclass
class GraphLink:
    id: int
    origin_id: int
    origin_slot: int
    target_id: int
    target_slot: int
    type: str


@dataclass
class Graph:
    nodes: list[GraphNode] = field(default_factory=list)
    links: list[GraphLink] = field(default_factory=list)
    workflow_id: str = ""
    view: dict | None = None  # extra.ds (canvas scale/offset) is kept, never interpreted
    last_link_id: int = 0

    def node(self, node_id: int) -> GraphNode:
        return next(node for node in self.nodes if node.id == node_id)

    def diagnostics(self) -> list[str]:
        """Draft-level notes (not import errors): unconnected inputs. The editor may save drafts."""
        connected = {(link.target_id, link.target_slot) for link in self.links}
        notes = []
        for node in self.nodes:
            for slot, port in enumerate(CATALOG[node.type].inputs):
                if (node.id, slot) not in connected:
                    notes.append(f"{CATALOG[node.type].title} #{node.id}: input '{port.name}' is not connected.")
        return notes


def check_param(param: Param, value: object, where: str = "") -> object:
    """Return the value when it fits ``param`` exactly; raise WorkflowError otherwise."""
    label = f"{where}{param.name}"
    if param.kind in ("checkpoint", "text", "string"):
        if not isinstance(value, str):
            raise WorkflowError(f"{label} must be a string.")
        if param.kind in ("checkpoint", "string") and not value.strip():
            raise WorkflowError(f"{label} must not be empty.")
        return value
    if param.kind == "choice":
        if value not in param.choices:
            raise WorkflowError(f"{label} must be one of {', '.join(param.choices)}; got {value!r}.")
        return value
    if param.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise WorkflowError(f"{label} must be an integer.")
        if not param.minimum <= value <= param.maximum:
            raise WorkflowError(f"{label} must be between {param.minimum} and {param.maximum}; got {value}.")
        if param.step and param.step > 1 and value % param.step:
            raise WorkflowError(f"{label} must be a multiple of {param.step}; got {value}.")
        return value
    if param.kind == "float":
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise WorkflowError(f"{label} must be a number.")
        if not param.minimum <= value <= param.maximum:
            raise WorkflowError(f"{label} must be between {param.minimum} and {param.maximum}; got {value}.")
        return float(value)
    if param.kind == "loras":
        if not isinstance(value, list):
            raise WorkflowError(f"{label} must be a list of [file, weight] pairs.")
        result = []
        for index, entry in enumerate(value):
            if not (isinstance(entry, list) and len(entry) == 2):
                raise WorkflowError(f"{label}[{index}] must be a [file, weight] pair.")
            name, weight = entry
            if not isinstance(name, str) or not name.strip():
                raise WorkflowError(f"{label}[{index}] file must be a non-empty string.")
            if isinstance(weight, bool) or not isinstance(weight, int | float) or not math.isfinite(weight):
                raise WorkflowError(f"{label}[{index}] weight must be a number.")
            if not param.minimum <= weight <= param.maximum:
                raise WorkflowError(f"{label}[{index}] weight must be between {param.minimum} and {param.maximum}.")
            result.append([name, float(weight)])
        return result
    raise AssertionError(param.kind)


_TOP_KEYS = {"id", "revision", "version", "state", "nodes", "links", "groups", "reroutes", "config", "extra", "models"}
_STATE_KEYS = {"lastNodeId", "lastLinkId", "lastRerouteId", "lastGroupid"}
_NODE_KEYS = {
    "id",
    "type",
    "pos",
    "size",
    "flags",
    "order",
    "mode",
    "inputs",
    "outputs",
    "properties",
    "widgets_values",
}
_LINK_KEYS = {"id", "origin_id", "origin_slot", "target_id", "target_slot", "type"}
_EXTRA_KEYS = {"dtflow", "ds", "frontendVersion"}


def _int(value: object, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise WorkflowError(f"{label} must be an integer >= {minimum}.")
    return value


def _pair(value: object, label: str) -> tuple[float, float]:
    if not (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) for v in value)
    ):
        raise WorkflowError(f"{label} must be a [x, y] number pair.")
    return float(value[0]), float(value[1])


def _keys(obj: object, allowed: set[str], required: set[str], label: str) -> dict:
    if not isinstance(obj, dict):
        raise WorkflowError(f"{label} must be an object.")
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise WorkflowError(f"{label} has unsupported key(s): {', '.join(unknown)}.")
    missing = sorted(required - set(obj))
    if missing:
        raise WorkflowError(f"{label} is missing: {', '.join(missing)}.")
    return obj


def load_workflow(data: object) -> Graph:
    """Strictly parse a DTFlow ComfyUI-format workflow. Raises WorkflowError with one reason."""
    top = _keys(data, _TOP_KEYS, {"version", "state", "nodes", "links", "extra"}, "Workflow")
    if top["version"] != 1 or isinstance(top["version"], bool):
        raise WorkflowError(f"Only ComfyUI workflow format version 1 is supported; got {top['version']!r}.")
    for key in ("groups", "reroutes", "models"):
        if top.get(key):
            raise WorkflowError(f"Workflow '{key}' are not supported by DTFlow V1.")
    if top.get("config"):
        raise WorkflowError("Workflow 'config' is not supported by DTFlow V1.")
    extra = _keys(top["extra"], _EXTRA_KEYS, {"dtflow"}, "extra")
    dtflow = _keys(extra["dtflow"], {"contract_version"}, {"contract_version"}, "extra.dtflow")
    if dtflow["contract_version"] != CONTRACT_VERSION or isinstance(dtflow["contract_version"], bool):
        raise WorkflowError(
            f"DTFlow contract version {dtflow['contract_version']!r} is not supported (expected {CONTRACT_VERSION})."
        )
    state = _keys(top["state"], _STATE_KEYS, _STATE_KEYS, "state")
    for key in _STATE_KEYS:
        _int(state[key], f"state.{key}")

    if not isinstance(top["nodes"], list):
        raise WorkflowError("nodes must be an array.")
    if not isinstance(top["links"], list):
        raise WorkflowError("links must be an array.")

    graph = Graph(workflow_id=str(top.get("id", "")), view=extra.get("ds"), last_link_id=state["lastLinkId"])
    raw_nodes: dict[int, dict] = {}
    for index, raw in enumerate(top["nodes"]):
        where = f"nodes[{index}]"
        node = _keys(raw, _NODE_KEYS, _NODE_KEYS - {"inputs", "outputs"}, where)
        node_id = _int(node["id"], f"{where}.id", 1)
        if node_id in raw_nodes:
            raise WorkflowError(f"Node id {node_id} is used twice.")
        spec = CATALOG.get(node["type"])
        if spec is None:
            raise WorkflowError(f"Node #{node_id} has unsupported type {node['type']!r}.")
        where = f"{spec.title} #{node_id}"
        if node["mode"] != 0:
            raise WorkflowError(f"{where}: muted or bypassed nodes (mode {node['mode']!r}) are not supported.")
        if node["flags"] not in ({}, {"collapsed": False}, {"collapsed": True}):
            raise WorkflowError(f"{where}: unsupported flags {node['flags']!r}.")
        if node["properties"] not in ({}, {"Node name for S&R": spec.type}):
            raise WorkflowError(f"{where}: unsupported properties.")
        _int(node["order"], f"{where} order")
        values = node["widgets_values"]
        if not isinstance(values, list):
            raise WorkflowError(f"{where}: widgets_values must be an array (DTFlow contract {CONTRACT_VERSION}).")
        if len(values) != len(spec.params):
            raise WorkflowError(f"{where}: widgets_values has {len(values)} entries, expected {len(spec.params)}.")
        params = {
            param.name: check_param(param, value, f"{where}: ")
            for param, value in zip(spec.params, values, strict=True)
        }
        _check_ports(node.get("inputs", []), spec.inputs, f"{where} inputs", is_input=True)
        _check_ports(node.get("outputs", []), spec.outputs, f"{where} outputs", is_input=False)
        raw_nodes[node_id] = node
        graph.nodes.append(
            GraphNode(
                node_id, spec.type, _pair(node["pos"], f"{where} pos"), params, _pair(node["size"], f"{where} size")
            )
        )

    link_ids: set[int] = set()
    targets: set[tuple[int, int]] = set()
    for index, raw in enumerate(top["links"]):
        if isinstance(raw, list):
            raise WorkflowError("Array links are the older 0.4 format; DTFlow reads version 1 object links only.")
        link = _keys(raw, _LINK_KEYS | {"parentId"}, _LINK_KEYS, f"links[{index}]")
        if link.get("parentId") is not None:
            raise WorkflowError(f"Link {link['id']}: reroutes are not supported by DTFlow V1.")
        link_id = _int(link["id"], f"links[{index}].id", 1)
        if link_id in link_ids:
            raise WorkflowError(f"Link id {link_id} is used twice.")
        link_ids.add(link_id)
        origin, target = raw_nodes.get(link["origin_id"]), raw_nodes.get(link["target_id"])
        if origin is None or target is None:
            raise WorkflowError(f"Link {link_id} refers to a missing node.")
        origin_ports, target_ports = CATALOG[origin["type"]].outputs, CATALOG[target["type"]].inputs
        o_slot, t_slot = link["origin_slot"], link["target_slot"]
        if not (isinstance(o_slot, int) and 0 <= o_slot < len(origin_ports)):
            raise WorkflowError(f"Link {link_id}: origin slot {o_slot!r} does not exist.")
        if not (isinstance(t_slot, int) and 0 <= t_slot < len(target_ports)):
            raise WorkflowError(f"Link {link_id}: target slot {t_slot!r} does not exist.")
        if origin_ports[o_slot].type != target_ports[t_slot].type:
            raise WorkflowError(
                f"Link {link_id}: {origin_ports[o_slot].type} output cannot feed {target_ports[t_slot].type} input."
            )
        if link["type"] != origin_ports[o_slot].type:
            raise WorkflowError(
                f"Link {link_id}: type {link['type']!r} does not match its {origin_ports[o_slot].type} ports."
            )
        if (link["target_id"], t_slot) in targets:
            raise WorkflowError(f"Link {link_id}: input already has a link.")
        targets.add((link["target_id"], t_slot))
        if target.get("inputs", [{}] * len(target_ports))[t_slot].get("link") != link_id:
            raise WorkflowError(f"Link {link_id}: target input does not reference it.")
        if link_id not in (origin.get("outputs", [{}] * len(origin_ports))[o_slot].get("links") or []):
            raise WorkflowError(f"Link {link_id}: origin output does not list it.")
        graph.links.append(GraphLink(link_id, link["origin_id"], o_slot, link["target_id"], t_slot, link["type"]))

    for node in raw_nodes.values():
        for slot, port in enumerate(node.get("inputs", [])):
            if port.get("link") is not None and (node["id"], slot) not in targets:
                raise WorkflowError(f"Node #{node['id']} input '{port['name']}' references a missing link.")
        for port in node.get("outputs", []):
            for link_id in port.get("links") or []:
                if link_id not in link_ids:
                    raise WorkflowError(f"Node #{node['id']} output '{port['name']}' lists missing link {link_id}.")

    if max((n.id for n in graph.nodes), default=0) > state["lastNodeId"]:
        raise WorkflowError("state.lastNodeId is lower than a node id.")
    if max(link_ids, default=0) > state["lastLinkId"]:
        raise WorkflowError("state.lastLinkId is lower than a link id.")
    topological_order(graph)  # rejects cycles
    return graph


def _check_ports(ports: object, expected: tuple[Port, ...], where: str, *, is_input: bool) -> None:
    if not isinstance(ports, list) or len(ports) != len(expected):
        raise WorkflowError(f"{where} must list exactly: {', '.join(p.name for p in expected) or 'nothing'}.")
    allowed = {"name", "type", "link"} if is_input else {"name", "type", "links", "slot_index"}
    for slot, (port, spec) in enumerate(zip(ports, expected, strict=True)):
        port = _keys(port, allowed, {"name", "type"}, f"{where}[{slot}]")
        if port["name"] != spec.name or port["type"] != spec.type:
            raise WorkflowError(f"{where}[{slot}] must be {spec.name}:{spec.type}; got {port['name']}:{port['type']}.")
        if not is_input and port.get("slot_index", slot) != slot:
            raise WorkflowError(f"{where}[{slot}] slot_index must be {slot}.")


def topological_order(graph: Graph) -> list[int]:
    """Kahn order by node id; raises WorkflowError on a cycle."""
    incoming = {node.id: 0 for node in graph.nodes}
    children: dict[int, list[int]] = {node.id: [] for node in graph.nodes}
    for link in graph.links:
        incoming[link.target_id] += 1
        children[link.origin_id].append(link.target_id)
    ready = sorted(node_id for node_id, count in incoming.items() if count == 0)
    order = []
    while ready:
        node_id = ready.pop(0)
        order.append(node_id)
        for child in children[node_id]:
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
                ready.sort()
    if len(order) != len(graph.nodes):
        raise WorkflowError("The graph has a cycle; DTFlow workflows must be acyclic.")
    return order


def dump_workflow(graph: Graph) -> dict:
    """Write ComfyUI v1.0 with widgets_values arrays and stable positive link ids."""
    order = {node_id: index for index, node_id in enumerate(topological_order(graph))}
    links = sorted(graph.links, key=lambda link: (order[link.target_id], link.target_slot))
    ids = [_int(link.id, "Link id", 1) for link in links]
    if len(set(ids)) != len(ids):
        raise WorkflowError("Link ids must be unique.")
    nodes = []
    for node in sorted(graph.nodes, key=lambda n: n.id):
        spec = CATALOG[node.type]
        entry = {
            "id": node.id,
            "type": node.type,
            "pos": [round(float(node.pos[0]), 1), round(float(node.pos[1]), 1)],
            "size": [round(float(node.size[0]), 1), round(float(node.size[1]), 1)],
            "flags": {},
            "order": order[node.id],
            "mode": 0,
        }
        if spec.inputs:
            entry["inputs"] = [
                {
                    "name": port.name,
                    "type": port.type,
                    "link": next(
                        (link.id for link in links if link.target_id == node.id and link.target_slot == slot), None
                    ),
                }
                for slot, port in enumerate(spec.inputs)
            ]
        if spec.outputs:
            entry["outputs"] = [
                {
                    "name": port.name,
                    "type": port.type,
                    "links": [link.id for link in links if link.origin_id == node.id and link.origin_slot == slot],
                    "slot_index": slot,
                }
                for slot, port in enumerate(spec.outputs)
            ]
        entry["properties"] = {"Node name for S&R": node.type}
        entry["widgets_values"] = [_copy(node.params[param.name]) for param in spec.params]
        nodes.append(entry)
    extra: dict = {"dtflow": {"contract_version": CONTRACT_VERSION}}
    if graph.view is not None:
        extra["ds"] = graph.view
    return {
        "id": graph.workflow_id or str(uuid.uuid4()),
        "revision": 0,
        "version": 1,
        "state": {
            "lastNodeId": max((n.id for n in graph.nodes), default=0),
            "lastLinkId": max(graph.last_link_id, max(ids, default=0)),
            "lastRerouteId": 0,
            "lastGroupid": 0,
        },
        "nodes": nodes,
        "links": [
            {
                "id": link.id,
                "origin_id": link.origin_id,
                "origin_slot": link.origin_slot,
                "target_id": link.target_id,
                "target_slot": link.target_slot,
                "type": link.type,
            }
            for link in links
        ],
        "groups": [],
        "config": {},
        "extra": extra,
    }


def summary(node_type: str, params: dict[str, object]) -> str:
    """Short on-node text so the canvas shows configuration without opening the inspector."""
    if node_type == "DTFDetectFaceMask":
        return f"{params['mask_region']} · person: {params['person']}"
    if node_type == "DTFSaveImage":
        return f"prefix: {params['filename_prefix']}"
    lines = [str(params["checkpoint"])]
    loras = params["loras"]
    lines.append(f"{len(loras)} LoRA" + ("s" if len(loras) != 1 else "") if loras else "no LoRA")
    detail = f"{params['steps']} steps · cfg {params['guidance']:g}"
    if "strength" in params:
        detail += f" · str {params['strength']:.2f}"
    lines.append(detail)
    return "\n".join(lines)


def _copy(value: object) -> object:
    return [list(item) if isinstance(item, list) else item for item in value] if isinstance(value, list) else value


def copy_params(params: dict[str, object]) -> dict[str, object]:
    return {name: _copy(value) for name, value in params.items()}

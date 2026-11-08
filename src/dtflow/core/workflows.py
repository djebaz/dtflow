"""DTFlow workflow contract v1: node catalogue, strict ComfyUI-format adapter, graph checks and fan-out plan (#223).

Backend-neutral and Qt/Pillow-free. The file shape is ComfyUI saved-workflow JSON v1.0 (``version`` 1, ``state``,
``nodes``, object ``links``); it is *structure* compatibility only, never a claim that DTFlow nodes run in ComfyUI.

Rules (audit 10.7/10.8, owner decisions D1-D8):

- ``widgets_values`` is an array in the fixed per-type order of ``NodeSpec.params`` (D1). DTFlow uses named parameter
  dicts internally; arrays exist only at the file boundary. The order is versioned by ``extra.dtflow``
  (``{"schema": "dtflow-workflow", "version": 1}``), never by ComfyUI's top-level ``version``.
- Import is all-or-nothing: any unknown node, key, value, port, link, flag, mode or contract version rejects the whole
  file with one reason. Nothing is repaired, dropped or guessed. Groups, reroutes, subgraphs and ``models`` are rejected.
- Range and semantic checks of generation settings reuse ``ResolvedGenerationRequest`` (see ``node_request``); this
  module adds only graph checks: types, cycles, one link per input and same-branch pairing (D3).
- Fan-out (D3): only ``DTFGenerate`` produces several images (``batch_size * batch_count``). Every downstream node runs
  once per image ("branch"); converging inputs must come from the same branches, never a cross product.
  Each output is its own single-image request with seed = node seed + output index (wrapped at the seed limit, #275).
  ``plan_workflow`` returns the total number of generations the run will submit, the base of the runtime loop guard.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
import uuid
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from dtflow.core.models import SEED_LIMIT, LoRASelection, ResolvedGenerationRequest, Sampler, ValidationError

SCHEMA = "dtflow-workflow"
CONTRACT_VERSION = 1
COMFY_FORMAT_VERSION = 1
IMAGE = "IMAGE"
MASK = "MASK"
PORT_TYPES = (IMAGE, MASK)
MAX_NODES = 200  # practical bound on one file, not a chain-length limit
MAX_PLANNED_GENERATIONS = 256  # practical bound on one run's submissions after fan-out
MAX_CONCURRENCY = 10  # most steps of one workflow, or of one node, that run together (#286)
# Built-in defaults; the app settings (Connection tab) replace them: DT+, local, fake backend, and local segmentation
# nodes (Detect face today; later segmentation nodes share it).
DEFAULT_CONCURRENCY = {"cloud": 10, "local": 2, "fake": 4, "segmentation": 2}
PROP_CONCURRENCY = "DTFlow concurrency"  # optional node property; a missing value means the default
PROP_OUTPUTS = "DTFlow outputs"  # optional Inpaint/Img2img node property: images made per incoming image (#276)
MAX_OUTPUTS = 4  # most outputs per incoming image of one Inpaint/Img2img node
PROP_PROMPT_MODE = "DTFlow prompt mode"  # optional generative-node property: "sample" or "all" (#317); absent = literal
PROP_PROMPT_COUNT = "DTFlow prompt count"  # optional with "sample": how many random prompts the node draws (#317)
PROMPT_MODES = ("sample", "all")
MAX_PROMPT_COUNT = 16  # most prompts one node draws per incoming image (the wide branch form, #317)
LORA_WEIGHT_RANGE = (0.0, 3.0)  # the LoRA editor's range; imported values outside it are rejected, never clamped
MASK_REGIONS = ("face_skin", "face_skin_and_hair")
GEOMETRIC_FACE_METHODS = ("fast", "balanced")  # the methods stored as a node property; the rest are legacy mask regions
FACE_METHODS = (*GEOMETRIC_FACE_METHODS, *MASK_REGIONS)
# Detection model roles each method needs; face_model_roles() takes the union over a graph's Detect nodes.
FACE_METHOD_ROLES = {
    "fast": frozenset({"faces"}),
    "balanced": frozenset({"faces", "segmentation"}),
    "face_skin": frozenset({"faces", "segmentation", "clothes"}),
    "face_skin_and_hair": frozenset({"faces", "segmentation", "clothes"}),
}
FACE_ROLE_ORDER = ("segmentation", "clothes", "faces")
FACE_EXPANSION_DEFAULT = 1.6
FACE_EXPANSION_MIN = 1.0
FACE_EXPANSION_MAX = 2.5
PROP_FACE_METHOD = "DTFlow mask method"  # optional: absent means the legacy precise mask_region
PROP_FACE_EXPANSION = "DTFlow face expansion"  # only for fast/balanced; absent means 1.6

PERSON_CHOICES = ("strongest", "all")
SAMPLERS = tuple(sampler.value for sampler in Sampler)

GENERATE = "DTFGenerate"
DETECT_FACE = "DTFDetectFaceMask"
INPAINT = "DTFInpaint"
IMG2IMG = "DTFImg2Img"
SAVE = "DTFSaveImage"
GENERATIVE = (GENERATE, INPAINT, IMG2IMG)
SEGMENTATION = (DETECT_FACE,)  # local segmentation/detection nodes; they share one default concurrency
IMAGE_OPERATIONS = {INPAINT: "inpaint", IMG2IMG: "img2img"}


class WorkflowError(ValidationError):
    """The whole file or run is rejected; ``str(error)`` is the single actionable reason."""


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # checkpoint | loras | text | string | int | float | choice | bool
    default: object
    label: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: tuple[str, ...] = ()
    hint: str = ""


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
    description: str = ""
    thumbnail: bool = False

    def defaults(self) -> dict[str, object]:
        return {param.name: _copy(param.default) for param in self.params}

    def param(self, name: str) -> Param:
        return next(param for param in self.params if param.name == name)


def _model_params(strength: float | None) -> tuple[Param, ...]:
    """Each generative node owns its exact checkpoint, LoRAs and sampling settings (no workflow-wide model)."""
    params = [
        Param("checkpoint", "checkpoint", "", "Checkpoint", hint="Exact model file on the selected backend."),
        Param("loras", "loras", [], "LoRAs", LORA_WEIGHT_RANGE[0], LORA_WEIGHT_RANGE[1], 0.1),
        Param("prompt", "text", "", "Prompt"),
        Param("negative_prompt", "text", "", "Negative prompt"),
        Param("seed", "int", 0, "Seed", 0, SEED_LIMIT, 1),
        Param("steps", "int", 20, "Steps", 1, 150, 1),
        Param("guidance", "float", 4.5, "Guidance", 0.0, 50.0, 0.1),
        Param("sampler", "choice", Sampler.DPMPP_2M_KARRAS.value, "Sampler", choices=SAMPLERS),
    ]
    if strength is not None:
        params.append(Param("strength", "float", strength, "Strength", 0.0, 1.0, 0.01))
    return tuple(params)


CATALOG: dict[str, NodeSpec] = {
    spec.type: spec
    for spec in (
        NodeSpec(
            GENERATE,
            "Generate",
            (),
            (Port("IMAGE", IMAGE),),
            _model_params(None)
            + (
                Param("width", "int", 1024, "Width", 128, 2048, 64),
                Param("height", "int", 1024, "Height", 128, 2048, 64),
                Param("batch_size", "int", 1, "Batch size", 1, 4, 1),
                Param("batch_count", "int", 1, "Batch count", 1, 4, 1),
            ),
            "Text-to-image. Several images fan out: every downstream node runs once per image.",
            thumbnail=True,
        ),
        NodeSpec(
            DETECT_FACE,
            "Detect face mask",
            (Port("image", IMAGE),),
            (Port("MASK", MASK),),
            (
                Param("mask_region", "choice", "face_skin", "Mask region", choices=MASK_REGIONS),
                Param("person", "choice", "strongest", "Person", choices=PERSON_CHOICES),
            ),
            "Local analysis (person segmentation, clothes/face parsing, faces model). No person, no face box or an "
            "empty region pauses only that image branch.",
        ),
        NodeSpec(
            INPAINT,
            "Inpaint",
            (Port("image", IMAGE), Port("mask", MASK)),
            (Port("IMAGE", IMAGE),),
            _model_params(0.75)
            + (
                Param("grow", "int", 0, "Mask grow", -64, 64, 1),
                Param("feather", "float", 0.0, "Mask feather", 0.0, 64.0, 0.5),
                Param("preserve_outside", "bool", True, "Keep outside mask"),
            ),
            "Inpaints the masked region at the source image size.",
            thumbnail=True,
        ),
        NodeSpec(
            IMG2IMG,
            "Img2img refine",
            (Port("image", IMAGE),),
            (Port("IMAGE", IMAGE),),
            _model_params(0.35),
            "Image-to-image refinement at the source image size.",
            thumbnail=True,
        ),
        NodeSpec(
            SAVE,
            "Save image",
            (Port("image", IMAGE),),
            (),
            (Param("label", "string", "result", "Result label"),),
            "Marks its image as a workflow result. Every step's image is already saved in Images.",
            thumbnail=True,
        ),
    )
}


@dataclass(frozen=True, slots=True)
class FaceOptions:
    """Detect face mask options kept outside ``widgets_values`` (#299): the file stores them as node properties."""

    method: str | None = None  # None = legacy Precise: the node's mask_region param decides, no property is stored
    expansion: float = FACE_EXPANSION_DEFAULT


@dataclass
class GraphNode:
    id: int
    type: str
    pos: tuple[float, float]
    params: dict[str, object]
    size: tuple[float, float] = (240.0, 160.0)
    concurrency: int | None = None  # steps of this node running together (#286); None = the workflow's limit
    outputs: int = 1  # Inpaint/Img2img only: images made per incoming image (#276); the file omits the default 1
    prompt_mode: str | None = None  # "sample" / "all": the prompt is a dynamic-prompt template (#317); None = literal
    prompt_count: int | None = None  # "sample" only: prompts drawn per incoming image (Generate: its image count)
    face: FaceOptions = FaceOptions()  # Detect face mask only

    @property
    def spec(self) -> NodeSpec:
        return CATALOG[self.type]

    @property
    def label(self) -> str:
        return f"{CATALOG[self.type].title} #{self.id}"


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
    frontend_version: str | None = None
    concurrency: int | None = None  # steps of this workflow running together (#286); None = the backend default

    def node(self, node_id: int) -> GraphNode:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise WorkflowError(f"Node #{node_id} does not exist.")

    def input_link(self, node_id: int, slot: int) -> GraphLink | None:
        return next((x for x in self.links if x.target_id == node_id and x.target_slot == slot), None)

    def children(self, node_id: int) -> list[int]:
        return sorted({link.target_id for link in self.links if link.origin_id == node_id})

    def next_node_id(self) -> int:
        return max((node.id for node in self.nodes), default=0) + 1

    def next_link_id(self) -> int:
        return max(self.last_link_id, max((link.id for link in self.links), default=0)) + 1

    def diagnostics(self) -> list[str]:
        """Draft-level notes (not import errors): what must be fixed before the graph can run."""
        notes = []
        for node in self.nodes:
            for slot, port in enumerate(node.spec.inputs):
                if self.input_link(node.id, slot) is None:
                    notes.append(f"{node.label}: input '{port.name}' is not connected.")
            for param in node.spec.params:
                if param.kind == "checkpoint" and not str(node.params.get(param.name, "")).strip():
                    notes.append(f"{node.label}: choose a checkpoint.")
        if not any(node.type == SAVE for node in self.nodes):
            notes.append("Add a Save image node to mark the workflow result.")
        return notes


def check_param(param: Param, value: object, where: str = "", *, draft: bool = False) -> object:
    """Return the value when it fits ``param`` exactly; raise WorkflowError otherwise. Never clamps or coerces.

    ``draft`` accepts an empty checkpoint, so an unfinished workflow can be saved; running still requires one.
    """
    label = f"{where}{param.label}"
    if param.kind in ("checkpoint", "text", "string"):
        if not isinstance(value, str):
            raise WorkflowError(f"{label} must be text.")
        if param.kind == "string" and not value.strip():
            raise WorkflowError(f"{label} must not be empty.")
        if param.kind == "checkpoint" and not value.strip() and not draft:
            raise WorkflowError(f"{label}: choose a checkpoint.")
        return value
    if param.kind == "choice":
        if value not in param.choices:
            raise WorkflowError(f"{label} must be one of {', '.join(param.choices)}; got {value!r}.")
        return value
    if param.kind == "bool":
        if type(value) is not bool:
            raise WorkflowError(f"{label} must be true or false.")
        return value
    if param.kind == "int":
        if type(value) is not int:
            raise WorkflowError(f"{label} must be an integer.")
        if not param.minimum <= value <= param.maximum:
            raise WorkflowError(f"{label} must be between {param.minimum:g} and {param.maximum:g}; got {value}.")
        if param.step and param.step > 1 and value % param.step:
            raise WorkflowError(f"{label} must be a multiple of {param.step:g}; got {value}.")
        return value
    if param.kind == "float":
        if type(value) not in (int, float) or not math.isfinite(value):
            raise WorkflowError(f"{label} must be a number.")
        if not param.minimum <= value <= param.maximum:
            raise WorkflowError(f"{label} must be between {param.minimum:g} and {param.maximum:g}; got {value}.")
        return float(value)
    if param.kind == "loras":
        if not isinstance(value, list):
            raise WorkflowError(f"{label} must be a list of [file, weight] pairs.")
        result, seen = [], set()
        for index, entry in enumerate(value):
            if not (isinstance(entry, list) and len(entry) == 2):
                raise WorkflowError(f"{label}[{index}] must be a [file, weight] pair.")
            name, weight = entry
            if not isinstance(name, str) or not name.strip():
                raise WorkflowError(f"{label}[{index}] file must be a non-empty string.")
            if name in seen:
                raise WorkflowError(f"{label}: {name} is listed twice.")
            seen.add(name)
            if type(weight) not in (int, float) or not math.isfinite(weight):
                raise WorkflowError(f"{label}[{index}] weight must be a number.")
            if not param.minimum <= weight <= param.maximum:
                raise WorkflowError(
                    f"{label}[{index}] weight must be between {param.minimum:g} and {param.maximum:g}; got {weight}."
                )
            result.append([name, float(weight)])
        return result
    raise AssertionError(param.kind)


_TOP_KEYS = {"id", "revision", "version", "state", "nodes", "links", "groups", "reroutes", "config", "extra", "models"}
_STATE_KEYS = {"lastNodeId", "lastLinkId", "lastRerouteId", "lastGroupid"}
_NODE_KEYS = {"id", "type", "pos", "size", "flags", "order", "mode", "inputs", "outputs", "properties"}
_NODE_KEYS |= {"widgets_values", "title", "color", "bgcolor"}
_LINK_KEYS = {"id", "origin_id", "origin_slot", "target_id", "target_slot", "type"}
_EXTRA_KEYS = {"dtflow", "ds", "frontendVersion"}


def _int(value: object, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise WorkflowError(f"{label} must be an integer >= {minimum}.")
    return value


def _concurrency(value: object, label: str) -> int | None:
    """An optional concurrency limit: absent/null means the default; otherwise an integer 1..MAX_CONCURRENCY."""
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= MAX_CONCURRENCY:
        raise WorkflowError(f"{label} must be an integer from 1 to {MAX_CONCURRENCY}.")
    return value


def validate_face_expansion(value: object, label: str) -> float:
    """Validate the source-box width/height scale, stored in tenths."""
    if type(value) not in (int, float) or not math.isfinite(value):
        raise WorkflowError(f"{label} must be a number.")
    if not FACE_EXPANSION_MIN <= value <= FACE_EXPANSION_MAX or abs(value * 10 - round(value * 10)) > 1e-6:
        raise WorkflowError(f"{label} must be from 1.0 to 2.5 in steps of 0.1.")
    return round(float(value), 1)


def face_method(node: GraphNode) -> str:
    """The visible method: a missing property preserves the old precise mask region."""
    return node.face.method or str(node.params["mask_region"])


def _face_settings(node: GraphNode) -> None:
    if node.face.method is not None and node.face.method not in GEOMETRIC_FACE_METHODS:
        raise WorkflowError(f"{node.label}: unsupported face mask method.")
    if node.type == DETECT_FACE:
        validate_face_expansion(node.face.expansion, f"{node.label}: Face box expansion")


def face_model_roles(graph: Graph) -> tuple[str, ...]:
    """Union of the model roles required by this graph's selected Detect methods."""
    roles: set[str] = set()
    for node in graph.nodes:
        if node.type == DETECT_FACE:
            roles |= FACE_METHOD_ROLES[face_method(node)]
    return tuple(role for role in FACE_ROLE_ORDER if role in roles)


def default_face_options(node_type: str) -> FaceOptions:
    """New Detect nodes start Balanced; older or imported files keep their legacy precise region (no method)."""
    return FaceOptions("balanced") if node_type == DETECT_FACE else FaceOptions()


def _load_face_options(properties: dict, node_type: str, where: str) -> FaceOptions:
    """Read the optional Detect-only properties; absent means the legacy precise method."""
    if node_type != DETECT_FACE:
        if PROP_FACE_METHOD in properties or PROP_FACE_EXPANSION in properties:
            raise WorkflowError(f"{where}: face mask properties apply to Detect face mask only.")
        return FaceOptions()
    method = properties.get(PROP_FACE_METHOD)
    if method is not None and method not in GEOMETRIC_FACE_METHODS:
        raise WorkflowError(f"{where}: unsupported mask method {method!r}.")
    if PROP_FACE_EXPANSION in properties and method is None:
        raise WorkflowError(f"{where}: Face box expansion requires Fast or Balanced.")
    expansion = validate_face_expansion(
        properties.get(PROP_FACE_EXPANSION, FACE_EXPANSION_DEFAULT), f"{where}: Face box expansion"
    )
    return FaceOptions(method, expansion)


def _dump_face_options(node: GraphNode, properties: dict) -> None:
    """Write the Detect-only properties; the legacy precise method writes none."""
    _face_settings(node)
    if node.face.method is not None:
        properties[PROP_FACE_METHOD] = node.face.method
        properties[PROP_FACE_EXPANSION] = validate_face_expansion(
            node.face.expansion, f"{node.label}: Face box expansion"
        )


def _outputs(value: object, label: str) -> int:
    """An optional output count per incoming image: absent means 1; otherwise an integer 1..MAX_OUTPUTS."""
    if value is None:
        return 1
    if type(value) is not int or not 1 <= value <= MAX_OUTPUTS:
        raise WorkflowError(f"{label} must be an integer from 1 to {MAX_OUTPUTS}.")
    return value


def _prompt_settings(properties: dict, node_type: str, outputs: int, where: str) -> tuple[str | None, int | None]:
    """The optional wildcard-prompt properties (#317): ``(mode, count)``; absent mode means a literal prompt."""
    mode = properties.get(PROP_PROMPT_MODE)
    count = properties.get(PROP_PROMPT_COUNT)
    if mode is None:
        if PROP_PROMPT_COUNT in properties:
            raise WorkflowError(f"{where}: {PROP_PROMPT_COUNT} needs {PROP_PROMPT_MODE}.")
        return None, None
    if node_type not in GENERATIVE:
        raise WorkflowError(f"{where}: {PROP_PROMPT_MODE} applies to Generate, Inpaint and Img2img nodes only.")
    if mode not in PROMPT_MODES:
        raise WorkflowError(f"{where}: {PROP_PROMPT_MODE} must be one of {', '.join(PROMPT_MODES)}.")
    if outputs != 1:
        raise WorkflowError(f"{where}: a wildcard prompt replaces the outputs per image; leave {PROP_OUTPUTS} unset.")
    if mode == "all":
        if count is not None:
            raise WorkflowError(f"{where}: {PROP_PROMPT_COUNT} is for the sample mode; 'all' makes every combination.")
        return mode, None
    if type(count) is not int or not 1 <= count <= MAX_PROMPT_COUNT:
        raise WorkflowError(f"{where}: {PROP_PROMPT_COUNT} must be an integer from 1 to {MAX_PROMPT_COUNT}.")
    return mode, count


def workflow_limit(graph: Graph, backend: str, defaults: dict[str, int] | None = None) -> int:
    """Global limit: steps of one run that may be in flight together; the workflow's own, else the default."""
    if graph.concurrency is not None:
        return graph.concurrency
    return {**DEFAULT_CONCURRENCY, **(defaults or {})}.get(backend, 1)


def node_limit(graph: Graph, node: GraphNode, backend: str, defaults: dict[str, int] | None = None) -> int:
    """Steps of one node that may run together, never above the global limit; segmentation nodes have their own default."""
    own = node.concurrency
    if own is None and node.type in SEGMENTATION:
        own = {**DEFAULT_CONCURRENCY, **(defaults or {})}["segmentation"]
    limit = workflow_limit(graph, backend, defaults)
    return max(1, min(own if own is not None else limit, limit))


def _pair(value: object, label: str) -> tuple[float, float]:
    if not (
        isinstance(value, list) and len(value) == 2 and all(type(v) in (int, float) and math.isfinite(v) for v in value)
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


def load_workflow(data: object, *, draft: bool = False) -> Graph:
    """Strictly parse a DTFlow ComfyUI-format workflow. Raises WorkflowError with one reason.

    ``draft`` only relaxes the empty-checkpoint rule (an unfinished editor save); every structural rule still applies.
    """
    top = _keys(data, _TOP_KEYS, {"version", "state", "nodes", "links", "extra"}, "Workflow")
    if type(top["version"]) is not int or top["version"] != COMFY_FORMAT_VERSION:
        raise WorkflowError(f"Only ComfyUI workflow format version 1 is supported; got {top['version']!r}.")
    for key in ("groups", "reroutes", "models"):
        if top.get(key):
            raise WorkflowError(f"Workflow '{key}' are not supported by DTFlow workflows.")
    if top.get("config"):
        raise WorkflowError("Workflow 'config' is not supported by DTFlow workflows.")
    if not isinstance(top["extra"], dict):
        raise WorkflowError("extra must be an object.")
    if "definitions" in top["extra"] or "subgraphs" in top:
        raise WorkflowError("Subgraphs are not supported by DTFlow workflows.")
    extra = _keys(top["extra"], _EXTRA_KEYS, {"dtflow"}, "extra")
    dtflow = _keys(extra["dtflow"], {"schema", "version", "concurrency"}, {"schema", "version"}, "extra.dtflow")
    if dtflow["schema"] != SCHEMA:
        raise WorkflowError(f"extra.dtflow.schema must be {SCHEMA!r}; this is not a DTFlow workflow.")
    if type(dtflow["version"]) is not int or dtflow["version"] != CONTRACT_VERSION:
        raise WorkflowError(
            f"DTFlow workflow contract version {dtflow['version']!r} is not supported (expected {CONTRACT_VERSION})."
        )
    wf_concurrency = _concurrency(dtflow.get("concurrency"), "extra.dtflow.concurrency")
    frontend = extra.get("frontendVersion")
    if frontend is not None and not isinstance(frontend, str):
        raise WorkflowError("extra.frontendVersion must be text.")
    state = _keys(top["state"], _STATE_KEYS, _STATE_KEYS, "state")
    for key in _STATE_KEYS:
        _int(state[key], f"state.{key}")
    if "id" in top and not isinstance(top["id"], str):
        raise WorkflowError("Workflow id must be text.")
    if not isinstance(top["nodes"], list):
        raise WorkflowError("nodes must be an array.")
    if not isinstance(top["links"], list):
        raise WorkflowError("links must be an array.")
    if len(top["nodes"]) > MAX_NODES:
        raise WorkflowError(f"A workflow may hold at most {MAX_NODES} nodes.")

    graph = Graph(
        workflow_id=str(top.get("id", "")),
        view=extra.get("ds"),
        last_link_id=state["lastLinkId"],
        frontend_version=frontend,
        concurrency=wf_concurrency,
    )
    raw_nodes: dict[int, dict] = {}
    for index, raw in enumerate(top["nodes"]):
        where = f"nodes[{index}]"
        node = _keys(raw, _NODE_KEYS, _NODE_KEYS - {"inputs", "outputs", "title", "color", "bgcolor"}, where)
        node_id = _int(node["id"], f"{where}.id", 1)
        if node_id in raw_nodes:
            raise WorkflowError(f"Node id {node_id} is used twice.")
        spec = CATALOG.get(node["type"]) if isinstance(node["type"], str) else None
        if spec is None:
            raise WorkflowError(f"Node #{node_id} has unsupported type {node['type']!r}.")
        where = f"{spec.title} #{node_id}"
        if type(node["mode"]) is not int or node["mode"] != 0:
            raise WorkflowError(f"{where}: muted or bypassed nodes (mode {node['mode']!r}) are not supported.")
        if node["flags"] not in ({}, {"collapsed": False}, {"collapsed": True}):
            raise WorkflowError(f"{where}: unsupported flags {node['flags']!r}.")
        properties = _keys(
            node["properties"],
            {
                "Node name for S&R",
                PROP_CONCURRENCY,
                PROP_OUTPUTS,
                PROP_PROMPT_MODE,
                PROP_PROMPT_COUNT,
                PROP_FACE_METHOD,
                PROP_FACE_EXPANSION,
            },
            set(),
            f"{where} properties",
        )
        if properties.get("Node name for S&R", spec.type) != spec.type:
            raise WorkflowError(f"{where}: unsupported properties.")
        nd_concurrency = _concurrency(properties.get(PROP_CONCURRENCY), f"{where}: {PROP_CONCURRENCY}")
        if PROP_OUTPUTS in properties and spec.type not in IMAGE_OPERATIONS:
            raise WorkflowError(f"{where}: {PROP_OUTPUTS} applies to Inpaint and Img2img nodes only.")
        nd_outputs = _outputs(properties.get(PROP_OUTPUTS), f"{where}: {PROP_OUTPUTS}")
        prompt_mode, prompt_count = _prompt_settings(properties, spec.type, nd_outputs, where)
        face = _load_face_options(properties, spec.type, where)
        for key in ("title", "color", "bgcolor"):
            if key in node:
                raise WorkflowError(f"{where}: custom '{key}' is not supported by DTFlow workflows.")
        _int(node["order"], f"{where} order")
        values = node["widgets_values"]
        if not isinstance(values, list):
            raise WorkflowError(f"{where}: widgets_values must be an array (DTFlow contract {CONTRACT_VERSION}).")
        if len(values) != len(spec.params):
            raise WorkflowError(f"{where}: widgets_values has {len(values)} entries, expected {len(spec.params)}.")
        params = {
            param.name: check_param(param, value, f"{where}: ", draft=draft)
            for param, value in zip(spec.params, values, strict=True)
        }
        _check_ports(node.get("inputs", []), spec.inputs, f"{where} inputs", is_input=True)
        _check_ports(node.get("outputs", []), spec.outputs, f"{where} outputs", is_input=False)
        raw_nodes[node_id] = node
        graph_node = GraphNode(
            node_id,
            spec.type,
            _pair(node["pos"], f"{where} pos"),
            params,
            _pair(node["size"], f"{where} size"),
            concurrency=nd_concurrency,
            outputs=nd_outputs,
            prompt_mode=prompt_mode,
            prompt_count=prompt_count,
            face=face,
        )
        if spec.type in GENERATIVE:
            _check_request(graph_node, draft=draft)
        graph.nodes.append(graph_node)

    link_ids: set[int] = set()
    targets: set[tuple[int, int]] = set()
    for index, raw in enumerate(top["links"]):
        if isinstance(raw, list):
            raise WorkflowError("Array links are the older 0.4 format; DTFlow reads version 1 object links only.")
        link = _keys(raw, _LINK_KEYS | {"parentId"}, _LINK_KEYS, f"links[{index}]")
        if link.get("parentId") is not None:
            raise WorkflowError(f"Link {link['id']}: reroutes are not supported by DTFlow workflows.")
        link_id = _int(link["id"], f"links[{index}].id", 1)
        if link_id in link_ids:
            raise WorkflowError(f"Link id {link_id} is used twice.")
        link_ids.add(link_id)
        if type(link["origin_id"]) is not int or type(link["target_id"]) is not int:
            raise WorkflowError(f"Link {link_id} refers to a missing node.")
        origin, target = raw_nodes.get(link["origin_id"]), raw_nodes.get(link["target_id"])
        if origin is None or target is None:
            raise WorkflowError(f"Link {link_id} refers to a missing node.")
        origin_ports, target_ports = CATALOG[origin["type"]].outputs, CATALOG[target["type"]].inputs
        o_slot, t_slot = link["origin_slot"], link["target_slot"]
        if not (type(o_slot) is int and 0 <= o_slot < len(origin_ports)):
            raise WorkflowError(f"Link {link_id}: origin slot {o_slot!r} does not exist.")
        if not (type(t_slot) is int and 0 <= t_slot < len(target_ports)):
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
    allowed = {"name", "type", "link", "localized_name"} if is_input else {"name", "type", "links", "slot_index"}
    if not is_input:
        allowed.add("localized_name")
    for slot, (port, spec) in enumerate(zip(ports, expected, strict=True)):
        port = _keys(port, allowed, {"name", "type"}, f"{where}[{slot}]")
        if port["name"] != spec.name or port["type"] != spec.type:
            raise WorkflowError(f"{where}[{slot}] must be {spec.name}:{spec.type}; got {port['name']}:{port['type']}.")
        if "localized_name" in port and port["localized_name"] != spec.name:
            raise WorkflowError(f"{where}[{slot}] localized_name must be {spec.name}.")
        if not is_input and port.get("slot_index", slot) != slot:
            raise WorkflowError(f"{where}[{slot}] slot_index must be {slot}.")
        if is_input and port.get("link") is not None and type(port["link"]) is not int:
            raise WorkflowError(f"{where}[{slot}] link must be a link id or null.")
        links = port.get("links")
        if not is_input and links is not None and not (isinstance(links, list) and all(type(x) is int for x in links)):
            raise WorkflowError(f"{where}[{slot}] links must be an array of link ids or null.")


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


def would_cycle(graph: Graph, origin_id: int, target_id: int) -> bool:
    """True when a new origin -> target link would close a cycle (editor connection check)."""
    if origin_id == target_id:
        return True
    stack, seen = [target_id], set()
    while stack:
        current = stack.pop()
        if current == origin_id:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(graph.children(current))
    return False


def dump_workflow(graph: Graph) -> dict:
    """Write ComfyUI v1.0 with widgets_values arrays, ``extra.dtflow`` and stable positive link ids."""
    order = {node_id: index for index, node_id in enumerate(topological_order(graph))}
    links = sorted(graph.links, key=lambda link: (order[link.target_id], link.target_slot))
    ids = [_int(link.id, "Link id", 1) for link in links]
    if len(set(ids)) != len(ids):
        raise WorkflowError("Link ids must be unique.")
    nodes = []
    for node in sorted(graph.nodes, key=lambda n: n.id):
        spec = CATALOG[node.type]
        entry: dict[str, Any] = {
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
        if node.concurrency is not None:
            entry["properties"][PROP_CONCURRENCY] = node.concurrency
        if node.outputs != 1:
            entry["properties"][PROP_OUTPUTS] = _outputs(node.outputs, f"{node.label}: {PROP_OUTPUTS}")
        if node.prompt_mode is not None:
            entry["properties"][PROP_PROMPT_MODE] = node.prompt_mode
            if node.prompt_count is not None:
                entry["properties"][PROP_PROMPT_COUNT] = node.prompt_count
        if node.type == DETECT_FACE:
            _dump_face_options(node, entry["properties"])
        entry["widgets_values"] = [_copy(node.params[param.name]) for param in spec.params]
        nodes.append(entry)
    extra: dict = {"dtflow": {"schema": SCHEMA, "version": CONTRACT_VERSION}}
    if graph.concurrency is not None:
        extra["dtflow"]["concurrency"] = graph.concurrency
    if graph.view is not None:
        extra["ds"] = graph.view
    if graph.frontend_version is not None:
        extra["frontendVersion"] = graph.frontend_version
    return {
        "id": graph.workflow_id or str(uuid.uuid4()),
        "revision": 0,
        "version": COMFY_FORMAT_VERSION,
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


# --- fan-out plan (D3) -------------------------------------------------------------------------------------------

Branch = tuple[int, int]  # (originating DTFGenerate node id, encoded image index; see below)

# An Inpaint/Img2img node with ``outputs`` > 1 splits each incoming branch into that many child branches (#276). The
# stored branch stays a (node id, integer) pair: the integer is a mixed-radix path. Digit 0 is the Generate image
# index (0..ROOT_RADIX-1); each further digit is the 1-based output number chosen by one multiplying node on the way
# (1..MAX_OUTPUTS, radix OUTPUT_RADIX). A child never collides with a root branch or a sibling at another depth, and a
# workflow without multiplying nodes keeps the plain index of every earlier run.
#
# That path names no node, so two unrelated multiplying nodes on the same images would share child identities. A
# workflow with such parallel splitters (#302) therefore uses the node-qualified form: the integer is stored NEGATIVE
# (-1 - path) and each further digit is ``slot * MAX_OUTPUTS + output + 1`` (radix QUALIFIED_RADIX), where ``slot`` is the
# 0-based rank of the splitting node among the workflow's multiplying nodes by node id. A non-negative integer is always
# read the old way, so records of earlier runs keep their meaning; Generate images keep their plain index in both forms.
ROOT_RADIX = 16  # Generate makes at most batch_size x batch_count = 16 images
OUTPUT_RADIX = MAX_OUTPUTS + 1
MAX_SPLIT_NODES = 16  # multiplying nodes a workflow may hold once its branches are node-qualified
QUALIFIED_RADIX = MAX_SPLIT_NODES * MAX_OUTPUTS + 1

# Wide form (#317): a node that makes up to MAX_WIDE_OUTPUTS images per incoming image (wildcard prompts) does not fit
# the digits above. Such a workflow is planned with the same two forms at a wider radix, kept in a disjoint integer
# range so no earlier record changes meaning: plain wide is ``WIDE_BASE + path`` (radix WIDE_OUTPUT_RADIX) and
# node-qualified wide is ``-1 - (WIDE_BASE + path)`` (radix WIDE_QUALIFIED_RADIX). The form is read from the integer's
# range, never from a stored flag. Generate images keep their plain index; the first split converts it, and parent_branch
# converts back. WIDE_BASE exceeds every legacy value (at most 8 multiplying levels inside the 256 generation limit),
# and a path that would pass WIDE_LIMIT (SQLite integers are 64 bit) is refused.
MAX_WIDE_OUTPUTS = 16
WIDE_OUTPUT_RADIX = MAX_WIDE_OUTPUTS + 1
WIDE_QUALIFIED_RADIX = MAX_SPLIT_NODES * MAX_WIDE_OUTPUTS + 1
WIDE_BASE = 2**60
WIDE_LIMIT = 2**62


def _form(index: int) -> tuple[bool, bool, int, int, int]:
    """(qualified, wide, per-slot output limit, radix, path value) of a branch integer."""
    qualified = index < 0
    value = -1 - index if qualified else index
    wide = value >= WIDE_BASE
    if wide:
        value -= WIDE_BASE
    if qualified:
        limit, radix = (MAX_WIDE_OUTPUTS, WIDE_QUALIFIED_RADIX) if wide else (MAX_OUTPUTS, QUALIFIED_RADIX)
    else:
        limit, radix = (MAX_WIDE_OUTPUTS, WIDE_OUTPUT_RADIX) if wide else (MAX_OUTPUTS, OUTPUT_RADIX)
    return qualified, wide, limit, radix, value


def _path(index: int) -> tuple[int, list[int]]:
    """(Generate image index, raw digits from the first multiplying node to the last)."""
    _, _, _, radix, value = _form(index)
    root, rest = value % ROOT_RADIX, value // ROOT_RADIX
    digits = []
    while rest:
        digits.append(rest % radix)
        rest //= radix
    return root, digits


def _output_number(index: int, digit: int) -> int:
    """The 1-based output number a digit of this branch integer stands for."""
    qualified, _, limit, _, _ = _form(index)
    return (digit - 1) % limit + 1 if qualified else digit


def _encode(qualified: bool, wide: bool, value: int) -> int:
    if wide:
        value += WIDE_BASE
        if value >= WIDE_LIMIT:
            raise ValueError("The workflow splits images into too many branches to number them.")
    return -1 - value if qualified else value


def child_branch(parent: Branch, output: int, slot: int | None = None, wide: bool = False) -> Branch:
    """The branch of output number ``output`` (0-based) of the node that splits ``parent``.

    ``slot`` is the splitting node's rank among the workflow's multiplying nodes; given, the child is node-qualified
    (#302). A node-qualified workflow qualifies every split, so a legacy child of a qualified parent is refused.
    ``wide`` selects the wide form (#317) for a first split; a deeper split always follows its parent's form."""
    _, digits = _path(parent[1])
    qualified, parent_wide, _, radix, value = _form(parent[1])
    if digits:
        if wide and not parent_wide:
            raise ValueError("A legacy branch cannot be split in the wide form.")
        wide = parent_wide
    if slot is None:
        if qualified:
            raise ValueError("A node-qualified branch can only be split with a node slot.")
        radix = WIDE_OUTPUT_RADIX if wide else OUTPUT_RADIX
        digit = output + 1
    else:
        if not qualified and digits:
            raise ValueError("A legacy branch cannot be split with a node slot.")
        limit = MAX_WIDE_OUTPUTS if wide else MAX_OUTPUTS
        radix = WIDE_QUALIFIED_RADIX if wide else QUALIFIED_RADIX
        digit = slot * limit + output + 1
    if output >= (MAX_WIDE_OUTPUTS if wide else MAX_OUTPUTS):
        raise ValueError("A node makes too many outputs per image.")
    return (parent[0], _encode(slot is not None, wide, value + digit * ROOT_RADIX * radix ** len(digits)))


def parent_branch(branch: Branch) -> Branch | None:
    """The branch this one was split from, or None for a Generate image."""
    _, digits = _path(branch[1])
    if not digits:
        return None
    qualified, wide, _, radix, value = _form(branch[1])
    value -= digits[-1] * ROOT_RADIX * radix ** (len(digits) - 1)
    if len(digits) == 1:
        return (branch[0], value)  # back to the plain Generate index, whatever form the split used
    return (branch[0], _encode(qualified, wide, value))


def output_index(branch: Branch) -> int:
    """0-based output number of the last multiplying node on this branch (0 for a Generate image)."""
    _, digits = _path(branch[1])
    return _output_number(branch[1], digits[-1]) - 1 if digits else 0


def is_descendant(branch: Branch, ancestor: Branch) -> bool:
    """True for ``ancestor`` itself and every branch split (at any depth) from it."""
    current: Branch | None = branch
    while current is not None:
        if current == ancestor:
            return True
        current = parent_branch(current)
    return False


def source_branch(plan: WorkflowPlan, node_id: int, branch: Branch) -> Branch | None:
    """The branch of ``node_id`` (an ancestor) that ``branch`` descends from: ``branch`` itself or the nearest parent."""
    known = plan.nodes[node_id].branches
    current: Branch | None = branch
    while current is not None and current not in known:
        current = parent_branch(current)
    return current


def branch_label(branch: Branch) -> str:
    root, digits = _path(branch[1])
    numbers = (_output_number(branch[1], digit) for digit in digits)
    return f"Generate #{branch[0]} image {root + 1}" + "".join(f".{number}" for number in numbers)


@dataclass(frozen=True)
class NodePlan:
    node_id: int
    type: str
    branches: tuple[Branch, ...]  # the image branches this node runs for, in order
    generations: int  # submissions this node makes over all its branches (0 for local nodes)


@dataclass(frozen=True)
class WorkflowPlan:
    order: tuple[int, ...]
    nodes: dict[int, NodePlan]
    total_generations: int  # the planned submission total shown before the run (D3, loop guard base)
    local_analyses: int
    needs_face_models: bool
    final_images: int = 0  # images the Save nodes keep (retry attempts are never part of the plan)

    def summary(self) -> str:
        noun = "generation" if self.total_generations == 1 else "generations"
        text = f"{self.total_generations} {noun}"
        if self.final_images:
            text += f" making {self.final_images} saved image{'s' if self.final_images != 1 else ''}"
        if self.local_analyses:
            text += f" and {self.local_analyses} local face detection" + ("s" if self.local_analyses != 1 else "")
        return text


def generate_outputs(node: GraphNode, prompt_counts: Mapping[int, int] | None = None) -> int:
    """Images a Generate node makes: its batch product, or the prompt count of a wildcard mode (#317)."""
    return prompt_branches(node, prompt_counts) or int(node.params["batch_size"]) * int(node.params["batch_count"])


def prompt_branches(node: GraphNode, prompt_counts: Mapping[int, int] | None = None) -> int:
    """Prompts a wildcard node draws per incoming image; 0 when its prompt is literal.

    ``sample`` knows its count; ``all`` is the number of combinations, which only the wildcard lists can tell, so the
    caller supplies it (``prompt_counts``, node id -> combinations) and planning refuses a missing or too large one."""
    if node.prompt_mode is None:
        return 0
    if node.prompt_mode == "sample":
        return int(node.prompt_count or 1)
    found = (prompt_counts or {}).get(node.id)
    if found is None:
        raise WorkflowError(
            f"{node.label}: the number of prompt combinations is not known yet (read the wildcard lists)."
        )
    if not 1 <= found <= MAX_PROMPT_COUNT:
        raise WorkflowError(
            f"{node.label}: the prompt makes {found} combinations; one node can make 1 to {MAX_PROMPT_COUNT}. "
            "Use the random sample mode or simplify the prompt."
        )
    return found


def fan_out(node: GraphNode, prompt_counts: Mapping[int, int] | None = None) -> int:
    """Outputs per incoming image of an Inpaint/Img2img node: its prompt count, else its outputs setting."""
    return prompt_branches(node, prompt_counts) or node.outputs


def plan_workflow(graph: Graph, prompt_counts: Mapping[int, int] | None = None) -> WorkflowPlan:
    """Validate the graph for running and compute per-node branches and the planned generation total.

    Requires every input connected, a checkpoint on every generative node, at least one Save node, same-branch
    pairing on converging inputs and settings that pass ``ResolvedGenerationRequest`` validation. Raises
    WorkflowError naming the node.
    """
    try:
        return _plan(graph, prompt_counts, qualified=False)
    except _ParallelSplit:  # unrelated multiplying nodes share images: plan again with node-qualified branches (#302)
        return _plan(graph, prompt_counts, qualified=True)


class _ParallelSplit(Exception):
    """Internal: two unrelated multiplying nodes split the same images, so plain branch paths would be ambiguous."""


def _plan(graph: Graph, prompt_counts: Mapping[int, int] | None, *, qualified: bool) -> WorkflowPlan:
    if not graph.nodes:
        raise WorkflowError("The workflow is empty.")
    order = topological_order(graph)
    if not any(node.type == SAVE for node in graph.nodes):
        raise WorkflowError("Add a Save image node to mark the workflow result.")
    plans: dict[int, NodePlan] = {}
    splitters: list[int] = []  # nodes with several outputs per image planned so far
    slots: dict[int, int] = {}
    if qualified:  # the rank of each multiplying node by node id is the node part of its children's identity
        multiplying = sorted(n.id for n in graph.nodes if n.type in IMAGE_OPERATIONS and fan_out(n, prompt_counts) > 1)
        if len(multiplying) > MAX_SPLIT_NODES:
            raise WorkflowError(
                f"A workflow with parallel multi-output nodes can hold at most {MAX_SPLIT_NODES} Inpaint or "
                "Img2Img nodes with several outputs."
            )
        slots = {node_id: rank for rank, node_id in enumerate(multiplying)}
    wide = any(n.type in IMAGE_OPERATIONS and fan_out(n, prompt_counts) > MAX_OUTPUTS for n in graph.nodes)
    total = analyses = 0
    for node_id in order:
        node = graph.node(node_id)
        spec = node.spec
        for param in spec.params:
            check_param(param, node.params.get(param.name), f"{node.label}: ")
        if node.type == DETECT_FACE:
            _face_settings(node)
        if node.type == GENERATE:
            node_request(node, Sampler(node.params["sampler"]))  # range/semantic checks through request validation
            images = generate_outputs(node, prompt_counts)
            _check_bound(total, images, node)
            branches = tuple((node.id, index) for index in range(images))
        else:
            inputs = []
            for slot, port in enumerate(spec.inputs):
                link = graph.input_link(node.id, slot)
                if link is None:
                    raise WorkflowError(f"{node.label}: input '{port.name}' is not connected.")
                inputs.append(plans[link.origin_id].branches)
            branches = inputs[0]
            if any(other != branches for other in inputs[1:]):
                raise WorkflowError(
                    f"{node.label}: its image and mask must come from the same generated images (same branch); "
                    "DTFlow never pairs images from different Generate nodes, or the outputs of two different "
                    "multi-output nodes (equal variation numbers are not the same image)."
                )
            if node.type in IMAGE_OPERATIONS:
                node_request(node, Sampler(node.params["sampler"]), width=1024, height=1024)
                outputs = fan_out(node, prompt_counts)
                if outputs > 1:  # each incoming image becomes ``outputs`` branches (#276, #317)
                    _check_bound(total, len(branches) * outputs, node)  # before the branches are built
                    split_slot = slots[node.id] if qualified else None
                    try:
                        branches = tuple(
                            child_branch(parent, k, split_slot, wide) for parent in branches for k in range(outputs)
                        )
                    except ValueError as exc:
                        raise WorkflowError(f"{node.label}: {exc}") from exc
                    if not qualified:
                        _check_split_lineage(graph, plans, splitters, node, branches)
                    splitters.append(node.id)
        generations = len(branches) if node.type in GENERATIVE else 0
        _check_bound(total, generations, node)
        if node.type == DETECT_FACE:
            analyses += len(branches)
        total += generations
        plans[node.id] = NodePlan(node.id, node.type, branches, generations)
    final = sum(len(p.branches) for p in plans.values() if p.type == SAVE)
    return WorkflowPlan(tuple(order), plans, total, analyses, any(n.type == DETECT_FACE for n in graph.nodes), final)


def _check_split_lineage(
    graph: Graph, plans: dict[int, NodePlan], earlier: list[int], node: GraphNode, branches
) -> None:
    """Detect two unrelated nodes that both split the same images (#276): a plain branch is (Generate, mixed-radix
    path) and names no node, so their children would share identities. The workflow is then planned again with
    node-qualified branches (#302). A node splitting the output of another one (a chain) has a longer path and stays
    unambiguous in the plain form."""
    for other_id in earlier:
        if set(plans[other_id].branches) & set(branches) and not (
            would_cycle(graph, other_id, node.id) or would_cycle(graph, node.id, other_id)
        ):
            raise _ParallelSplit


def _check_bound(total: int, adding: int, node: GraphNode) -> None:
    """Refuse a plan whose generations across every stage exceed the limit, as soon as a node crosses it, so an
    oversized multiplication is never built (or submitted)."""
    if total + adding > MAX_PLANNED_GENERATIONS:
        raise WorkflowError(
            f"{node.label}: the workflow would run at least {total + adding} generations in total "
            f"(Generate, Inpaint and Img2Img steps all count, once per image they receive); "
            f"one run is limited to {MAX_PLANNED_GENERATIONS}. Lower an image count or an outputs-per-image value."
        )


def _check_request(node: GraphNode, *, draft: bool) -> None:
    """The shared request rules across a node's settings (for example batch size × count ≤ the output maximum), so a
    file the runner could never submit is rejected at the boundary. Image operations are checked at a nominal size
    (they run at the source size); a draft may still leave its checkpoint empty."""
    params = dict(node.params)
    if draft and not str(params.get("checkpoint") or "").strip():
        params["checkpoint"] = "draft.ckpt"
    size = None if node.type == GENERATE else 512
    probe = GraphNode(node.id, node.type, node.pos, params, node.size)
    node_request(probe, width=size, height=size)


def node_request(
    node: GraphNode,
    sampler: Sampler | None = None,
    *,
    width: int | None = None,
    height: int | None = None,
    request_id: str | None = None,
    evidence: tuple[tuple[str, str], ...] = (),
    seed: int | None = None,
    single: bool = False,
    prompt: str | None = None,
) -> ResolvedGenerationRequest:
    """The txt2img settings base of a generative node, validated by ``ResolvedGenerationRequest``.

    Image operations pass the source image's size as ``width``/``height``; the image-operation entry then attaches
    the prepared inputs and switches the operation. ``seed`` overrides the node's seed; ``prompt`` the node's prompt (the resolved wildcard prompt, #317); ``single`` makes one image
    (batch size 1 x count 1), the request of one Generate output (#275). Raises WorkflowError naming the node.
    """
    params = node.params
    if node.type not in GENERATIVE:
        raise WorkflowError(f"{node.label} does not generate.")
    try:
        return ResolvedGenerationRequest(
            request_id or uuid4().hex,
            str(params["prompt"]) if prompt is None else prompt,
            str(params["checkpoint"]),
            int(params["seed"]) if seed is None else seed,
            negative_prompt=str(params["negative_prompt"]),
            width_px=int(params["width"]) if width is None else width,
            height_px=int(params["height"]) if height is None else height,
            steps=int(params["steps"]),
            guidance=float(params["guidance"]),
            sampler_id=sampler or Sampler(params["sampler"]),
            batch_size=1 if single else int(params.get("batch_size", 1)),
            batch_count=1 if single else int(params.get("batch_count", 1)),
            loras=tuple(LoRASelection(name, float(weight)) for name, weight in params["loras"]),
            resolution_evidence=(("source", "workflow"), ("workflow_node", str(node.id)), *evidence),
        )
    except ValidationError as exc:
        raise WorkflowError(f"{node.label}: {exc}") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowError(f"{node.label}: settings are incomplete.") from exc


def summary(
    node_type: str,
    params: dict[str, object],
    outputs: int = 1,
    face: FaceOptions | None = None,
) -> str:
    """Short on-node text so the canvas shows configuration without opening the inspector."""
    if node_type == DETECT_FACE:
        face = face or FaceOptions()
        method = face.method or params["mask_region"]
        if method in GEOMETRIC_FACE_METHODS:
            name = "Fast ellipse" if method == "fast" else "Balanced ellipse"
            target = "face" if method == "fast" else "person"
            return f"{name} ×{face.expansion:g} · {target}: {params['person']}"
        region = "face skin + hair" if method == "face_skin_and_hair" else "face skin"
        return f"{region} · person: {params['person']}"
    if node_type == SAVE:
        return f"label: {params['label']}"
    lines = [str(params["checkpoint"]) or "No checkpoint"]
    loras = params["loras"]
    lines.append(f"{len(loras)} LoRA" + ("s" if len(loras) != 1 else "") if loras else "no LoRA")
    detail = f"{params['steps']} steps · cfg {float(params['guidance']):g}"
    if "strength" in params:
        detail += f" · str {float(params['strength']):.2f}"
    if outputs > 1:
        detail += f" · ×{outputs} per image"
    if node_type == GENERATE:
        count = int(params["batch_size"]) * int(params["batch_count"])
        detail = f"{params['width']}×{params['height']} · " + detail + (f" · ×{count}" if count > 1 else "")
    lines.append(detail)
    return "\n".join(lines)


def new_node(graph: Graph, node_type: str, pos: tuple[float, float]) -> GraphNode:
    """Append a node of ``node_type`` with default parameters and the next free id."""
    spec = CATALOG.get(node_type)
    if spec is None:
        raise WorkflowError(f"Unknown node type {node_type!r}.")
    node = GraphNode(
        graph.next_node_id(),
        node_type,
        (float(pos[0]), float(pos[1])),
        spec.defaults(),
        face=default_face_options(node_type),
    )
    graph.nodes.append(node)
    return node


def connect(graph: Graph, origin_id: int, origin_slot: int, target_id: int, target_slot: int) -> GraphLink:
    """Add a typed link, replacing any link already on that input; rejects type mismatches and cycles."""
    origin, target = graph.node(origin_id), graph.node(target_id)
    try:
        out_port = origin.spec.outputs[origin_slot]
        in_port = target.spec.inputs[target_slot]
    except IndexError as exc:
        raise WorkflowError("That port does not exist.") from exc
    if out_port.type != in_port.type:
        raise WorkflowError(f"{out_port.type} output cannot feed {in_port.type} input.")
    if would_cycle(graph, origin_id, target_id):
        raise WorkflowError("That connection would make a cycle.")
    graph.links = [x for x in graph.links if not (x.target_id == target_id and x.target_slot == target_slot)]
    link = GraphLink(graph.next_link_id(), origin_id, origin_slot, target_id, target_slot, out_port.type)
    graph.links.append(link)
    graph.last_link_id = link.id
    return link


def remove_node(graph: Graph, node_id: int) -> None:
    graph.nodes = [node for node in graph.nodes if node.id != node_id]
    graph.links = [x for x in graph.links if node_id not in (x.origin_id, x.target_id)]


def _copy(value: object) -> object:
    return [list(item) if isinstance(item, list) else item for item in value] if isinstance(value, list) else value


def copy_params(params: dict[str, object]) -> dict[str, object]:
    return {name: _copy(value) for name, value in params.items()}


def copy_graph(graph: Graph) -> Graph:
    return load_workflow(dump_workflow(graph), draft=True)

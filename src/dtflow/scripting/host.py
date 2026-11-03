"""Tier A host hooks: the parts of upstream ``ScriptExecutor`` that generate, look things up and touch files.

``ScriptHost`` builds the hook table for ``run_script``. Hooks run on short-lived threads in the parent process (never the
Qt GUI thread, never the JavaScript child); work that needs the asyncio service loop is handed to ``loop`` and waited for
there. Rules kept here:

* A ``generateImage`` call becomes one ``ResolvedGenerationRequest`` through the normal generation path
  (``run_generation``), after its attempt id has been linked in the ScriptRun ledger and before anything is submitted.
* Stop only stops *waiting*. The in-flight attempt is never cancelled remotely, never retried and stays durable; the
  caller drains ``pending`` before closing the service.
* An unresolved outcome blocks further generation in the same run.
* Configuration fields that DTFlow cannot honour yet fail the affected call when a script *changes* them from the
  baseline it was given. Unchanged inherited fields are allowed. Nothing is silently dropped.
* Files live in one per-run sandbox directory; ``picturesPath`` and friends point there, never at the real Pictures
  folder.
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import secrets
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from dtflow.core.models import (
    SEED_LIMIT,
    BackendError,
    LoRASelection,
    RemoteOutcome,
    ResolvedGenerationRequest,
    Sampler,
)
from dtflow.scripting.models import (
    CONFIGURATION_FIELDS,
    decode_configuration,
    normalize_configuration,
    round_dimensions,
)
from dtflow.scripting.runner import ScriptCancelled
from dtflow.scripting.forms import initial_answers as _initial_answers
from dtflow.scripting.forms import validate_answers, validate_components
from dtflow.services.session import GenerationOutcome, run_generation

#: Upstream ``SamplerType`` numbers (vendored JS) <-> DTFlow sampler ids.
SAMPLER_NUMBERS: dict[int, Sampler] = {
    0: Sampler.DPMPP_2M_KARRAS,
    1: Sampler.EULER_A,
    2: Sampler.DDIM,
    3: Sampler.PLMS,
    4: Sampler.DPMPP_SDE_KARRAS,
    5: Sampler.UNIPC,
    6: Sampler.LCM,
    7: Sampler.EULER_A_SUBSTEP,
    8: Sampler.DPMPP_SDE_SUBSTEP,
    9: Sampler.TCD,
    10: Sampler.EULER_A_TRAILING,
    11: Sampler.DPMPP_SDE_TRAILING,
    12: Sampler.DPMPP_2M_AYS,
    13: Sampler.EULER_A_AYS,
    14: Sampler.DPMPP_SDE_AYS,
    15: Sampler.DPMPP_2M_TRAILING,
    16: Sampler.DDIM_TRAILING,
    17: Sampler.UNIPC_TRAILING,
    18: Sampler.UNIPC_AYS,
    19: Sampler.TCD_TRAILING,
}
SAMPLER_IDS = {sampler: number for number, sampler in SAMPLER_NUMBERS.items()}

#: Configuration fields a script may change today (everything else fails the call when changed; see #115).
SUPPORTED_FIELDS = frozenset({"width", "height", "seed", "steps", "guidanceScale", "model", "sampler", "loras"})
#: Fields that never count as a change (upstream ``id`` is a host-assigned handle).
IGNORED_FIELDS = frozenset({"id"})
DEFAULT_MAX_GENERATIONS = 4
MAX_SANDBOX_LISTING = 1000
MAX_SANDBOX_FILE_BYTES = 64 * 1024 * 1024
MAX_SANDBOX_FILES = 200
PIPELINE_RUN_KEYS = frozenset({"configuration", "prompt", "negativePrompt", "mask"})
SAFE_FAILURES = (RemoteOutcome.NOT_STARTED, RemoteOutcome.FAILED, RemoteOutcome.CANCELLED_CONFIRMED)
POLL_SECONDS = 0.1
PNG_PREFIX = "data:image/png;base64,"
SCREEN_SIZE = {"width": 1280.0, "height": 800.0}  # DTFlow has no canvas screen; a fixed, documented value


def _default(kind: str) -> Any:
    return {"bool": False, "str": None, "strlist": [], "controls": [], "loras": []}.get(
        kind, 0.0 if kind == "f32" else 0
    )


def baseline_configuration(base: ResolvedGenerationRequest) -> dict[str, Any]:
    """The configuration object ``pipeline.configuration`` starts from, built from validated Generate settings.

    Fields DTFlow does not model carry neutral values (zero/false/null); they are not Draw Things' app defaults, and a
    script that changes one of them is refused (see ``SUPPORTED_FIELDS``). Never mutates the live Generate settings."""
    values: dict[str, Any] = {name: _default(kind) for name, kind, _ in CONFIGURATION_FIELDS}
    values.update(
        width=base.width_px,
        height=base.height_px,
        seed=base.seed,
        steps=base.steps,
        guidanceScale=base.guidance,
        strength=1.0,
        model=base.model_id,
        sampler=SAMPLER_IDS[base.sampler_id],
        batchCount=1,
        batchSize=1,
        clipSkip=1,
        shift=1.0,
        loras=[{"file": item.identifier, "weight": item.weight, "mode": "all"} for item in base.loras],
    )
    return values


class ScriptHost:
    """Hook implementations bound to one run. Create inside the process that owns the service and its loop."""

    def __init__(
        self,
        *,
        service,
        base: ResolvedGenerationRequest,
        loop: asyncio.AbstractEventLoop,
        link_child: Callable[[str], int],
        console: Callable[[str, int], None],
        cancel: threading.Event,
        sandbox: Path,
        max_generations: int = DEFAULT_MAX_GENERATIONS,
        prefix: bool = False,
        answers: Sequence[Sequence[Any]] | None = None,
        record_answers: Callable[[dict], int] | None = None,
        ask: Callable[[str, str, list], list | None] | None = None,
        run_id: str | None = None,
    ) -> None:
        if base.operation != "txt2img" or base.image_inputs is not None:
            raise ValueError("Scripts start from text-to-image settings only.")
        self.service, self.base, self.loop = service, base, loop
        self._link_child, self._console, self.cancel = link_child, console, cancel
        self.sandbox = Path(sandbox).resolve()
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.max_generations, self.prefix = max_generations, prefix
        self._answers = list(answers) if answers is not None else None
        self._record_answers = record_answers
        self._ask = ask  # set only for a run whose forms are answered interactively (a queue item)
        self.run_id = run_id
        self._forms = 0
        self.baseline = normalize_configuration(decode_configuration(baseline_configuration(base)).values)
        self.generations = 0
        self.last: GenerationOutcome | None = None
        self.attempt_outputs: dict[str, list[str]] = {}  # every committed attempt's output paths, in order
        self.pending: list[concurrent.futures.Future] = []  # in-flight work to drain before the service closes
        self.attempts: list[str] = []
        self._blocked: str | None = None
        # Virtual canvas for text-to-image scripts: geometry bookkeeping only, no pixels (layers and masks are #116).
        self._zoom = 1.0
        self._origin = (0, 0)
        self._canvas_size = (base.width_px, base.height_px)
        self._cleared = False

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "existingConfiguration": self.existing_configuration,
            "existingPrompts": self.existing_prompts,
            "generateImage": self.generate_image,
            "createLoRA": self.create_lora,
            "createControl": self.create_control,
            "areModelsDownloaded": self.are_models_downloaded,
            "downloadBuiltins": self.download_builtins,
            "requestFromUser": self.request_from_user,
            "screenSize": self.screen_size,
            "picturesPath": self.pictures_path,
            "listFilesUnderPictures": self.list_files_under_pictures,
            "listFilesUnderPicturesWithinDirectory": self.list_files_under_pictures_within,
            "listFilesWithinDirectory": self.list_files_within,
            "saveImageFileFromCanvas": self.save_image_file,
            "saveImageSrcFromCanvas": self.save_image_src,
            "clearCanvas": self.clear_canvas,
            "setCanvasZoom": self.set_canvas_zoom,
            "canvasZoom": self.canvas_zoom,
            "updateCanvasSize": self.update_canvas_size,
            "moveCanvas": self.move_canvas,
            "clearMoodboard": self.clear_moodboard,
        }

    # -- waiting on the service loop ------------------------------------------------------------------------------

    def _await(self, coroutine, *, track: bool = False):
        """Run ``coroutine`` on the service loop and wait. Stop abandons the wait but never cancels the work."""
        if self.cancel.is_set():
            coroutine.close()  # never started
            raise ScriptCancelled()
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        if track:
            self.pending.append(future)
        while True:
            if self.cancel.is_set():
                raise ScriptCancelled()
            try:
                return future.result(POLL_SECONDS)
            except concurrent.futures.TimeoutError:
                continue

    # -- configuration --------------------------------------------------------------------------------------------

    def existing_configuration(self) -> dict[str, Any]:
        return baseline_configuration(self.base)

    def existing_prompts(self) -> dict[str, str]:
        return {"prompt": self.base.prompt, "negativePrompt": self.base.negative_prompt}

    def _request(self, args: Mapping[str, Any]) -> ResolvedGenerationRequest:
        if args.get("mask") is not None:
            raise ValueError("pipeline.run() mask is not supported yet (virtual canvas and masks arrive with #116).")
        for key in sorted(set(args) - PIPELINE_RUN_KEYS):
            self._console(f"pipeline.run() - ignoring unknown argument {key}", 0)
        configuration = args.get("configuration")
        decoded = decode_configuration(configuration)  # raises "Missing configuration parameter" like upstream
        for key in decoded.unknown_keys:
            self._console(f"pipeline.run() - configuration contains unknown key {key}", 0)
        values, warnings = round_dimensions(decoded.values)
        for warning in warnings:
            self._console(warning, 0)
        values = normalize_configuration(values)  # upstream createGenerationConfiguration, before any comparison
        changed = sorted(
            name
            for name, value in values.items()
            if name not in IGNORED_FIELDS and name not in SUPPORTED_FIELDS and value != self.baseline[name]
        )
        if changed:
            raise ValueError(
                "DTFlow does not support changing "
                + ", ".join(f"configuration.{name}" for name in changed)
                + " yet; no image was requested."
            )
        for item in values["loras"]:
            if not item["file"]:
                raise ValueError("Every LoRA needs a file; no image was requested.")
            if item["mode"] != "all":
                raise ValueError("DTFlow supports LoRA mode 'all' only; no image was requested.")
        if not values["model"]:
            raise ValueError("configuration.model must specify a model; no image was requested.")
        number = values["sampler"]
        if number not in SAMPLER_NUMBERS:
            raise ValueError(f"configuration.sampler {number} is not a known sampler; no image was requested.")
        seed = values["seed"]
        evidence = list(self.base.resolution_evidence)
        evidence += [("source", "script"), ("script_call", str(self.generations + 1))]
        if self.run_id:
            evidence.append(("script_run", self.run_id))  # "created by script X, step N" resolves without scanning runs
        if seed < 0:
            seed = secrets.randbelow(SEED_LIMIT + 1)
            evidence.append(("field.seed", "script_random"))
        prompt = args.get("prompt")
        negative = args.get("negativePrompt")
        selected = tuple(LoRASelection(item["file"], item["weight"]) for item in values["loras"])
        chosen = {item.identifier for item in selected}
        if self.base.prepared_assets and not chosen <= {item.identifier for item in self.base.loras}:
            raise ValueError(
                "This backend needs prepared assets for LoRAs; a script may not add a LoRA that has none. "
                "No image was requested."
            )
        assets = tuple(item for item in self.base.prepared_assets if item.identifier in chosen)
        return ResolvedGenerationRequest(
            uuid.uuid4().hex,
            self.base.prompt if prompt is None else prompt,
            values["model"],
            seed,
            negative_prompt=self.base.negative_prompt if negative is None else negative,
            width_px=values["width"],
            height_px=values["height"],
            steps=values["steps"],
            guidance=values["guidanceScale"],
            sampler_id=SAMPLER_NUMBERS[number],
            loras=selected,
            prepared_assets=assets,
            resolution_evidence=tuple(evidence),
            allow_unlisted_model=self.base.allow_unlisted_model,
        )

    def generate_image(self, args: Mapping[str, Any]) -> None:
        if not isinstance(args, Mapping):
            raise ValueError("Missing configuration parameter")
        if self._blocked:
            raise RuntimeError(self._blocked)
        if self.generations >= self.max_generations:
            raise RuntimeError(f"Script reached its generation limit ({self.max_generations}); no image was requested.")
        request = self._request(args)
        if self.cancel.is_set():
            raise ScriptCancelled()  # Stop landed after the dispatcher's check: nothing is linked or scheduled
        attempt_id = uuid.uuid4().hex
        self._link_child(attempt_id)  # recorded before anything can be submitted
        self.generations += 1
        self.attempts.append(attempt_id)
        try:
            outcome = self._await(
                run_generation(self.service, request, prefix=self.prefix, attempt_id=attempt_id), track=True
            )
        except ScriptCancelled:
            self._blocked = "Stopped; an earlier generation was left to finish on its own."
            raise
        except BackendError as exc:
            if exc.failure.remote_outcome not in SAFE_FAILURES:
                # unknown outcome, or a paid result whose save failed: never generate again in this run
                self._blocked = "A previous generation's outcome is unresolved; not generating again."
            raise RuntimeError(f"{exc.failure.code}: {exc.failure.message}") from exc
        except Exception as exc:
            self._blocked = "A previous generation did not finish cleanly; not generating again."
            raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
        self.last = outcome
        self._cleared = False  # a new generation fills the canvas again
        self.attempt_outputs[attempt_id] = [str(path) for path in outcome.output_paths]

    # -- catalogue lookups ----------------------------------------------------------------------------------------

    def _inventory(self):
        return self._await(self.service.get_inventory())

    def _find(self, kind: str, name: str):
        if not isinstance(name, str):
            raise ValueError("A file name is required.")
        wanted = name.strip().lower()
        for entry in self._inventory().entries:
            if entry.kind == kind and wanted in (entry.identifier.lower(), entry.display_name.lower()):
                return entry
        raise LookupError(f"{kind} '{name}' was not found in the backend inventory.")

    def create_lora(self, name: str) -> dict[str, Any]:
        entry = self._find("lora", name)
        weight = entry.default_weight if entry.default_weight is not None else 1.0
        return {"file": entry.identifier, "weight": weight, "mode": "all"}

    def create_control(self, name: str) -> dict[str, Any]:
        # Lookup only: controls cannot be dispatched until #117, and a script that uses one fails at generateImage.
        entry = self._find("control", name)
        return {
            "file": entry.identifier,
            "weight": 1.0,
            "guidanceStart": 0.0,
            "guidanceEnd": 1.0,
            "noPrompt": False,
            "globalAveragePooling": True,
            "downSamplingRate": 1.0,
            "controlImportance": "balanced",
            "inputOverride": "",
            "targetBlocks": [],
        }

    def are_models_downloaded(self, files: Sequence[str]) -> list[bool]:
        if not isinstance(files, (list, tuple)) or not all(isinstance(item, str) for item in files):
            raise ValueError("A list of file names is required.")
        known = {entry.identifier.lower() for entry in self._inventory().entries}
        return [item.lower() in known for item in files]

    def download_builtins(self, files: Sequence[str]) -> None:
        available = self.are_models_downloaded(files)
        missing = [name for name, present in zip(files, available, strict=True) if not present]
        if missing:
            raise RuntimeError("DTFlow never downloads models; not available: " + ", ".join(missing))

    # -- forms ---------------------------------------------------------------------------------------------------

    def request_from_user(self, title: str, confirm: str, components: Sequence[Mapping[str, Any]]) -> list[Any]:
        """Park on the persisted interactive bridge or consume one headless ordered answer set."""
        components = list(components)
        validate_components(components)
        source = "file" if self._answers is not None else "initial"
        if self._answers is None and self._ask is not None:
            source = "user"
            answers = self._ask(
                title if isinstance(title, str) else "", confirm if isinstance(confirm, str) else "", list(components)
            )
            if answers is None:
                raise ScriptCancelled()
            validate_answers(components, answers)
        elif self._answers is None:
            def needs_selection(fields):
                return any(
                    spec["type"] in ("imageField", "directory")
                    or (spec["type"] == "image" and spec.get("selectable"))
                    or (spec["type"] == "section" and needs_selection(spec.get("views") or []))
                    for spec in fields
                )

            if needs_selection(components):
                raise ValueError("This form needs explicit image or directory input; supply --answers or use Scripts.")
            answers = _initial_answers(components)
        else:
            if not self._answers:
                raise ValueError(
                    "This script asked for another input form but its supplied answer sets ran out. "
                    "The script was not continued."
                )
            answers = list(self._answers.pop(0))
        validate_answers(components, answers)
        if source != "user":
            # Headless files may embed raster data but cannot forge picker capabilities.
            def reject_token(path, kind, token):
                raise ValueError("Owned picker inputs require an interactive form in this run.")

            validate_answers(components, answers, origin=reject_token)
        self._forms += 1
        if self._record_answers is not None:
            # Durable before the script continues; if it cannot be recorded the call fails and the script stops here.
            self._record_answers(
                {
                    "call": self._forms,
                    "title": title if isinstance(title, str) else "",
                    "source": source,
                    "answers": answers,
                }
            )
        if source == "user":
            from dtflow.services.script_form_inputs import resolve_form_inputs

            return resolve_form_inputs(self.sandbox.parent.parent, self.run_id, self._forms, components, answers)
        return answers

    def screen_size(self) -> dict[str, float]:
        return dict(SCREEN_SIZE)

    # -- virtual canvas (geometry only; ports ScriptExecutor.swift behavior for text-to-image scripts) --------------

    def clear_canvas(self) -> None:
        """Upstream ``clearCanvas``: the canvas contents go away. Here that means a later save finds nothing."""
        self._cleared = True

    def set_canvas_zoom(self, zoom_scale: float) -> None:
        if not _is_number(zoom_scale):
            raise ValueError("canvas.canvasZoom must be a number.")
        if round(zoom_scale * 60) != zoom_scale * 60:
            self._console("Warning: setCanvasZoom parameter should be divisible by 60", 0)
        self._zoom = float(zoom_scale)

    def canvas_zoom(self) -> float:
        return self._zoom

    def update_canvas_size(self, configuration: Mapping[str, Any]) -> None:
        decoded = decode_configuration(configuration)  # raises "Missing configuration parameter" like upstream
        values, warnings = round_dimensions(decoded.values)
        for warning in warnings:
            self._console(warning, 0)
        self._canvas_size = (values["width"], values["height"])

    def move_canvas(self, x: float, y: float) -> None:
        if not (_is_number(x) and _is_number(y)):
            raise ValueError("canvas.moveCanvas needs two numbers.")
        if x != round(x) or y != round(y):
            self._console("Warning: x and y coordinates to moveCanvas should be integers", 0)
        self._origin = (int(x), int(y))

    def clear_moodboard(self) -> None:
        """DTFlow scripts never hold moodboard images yet (guidance is #117), so the moodboard is always empty."""

    # -- sandbox files -------------------------------------------------------------------------------------------

    def _confine(self, name: str) -> Path:
        if not isinstance(name, str):
            raise ValueError("A path is required.")
        candidate = Path(name)
        resolved = (candidate if candidate.is_absolute() else self.sandbox / candidate).resolve()
        if resolved != self.sandbox and self.sandbox not in resolved.parents:
            raise PermissionError("Path is outside the script's file area.")
        return resolved

    def pictures_path(self) -> str:
        return str(self.sandbox)

    def _listing(self, directory: Path) -> list[str]:
        if not directory.is_dir():
            raise FileNotFoundError("Directory does not exist in the script's file area.")
        entries = sorted(directory.iterdir())
        if len(entries) > MAX_SANDBOX_LISTING:
            self._console(f"Listing truncated to {MAX_SANDBOX_LISTING} entries.", 1)
        return [str(item) for item in entries[:MAX_SANDBOX_LISTING]]

    def list_files_under_pictures(self) -> list[str]:
        return self._listing(self.sandbox)

    def list_files_under_pictures_within(self, directory: str) -> list[str]:
        return self._listing(self._confine(directory) if directory else self.sandbox)

    def list_files_within(self, directory: str) -> list[str]:
        return self._listing(self._confine(directory))

    def _last_image(self) -> bytes:
        if self._cleared or self.last is None or self.last.final_image is None:
            raise RuntimeError("The canvas is empty: no image has been generated in this run.")
        return self.last.final_image

    def save_image_file(self, file: str, visible_region_only: bool = False) -> None:
        data = self._last_image()  # the canvas is the last committed output; cropping arrives with #116
        target = self._confine(file)
        if len(data) > MAX_SANDBOX_FILE_BYTES:
            raise ValueError("Image is too large to save.")
        if sum(1 for item in self.sandbox.rglob("*") if item.is_file()) >= MAX_SANDBOX_FILES:
            raise ValueError("The script's file area is full.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def save_image_src(self, visible_region_only: bool = False) -> str:
        data = self._last_image()
        # No image library here (contract): label the bytes by their signature; upstream accepts all three prefixes.
        if data.startswith(b"\xff\xd8\xff"):
            prefix = "data:image/jpeg;base64,"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            prefix = "data:image/webp;base64,"
        else:
            prefix = PNG_PREFIX
        return prefix + base64.b64encode(data).decode("ascii")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def starting_context(
    base: ResolvedGenerationRequest, *, backend: str, max_generations: int, answers: Sequence[Sequence[Any]] | None
) -> dict[str, Any]:
    """Validated starting settings for the ScriptRun record. Credentials, endpoints, state paths and prepared-asset
    references are deliberately left out; only the number of prepared assets is kept."""
    import hashlib
    import json

    return {
        "backend": backend,
        "model_id": base.model_id,
        "prompt": base.prompt,
        "negative_prompt": base.negative_prompt,
        "seed": base.seed,
        "width": base.width_px,
        "height": base.height_px,
        "steps": base.steps,
        "guidance": base.guidance,
        "sampler": str(base.sampler_id),
        "loras": [{"identifier": item.identifier, "weight": item.weight} for item in base.loras],
        "prepared_assets": len(base.prepared_assets),
        "max_generations": max_generations,
        "answers_supplied": None if answers is None else len(answers),
        "answers_sha256": None
        if answers is None
        else hashlib.sha256(json.dumps(answers, sort_keys=True).encode("utf-8")).hexdigest(),
    }


def script_hooks(
    service_of: Callable[[], Any],
    *,
    base: ResolvedGenerationRequest,
    loop: asyncio.AbstractEventLoop,
    state: Path,
    max_generations: int,
    answers: Sequence[Sequence[Any]] | None,
    hosts: list[ScriptHost],
    interactive: bool = False,
) -> Callable[[Any], dict[str, Callable[..., Any]]]:
    """The hook factory ``run_recorded`` takes, shared by the CLI and the queue item so both run the same host.

    ``service_of`` is read when the run starts (the service exists only once its session is open); every host built is
    appended to ``hosts`` so the caller can drain its in-flight work."""

    def hooks(context):
        host = ScriptHost(
            service=service_of(),
            base=base,
            loop=loop,
            link_child=context.link_child,
            console=context.console,
            cancel=context.cancel,
            sandbox=Path(state) / "script_files" / context.run_id,
            max_generations=max_generations,
            answers=answers,
            record_answers=context.record_answers,
            ask=context.ask if interactive else None,
            run_id=context.run_id,
        )
        hosts.append(host)
        return host.hooks()

    return hooks

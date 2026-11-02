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
import io
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
    round_dimensions,
)
from dtflow.scripting.runner import ScriptCancelled
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
    ) -> None:
        self.service, self.base, self.loop = service, base, loop
        self._link_child, self._console, self.cancel = link_child, console, cancel
        self.sandbox = Path(sandbox).resolve()
        self.sandbox.mkdir(parents=True, exist_ok=True)
        self.max_generations, self.prefix = max_generations, prefix
        self._answers = list(answers or ())
        self.baseline = decode_configuration(baseline_configuration(base)).values
        self.generations = 0
        self.last: GenerationOutcome | None = None
        self.pending: list[concurrent.futures.Future] = []  # in-flight work to drain before the service closes
        self.attempts: list[str] = []
        self._blocked: str | None = None

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
        }

    # -- waiting on the service loop ------------------------------------------------------------------------------

    def _await(self, coroutine, *, track: bool = False):
        """Run ``coroutine`` on the service loop and wait. Stop abandons the wait but never cancels the work."""
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
        configuration = args.get("configuration")
        decoded = decode_configuration(configuration)  # raises "Missing configuration parameter" like upstream
        for key in decoded.unknown_keys:
            self._console(f"pipeline.run() - configuration contains unknown key {key}", 0)
        values, warnings = round_dimensions(decoded.values)
        for warning in warnings:
            self._console(warning, 0)
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
        number = values["sampler"]
        if number not in SAMPLER_NUMBERS:
            raise ValueError(f"configuration.sampler {number} is not a known sampler; no image was requested.")
        seed = values["seed"]
        evidence = list(self.base.resolution_evidence)
        evidence += [("source", "script"), ("script_call", str(self.generations + 1))]
        if seed < 0:
            seed = secrets.randbelow(SEED_LIMIT + 1)
            evidence.append(("field.seed", "script_random"))
        prompt = args.get("prompt")
        negative = args.get("negativePrompt")
        return ResolvedGenerationRequest(
            uuid.uuid4().hex,
            self.base.prompt if prompt is None else prompt,
            values["model"] or self.base.model_id,
            seed,
            negative_prompt=self.base.negative_prompt if negative is None else negative,
            width_px=values["width"],
            height_px=values["height"],
            steps=values["steps"],
            guidance=values["guidanceScale"],
            sampler_id=SAMPLER_NUMBERS[number],
            loras=tuple(LoRASelection(item["file"], item["weight"]) for item in values["loras"]),
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
            if exc.failure.remote_outcome == RemoteOutcome.UNKNOWN:
                self._blocked = "A previous generation's outcome is unresolved; not generating again."
            raise RuntimeError(f"{exc.failure.code}: {exc.failure.message}") from exc
        except Exception as exc:
            self._blocked = "A previous generation did not finish cleanly; not generating again."
            raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
        self.last = outcome

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
        """Headless form: ordered answers come from the supplied answer sets (one per call) or from the components'
        own initial values. Sections contribute their children's answers in order; plain text, markdown and images
        contribute none. The complete form renderer is #196."""
        supplied = self._answers.pop(0) if self._answers else None
        if supplied is not None:
            return list(supplied)
        return list(_initial_answers(components))

    def screen_size(self) -> dict[str, float]:
        return dict(SCREEN_SIZE)

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
        return [str(item) for item in sorted(directory.iterdir())[:MAX_SANDBOX_LISTING]]

    def list_files_under_pictures(self) -> list[str]:
        return self._listing(self.sandbox)

    def list_files_under_pictures_within(self, directory: str) -> list[str]:
        return self._listing(self._confine(directory) if directory else self.sandbox)

    def list_files_within(self, directory: str) -> list[str]:
        return self._listing(self._confine(directory))

    def _last_image(self) -> bytes:
        if self.last is None or self.last.final_image is None:
            raise RuntimeError("The canvas is empty: no image has been generated in this run.")
        return self.last.final_image

    def save_image_file(self, file: str, visible_region_only: bool = False) -> None:
        data = self._last_image()  # the canvas is the last committed output; cropping arrives with #116
        target = self._confine(file)
        if len(data) > MAX_SANDBOX_FILE_BYTES:
            raise ValueError("Image is too large to save.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def save_image_src(self, visible_region_only: bool = False) -> str:
        data = self._last_image()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image, io.BytesIO() as buffer:
                image.save(buffer, "PNG")
                data = buffer.getvalue()
        return PNG_PREFIX + base64.b64encode(data).decode("ascii")


def _initial_answers(components: Sequence[Mapping[str, Any]]) -> list[Any]:
    answers: list[Any] = []
    for component in components:
        kind = component.get("type")
        if kind == "section":
            answers.extend(_initial_answers(component.get("views") or ()))
        elif kind in ("slider", "textField", "comboBox"):
            answers.append(component.get("value"))
        elif kind == "doubleSlider":
            answers.append(list(component.get("value") or ()))
        elif kind == "switch":
            answers.append(bool(component.get("isOn")))
        elif kind in ("segmented", "menu"):
            answers.append(component.get("index"))
        elif kind == "size":
            answers.append([component.get("width"), component.get("height")])
        elif kind == "imageField":
            answers.append([])
        elif kind == "directory":
            answers.append("")
    return answers

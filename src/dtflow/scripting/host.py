"""Tier A host hooks: the parts of upstream ``ScriptExecutor`` that generate, look things up and touch files.

``ScriptHost`` builds the hook table for ``run_script``. Hooks run on short-lived threads in the parent process (never the
Qt GUI thread, never the JavaScript child); work that needs the asyncio service loop is handed to ``loop`` and waited for
there. Rules kept here:

* A ``generateImage`` call becomes one ``ResolvedGenerationRequest`` through the normal generation path
  (``run_generation``), after its attempt id has been linked in the run (kept in its queue item) and before anything is submitted.
* Stop only stops *waiting*. The in-flight attempt is never cancelled remotely and stays durable; the caller drains
  ``pending`` before closing the service.
* A step or batch job whose failure ``core.retry_policy.auto_retryable`` accepts gets the one shared automatic retry
  (#241) after a short wait, as a new linked child, when the run still has a generation left.
* A step that fails or ends with no confirmed outcome does not block the rest of the run. In a run answered in Scripts
  (a queue item) the run pauses on that step and the person picks Retry (the same request and seed, a new linked
  child; the old step keeps its own outcome) or Continue (the script goes on with an empty canvas). Headless runs hand
  the error to the script as before. Each retry, manual or automatic, counts as one of the run's
  ``max_generations``.
* Configuration fields that DTFlow cannot honour yet fail the affected call when a script *changes* them from the
  baseline it was given. Unchanged inherited fields are allowed. Nothing is silently dropped.
* Files live in one per-run sandbox directory; ``picturesPath`` and friends point there, never at the real Pictures
  folder.
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import hashlib
import json
import re
import secrets
import threading
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from dtflow.core.models import (
    SEED_LIMIT,
    BackendError,
    ErrorCategory,
    LoRASelection,
    PreparedAsset,
    RemoteOutcome,
    ResolvedGenerationRequest,
    Sampler,
)
from dtflow.core.output_layout import OutputLayout, group_name, prompt_label
from dtflow.core.retry_policy import AUTO_RETRIES, AUTO_RETRY_DELAY, auto_retry_request, auto_retryable
from dtflow.core.tasks import settle
from dtflow.persistence.script_items import RETRY_EVIDENCE
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

#: How the Retry form and console name a step that did not complete, by kind.
HEADLINES = {"failed": "failed", "unknown": "has no confirmed outcome", "not_saved": "was generated but not saved"}
#: Configuration fields a script may change today (everything else fails the call when changed; see #115).
SUPPORTED_FIELDS = frozenset({"width", "height", "seed", "steps", "guidanceScale", "model", "sampler", "loras"})
#: Fields that never count as a change (upstream ``id`` is a host-assigned handle).
IGNORED_FIELDS = frozenset({"id"})
DEFAULT_MAX_GENERATIONS = 30
MAX_SANDBOX_LISTING = 1000
MAX_SANDBOX_FILE_BYTES = 64 * 1024 * 1024
MAX_SANDBOX_FILES = 200
PIPELINE_RUN_KEYS = frozenset({"configuration", "prompt", "negativePrompt", "mask"})
BATCH_JOB_KEYS = PIPELINE_RUN_KEYS | {"outputFile", "planId", "jobIndex"}
MAX_BATCH_CONCURRENT = 10
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
        require_start: bool = False,
        admission_gate: threading.Event | None = None,
        reserve_child: Callable[[str, int], Awaitable[bool]] | None = None,
        release_child: Callable[[str], Awaitable[None]] | None = None,
        available_assets: tuple[PreparedAsset, ...] = (),
        source_sha256: str = "",
        output_group: str | None = None,
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
        if require_start and (ask is None or answers is not None):
            raise ValueError("Setup requires an interactive form bridge without supplied answers.")
        self._start_permitted = not require_start
        self._admission_gate = admission_gate
        self.reserve_child, self.release_child = reserve_child, release_child
        self.available_assets = available_assets
        self.source_sha256 = source_sha256
        self._script_inventory = None
        self._preferences_allowed = False
        self.run_id = run_id
        self._group = output_group or group_name("script", run_id or uuid.uuid4().hex)
        self._forms = 0
        self.baseline = normalize_configuration(decode_configuration(baseline_configuration(base)).values)
        self.generations = 0
        self.last: GenerationOutcome | None = None
        self.attempt_outputs: dict[str, list[str]] = {}  # every committed attempt's output paths, in order
        self.pending: list[concurrent.futures.Future] = []  # in-flight work to drain before the service closes
        self.attempts: list[str] = []
        self.steps = 0  # generateImage calls; a retry stays the same step
        self.retries = 0
        self._blocked: str | None = None
        # Virtual canvas for text-to-image scripts: geometry bookkeeping only, no pixels (layers and masks are #116).
        self._zoom = 1.0
        self._origin = (0, 0)
        self._canvas_size = (base.width_px, base.height_px)
        self._cleared = False

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "existingConfiguration": self.existing_configuration,
            "listScriptLoRAs": self.list_script_loras,
            "scriptRunLimits": lambda: {"maxGenerations": self.max_generations},
            "loadScriptPreferences": self.load_preferences,
            "saveScriptPreferences": self.save_preferences,
            "fingerprintScriptPlan": self.plan_fingerprint,
            "existingPrompts": self.existing_prompts,
            "generateImage": self.generate_image,
            "generateBatch": self.generate_batch,
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

    def _layout(self, request: ResolvedGenerationRequest) -> OutputLayout:
        """All of this run's outputs share one folder; files are labeled from the prompt (issue #296)."""
        return OutputLayout(self._group, prompt_label(request.prompt))

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

    def _request(self, args: Mapping[str, Any], *, call_index: int | None = None) -> ResolvedGenerationRequest:
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
        evidence += [
            ("source", "script"),
            ("script_call", str(self.generations + 1 if call_index is None else call_index)),
        ]
        if self.run_id:
            evidence.append(("script_run", self.run_id))  # "created by script X, step N" resolves without scanning runs
        if seed < 0:
            seed = secrets.randbelow(SEED_LIMIT + 1)
            evidence.append(("field.seed", "script_random"))
        prompt = args.get("prompt")
        negative = args.get("negativePrompt")
        selected = tuple(LoRASelection(item["file"], item["weight"]) for item in values["loras"])
        # Like queue LoRA sets, a script can select ready assets not selected on
        # Generate. Explicit base bindings take precedence over the shared pool.
        pool = {item.identifier: item for item in (*self.available_assets, *self.base.prepared_assets)}
        assets = tuple(pool[item.identifier] for item in selected if item.identifier in pool)
        evidence = [(key, value) for key, value in evidence if key != "prepared_assets"]
        if assets:
            evidence.append(("prepared_assets", ",".join(item.receipt_id for item in assets)))
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

    def _admit_generation(self) -> None:
        """Wait for explicit Run Script consent and any window-owned preparation handoff."""
        if not self._start_permitted:
            self.request_from_user(
                "Ready to run script",
                "Run Script",
                [
                    {
                        "type": "plainText",
                        "value": "This script requests generation before an input form. Review the captured settings and press Run Script to permit generation.",
                    }
                ],
            )
        while self._admission_gate is not None and not self._admission_gate.wait(POLL_SECONDS):
            if self.cancel.is_set():
                raise ScriptCancelled()
        if self.cancel.is_set():
            raise ScriptCancelled()

    def generate_image(self, args: Mapping[str, Any]) -> None:
        self._admit_generation()
        if not isinstance(args, Mapping):
            raise ValueError("Missing configuration parameter")
        if self._blocked:
            raise RuntimeError(self._blocked)
        if self.generations >= self.max_generations:
            raise RuntimeError(f"Script reached its generation limit ({self.max_generations}); no image was requested.")
        request = self._request(args)
        if self.cancel.is_set():
            raise ScriptCancelled()  # Stop landed after the dispatcher's check: nothing is linked or scheduled
        self.generations += 1
        self.steps += 1
        step, retry_of = self.steps, None
        auto_left = AUTO_RETRIES  # the shared automatic retry (#241), per submission
        while True:
            if self.cancel.is_set():
                raise ScriptCancelled()  # Stop landed while the Retry form closed: nothing more is linked
            attempt_id = uuid.uuid4().hex
            self._link_child(attempt_id)  # recorded before anything can be submitted
            self.attempts.append(attempt_id)
            try:
                outcome = self._await(
                    run_generation(
                        self.service, request, prefix=self.prefix, attempt_id=attempt_id, layout=self._layout(request)
                    ),
                    track=True,
                )
            except ScriptCancelled:
                self._blocked = "Stopped; an earlier generation was left to finish on its own."
                raise
            except BackendError as exc:
                remote = exc.failure.remote_outcome
                kind = (
                    "failed"
                    if remote in SAFE_FAILURES
                    else "not_saved"
                    if remote == RemoteOutcome.SUCCEEDED
                    else "unknown"
                )
                reason = exc.failure.message or exc.failure.code
                automatic = auto_retryable(exc.failure)
                assets = getattr(self.service, "assets", None)
                if (
                    assets is not None
                    and request.prepared_assets
                    and exc.failure.category == ErrorCategory.REMOTE_REJECTION
                    and callable(getattr(assets, "recover_missing", None))
                ):
                    repaired = self._await(assets.recover_missing(request.prepared_assets), track=True)
                    automatic = automatic or (repaired and exc.failure.remote_outcome == RemoteOutcome.NOT_STARTED)
            except Exception as exc:
                kind, reason, automatic = "unknown", str(exc) or type(exc).__name__, False
            else:
                self.last = outcome
                self._cleared = False  # a new generation fills the canvas again
                self.attempt_outputs[attempt_id] = [str(path) for path in outcome.output_paths]
                return
            if automatic and auto_left > 0 and self.generations < self.max_generations:
                # The one automatic retry: no form, counted as one of the run's generations like any retry.
                auto_left -= 1
                self._console(f"Step {step} {HEADLINES[kind]}: {reason} Retrying automatically.", 0)
                if self.cancel.wait(AUTO_RETRY_DELAY):
                    raise ScriptCancelled()
                request = auto_retry_request(request)
            elif not self._retry_step(step, reason, kind=kind):
                # Continue: the step keeps its outcome and the script goes on without its image.
                self.last, self._cleared = None, True
                self._console(f"Step {step} {HEADLINES[kind]}: {reason}", 1)
                return
            else:
                auto_left = AUTO_RETRIES  # a manual Retry is a new submission with its own automatic retry
            retry_of = attempt_id
            self.retries += 1
            self.generations += 1  # one limit: a retry counts like any other generation
            request = replace(
                request,
                request_id=uuid.uuid4().hex,
                resolution_evidence=(
                    *(pair for pair in request.resolution_evidence if pair[0] != RETRY_EVIDENCE),
                    (RETRY_EVIDENCE, retry_of),
                ),
            )

    def _retry_step(self, step: int, reason: str, *, kind: str) -> bool:
        """Ask the person whether to retry a step that failed or has no confirmed outcome.

        True: retry with the same settings. False: continue without the step's image. A headless run (no form bridge)
        raises the step's error to the script instead, which is how the script learns the step did not complete."""
        if self._ask is None or self._answers is not None:
            raise RuntimeError(reason)
        retry_left = self.generations < self.max_generations  # a retry is one of the run's generations
        headline = f"Step {step} {HEADLINES[kind]}"
        detail = {
            "unknown": "It may still finish on the backend, so a retry can make a second image. "
            "Retry sends the same settings (same seed); the first try keeps its own result.",
            "not_saved": "The image was generated, so a retry generates (and is charged) again. "
            "Retry sends the same settings (same seed).",
            "failed": "Retry sends the same settings (same seed) again.",
        }[kind]
        if not retry_left:
            detail = f"This run reached its generation limit ({self.max_generations}); it cannot retry."
        # Scripts shows one button per option (nothing is sent before a click); the field's value is only the
        # remembered choice that gets focus. Continue comes first, so a headless default never sends the step again.
        options = (
            ["Continue without this image", "Retry with the same settings"]
            if retry_left
            else ["Continue without this image"]
        )
        components = [
            {"type": "plainText", "value": reason},
            {"type": "plainText", "value": detail},
            {"type": "segmented", "title": "Next", "options": options, "index": 0},
        ]
        answers = self._ask(headline, "Apply", components, choices=options)
        self._forms += 1  # the run's form ordinal; script forms resolve their inputs by it
        if answers is None or self.cancel.is_set():
            raise ScriptCancelled()
        validate_answers(components, answers)
        return retry_left and answers[-1] == 1

    def generate_batch(self, jobs: Sequence[Mapping[str, Any]], max_concurrent: int = 1) -> list[dict]:
        """DTFlow-only bounded batch; one immutable request and linked durable attempt per submitted child.

        Never writes to the virtual canvas: completion order is not a display/save order.
        Each result is returned at the original input index. A failed or unconfirmed job does not stop the others; apart
        from the shared automatic retry (#241) it is not retried here, and the script sees its status.
        """
        self._admit_generation()
        if self._blocked:
            raise RuntimeError(self._blocked)
        if not isinstance(jobs, list) or not jobs or len(jobs) > self.max_generations:
            raise ValueError("runBatch requires 1..max_generations job objects.")
        if type(max_concurrent) is not int or not 1 <= max_concurrent <= MAX_BATCH_CONCURRENT:
            raise ValueError(f"maxConcurrent must be an integer from 1 to {MAX_BATCH_CONCURRENT}.")
        if self.generations + len(jobs) > self.max_generations:
            raise RuntimeError(
                f"Script reached its generation limit ({self.max_generations}); no batch jobs were submitted."
            )
        if max_concurrent > 1 and (self.reserve_child is None or self.release_child is None):
            raise ValueError("Concurrent batch jobs require a queued DTFlow script with shared slot accounting.")

        # Preflight every script-visible value before linking or submitting any work.
        requests: list[ResolvedGenerationRequest] = []
        destinations: list[Path | None] = []
        for index, item in enumerate(jobs):
            if not isinstance(item, Mapping):
                raise ValueError(f"runBatch job {index + 1} must be an object; nothing was submitted.")
            keys = set(item)
            extra = keys - BATCH_JOB_KEYS
            if extra:
                raise ValueError(f"runBatch job {index + 1} has unsupported keys; nothing was submitted.")
            output_file = item.get("outputFile")
            target = None if output_file is None else self._confine(output_file)
            if target is not None and target.suffix.lower() != ".png":
                raise ValueError("runBatch outputFile must end with .png; nothing was submitted.")
            if target is not None and target.exists():
                raise ValueError("runBatch outputFile already exists; nothing was submitted.")
            plan_id, job_index = item.get("planId"), item.get("jobIndex")
            if plan_id is not None or job_index is not None:
                if (
                    not isinstance(plan_id, str)
                    or re.fullmatch(r"[0-9a-f]{64}", plan_id) is None
                    or type(job_index) is not int
                    or not 1 <= job_index <= 10000
                ):
                    raise ValueError("Batch planId/jobIndex require a SHA-256 and global index 1..10000.")
            request = self._request(
                {key: value for key, value in item.items() if key in PIPELINE_RUN_KEYS},
                call_index=self.generations + index + 1,
            )
            if plan_id is not None:
                request = replace(
                    request,
                    resolution_evidence=(
                        *request.resolution_evidence,
                        ("script_plan", plan_id),
                        ("script_job", str(job_index)),
                    ),
                )
            requests.append(request)
            destinations.append(target)
        named = [path for path in destinations if path is not None]
        if len(set(named)) != len(named):
            raise ValueError("runBatch outputFile paths must be unique; nothing was submitted.")
        if len(named) + sum(1 for file in self.sandbox.rglob("*") if file.is_file()) > MAX_SANDBOX_FILES:
            raise ValueError("The script's file area would exceed its file limit; nothing was submitted.")
        if self.cancel.is_set():
            raise ScriptCancelled()
        self.generations += len(jobs)
        # This future is tracked and drained even if the JS process is killed on Stop.
        return self._await(self._run_batch(requests, destinations, max_concurrent), track=True)

    def _save_batch_png(self, target: Path, data: bytes | None) -> str:
        """Export one committed result into the confined script sandbox without replacing existing files."""
        if data is None or not data.startswith(bytes((137, 80, 78, 71, 13, 10, 26, 10))):
            raise ValueError("The committed result has no PNG bytes for the requested sandbox export.")
        if len(data) > MAX_SANDBOX_FILE_BYTES:
            raise ValueError("The committed PNG exceeds the script file limit.")
        if sum(1 for item in self.sandbox.rglob("*") if item.is_file()) >= MAX_SANDBOX_FILES:
            raise ValueError("The script's file area is full.")
        # Paths were confined before execution. Recheck after asynchronous work to reject redirected parents.
        target = self._confine(str(target))
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(data)
        return str(target)

    async def _run_batch(
        self, requests: list[ResolvedGenerationRequest], destinations: list[Path | None], concurrency: int
    ) -> list[dict]:
        """Worker pool shares the normal queue reservation budget; Stop stops intake, not remote work."""
        limit = min(concurrency, self.service.backend.capabilities.max_concurrency)
        results: list[dict] = [{"status": "not_started", "attemptId": None, "outputPaths": []} for _ in requests]
        next_job = 0
        intake_open = True
        asset_gate = asyncio.Event()
        asset_gate.set()
        assets = getattr(self.service, "assets", None)
        if assets is not None and callable(getattr(assets, "ensure_many", None)):
            distinct = {
                (asset.identifier, asset.sha256, asset.size): asset
                for request in requests
                for asset in request.prepared_assets
            }
            if distinct:
                await assets.ensure_many(tuple(distinct.values()))

        async def worker():
            nonlocal next_job, intake_open
            while intake_open and not self.cancel.is_set() and next_job < len(requests):
                await asset_gate.wait()
                if not intake_open or self.cancel.is_set():
                    break
                index = next_job
                next_job += 1
                request = requests[index]
                lane = attempt_id = uuid.uuid4().hex  # the lane keeps the first child's id across a retry
                acquired = False

                def relinked(new_id: str, index: int = index) -> None:
                    nonlocal attempt_id
                    attempt_id = new_id
                    results[index]["attemptId"] = new_id

                linked = False
                try:
                    if self.reserve_child is not None:
                        while intake_open and not self.cancel.is_set():
                            acquired = await self.reserve_child(lane, concurrency)
                            if acquired:
                                break
                            await asyncio.sleep(POLL_SECONDS)
                        if not acquired:
                            return
                    if not intake_open or self.cancel.is_set():
                        return
                    # Linking writes the run through the state owner; never block the loop on it.
                    await asyncio.to_thread(self._link_child, attempt_id)
                    linked = True
                    self.steps += 1
                    self.attempts.append(attempt_id)
                    # Keep the identity even if another worker closed intake
                    # while the run writer was linking this job.
                    results[index]["attemptId"] = attempt_id
                    if not intake_open or self.cancel.is_set():
                        return
                    try:
                        outcome = await self._generate_child(request, attempt_id, relinked, asset_gate)
                    except BackendError as exc:
                        remote = exc.failure.remote_outcome
                        results[index] = {
                            "status": (
                                "failed"
                                if remote in SAFE_FAILURES
                                else "save_failed"
                                if remote == RemoteOutcome.SUCCEEDED
                                else "unknown"
                            ),
                            "attemptId": attempt_id,
                            "outputPaths": [],
                            "error": exc.failure.code,
                        }
                        # A failed or unconfirmed job keeps its own status; the other jobs still run (#220).
                    except Exception as exc:
                        results[index] = {
                            "status": "unknown",
                            "attemptId": attempt_id,
                            "outputPaths": [],
                            "error": type(exc).__name__,
                        }
                    else:
                        self.last = outcome
                        self._cleared = False
                        paths = [str(path) for path in outcome.output_paths]
                        self.attempt_outputs[attempt_id] = paths
                        saved_path = None
                        if destinations[index] is not None:
                            try:
                                saved_path = await asyncio.to_thread(
                                    self._save_batch_png, destinations[index], outcome.final_image
                                )
                            except Exception as exc:
                                results[index] = {
                                    "status": "save_failed",
                                    "attemptId": attempt_id,
                                    "outputPaths": paths,
                                    "error": f"Sandbox export: {type(exc).__name__}",
                                }
                        if results[index]["status"] != "save_failed":
                            results[index] = {
                                "status": "succeeded",
                                "attemptId": attempt_id,
                                "outputPaths": paths,
                                "savedPath": saved_path,
                            }
                except asyncio.CancelledError:
                    intake_open = False
                    self._blocked = "A batch worker was interrupted; no more jobs."
                    raise
                except Exception as exc:
                    intake_open = False
                    self._blocked = "A batch reservation or run record failed; no more jobs."
                    results[index] = {
                        "status": "failed",
                        "attemptId": attempt_id if linked else None,
                        "outputPaths": [],
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                finally:
                    if acquired and self.release_child is not None:
                        try:
                            await settle(self.release_child(lane))
                        except BaseException:
                            intake_open = False
                            self._blocked = "A batch slot could not be released; no more jobs."
                            raise

        # Own every worker until it settles, even when a sibling's release fails.
        # Shield the aggregate so cancelling this batch stops intake, not remote
        # work. The tracked host future must not complete before this drain does.
        workers = [asyncio.create_task(worker()) for _ in range(min(limit, len(requests)))]
        drain = asyncio.gather(*workers, return_exceptions=True)
        try:
            outcomes = await asyncio.shield(drain)
        except asyncio.CancelledError:
            intake_open = False
            self._blocked = "Stopped; already admitted batch generations are being drained."
            await settle(drain)
            raise
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
        return results

    async def _generate_child(
        self, request: ResolvedGenerationRequest, attempt_id: str, relinked: Callable[[str], None],
        asset_gate: asyncio.Event,
    ) -> GenerationOutcome:
        """One batch job's generation with the shared automatic retry (#241): a retry is a new child, linked before it
        is sent, counted as one of the run's generations. Stop during the wait raises the first try's failure."""
        auto_left = AUTO_RETRIES
        while True:
            try:
                return await run_generation(
                    self.service, request, prefix=self.prefix, attempt_id=attempt_id, layout=self._layout(request)
                )
            except BackendError as exc:
                repaired = False
                assets = getattr(self.service, "assets", None)
                if (
                    assets is not None and request.prepared_assets
                    and exc.failure.category == ErrorCategory.REMOTE_REJECTION
                    and callable(getattr(assets, "recover_missing", None))
                ):
                    asset_gate.clear()
                    try:
                        repaired = await assets.recover_missing(request.prepared_assets)
                    finally:
                        asset_gate.set()
                safe_retry = repaired and exc.failure.remote_outcome == RemoteOutcome.NOT_STARTED
                if not (
                    auto_left > 0
                    and (safe_retry or auto_retryable(exc.failure))
                    and self.generations < self.max_generations
                    and not self.cancel.is_set()
                ):
                    raise
                first = exc
            auto_left -= 1
            self.generations += 1
            self.retries += 1
            deadline = asyncio.get_running_loop().time() + AUTO_RETRY_DELAY
            while asyncio.get_running_loop().time() < deadline:
                if self.cancel.is_set():
                    raise first
                await asyncio.sleep(POLL_SECONDS)
            retried = auto_retry_request(request)
            request = replace(
                retried,
                resolution_evidence=(
                    *(pair for pair in retried.resolution_evidence if pair[0] != RETRY_EVIDENCE),
                    (RETRY_EVIDENCE, attempt_id),
                ),
            )
            attempt_id = uuid.uuid4().hex
            await asyncio.to_thread(self._link_child, attempt_id)
            self.attempts.append(attempt_id)
            relinked(attempt_id)

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
        if isinstance(name, str):
            for asset in (*self.base.prepared_assets, *self.available_assets):
                if name.strip().lower() == asset.identifier.lower():
                    return {"file": asset.identifier, "weight": 1.0, "mode": "all"}
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

    def list_script_loras(self, offset: int = 0) -> dict:
        from dtflow.services.script_inventory import script_lora_inventory

        if type(offset) is not int or offset < 0:
            raise ValueError("LoRA inventory offset must be a nonnegative integer.")
        if self._script_inventory is None:
            self._script_inventory = script_lora_inventory(
                self.sandbox.parent.parent, self._inventory(), self.available_assets, self.base.model_id
            )
        # Bound the ASCII-JSON reply even for maximally escaped names/identifiers.
        page = self._script_inventory["items"][offset : offset + 100]
        following = offset + len(page)
        return {
            **self._script_inventory,
            "items": page,
            "nextOffset": following if following < self._script_inventory["total"] else None,
        }

    def load_preferences(self):
        from dtflow.persistence.script_preferences import preferences

        return preferences(self.sandbox.parent.parent, self.source_sha256)

    def save_preferences(self, value):
        from dtflow.persistence.script_preferences import preferences

        if not self._preferences_allowed or self.cancel.is_set():
            raise ValueError("Answer a form explicitly before saving script preferences.")
        return preferences(self.sandbox.parent.parent, self.source_sha256, value=value, write=True)

    def plan_fingerprint(self, value) -> str:
        if not isinstance(value, dict):
            raise ValueError("Script plan identity must be a JSON object.")
        selected = value.get("selected", [])
        if not isinstance(selected, list) or len(selected) > 400 or any(not isinstance(item, str) for item in selected):
            raise ValueError("Script plan selections must be at most 400 identifiers.")
        pool = {item.identifier: item for item in (*self.available_assets, *self.base.prepared_assets)}
        content = {name: (pool[name].sha256, pool[name].size) for name in selected if name in pool}
        text = json.dumps(
            {"source": self.source_sha256, "value": value, "preparedContent": content},
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
        if len(text) > 1024 * 1024:
            raise ValueError("Script plan identity exceeds 1 MiB.")
        return hashlib.sha256(text.encode("ascii")).hexdigest()

    # -- forms ---------------------------------------------------------------------------------------------------

    def request_from_user(self, title: str, confirm: str, components: Sequence[Mapping[str, Any]]) -> list[Any]:
        """Park on the persisted interactive bridge or consume one headless ordered answer set."""
        self._preferences_allowed = False
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
            from dtflow.services.script_form_inputs import crop_inline_form_images

            answers = crop_inline_form_images(components, answers)
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

            resolved = resolve_form_inputs(self.sandbox.parent.parent, self.run_id, self._forms, components, answers)
            if self.cancel.is_set():
                raise ScriptCancelled()
            self._start_permitted = True
            self._preferences_allowed = True
            return resolved
        self._preferences_allowed = source == "file"
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
    require_start: bool = False,
    reserve_child: Callable[[str, int], Awaitable[bool]] | None = None,
    release_child: Callable[[str], Awaitable[None]] | None = None,
    available_assets: tuple[PreparedAsset, ...] = (),
    output_group: str | None = None,
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
            require_start=require_start,
            reserve_child=reserve_child,
            release_child=release_child,
            available_assets=available_assets,
            output_group=output_group,
            source_sha256=getattr(context, "source_sha256", ""),
        )
        hosts.append(host)
        return host.hooks()

    return hooks

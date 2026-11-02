"""Hook table: every ``__dtHooks.*`` function the vendored upstream JS calls, with its DTFlow state and owner.

States: ``implemented`` (host function exists in this build), ``planned`` (to be ported from upstream
``ScriptExecutor.swift`` by the owning issue; calling it raises a clear error until then) and ``unsupported``
(unsupported by owner decision; always raises).
"""

from __future__ import annotations

from dataclasses import dataclass

IMPLEMENTED = "implemented"
PLANNED = "planned"
UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class HookSpec:
    name: str
    state: str
    owner: str  # owning issue, e.g. "#114"
    maps_to: str  # DTFlow service/area that will back the hook
    note: str = ""


def _hook(name: str, state: str, owner: str, maps_to: str, note: str = "") -> tuple[str, HookSpec]:
    return name, HookSpec(name, state, owner, maps_to, note)


HOOKS: dict[str, HookSpec] = dict(
    [
        # Console and DTFlow overlay.
        _hook("log", IMPLEMENTED, "#113", "diagnostics (script.console)", "type 0 log, 1 warn, 2 error"),
        _hook(
            "sleep",
            IMPLEMENTED,
            "#113",
            "cancellable bounded wait",
            "DTFlow overlay for __dtSleep (upstream busy-spins)",
        ),
        _hook("repl", UNSUPPORTED, "#112", "-", "interactive REPL is not offered"),
        # Generation and configuration (Tier A).
        _hook("existingConfiguration", IMPLEMENTED, "#114", "validated Generate settings snapshot"),
        _hook("existingPrompts", IMPLEMENTED, "#114", "Generate prompt and negative prompt"),
        _hook(
            "generateImage", IMPLEMENTED, "#114", "ResolvedGenerationRequest -> GenerationService", "fields per #115"
        ),
        _hook("createLoRA", IMPLEMENTED, "#114", "LoRA inventory lookup"),
        _hook("createControl", IMPLEMENTED, "#114", "control inventory lookup", "dispatch gated by #117"),
        _hook("areModelsDownloaded", IMPLEMENTED, "#114", "inventory lookup (read-only)"),
        _hook("downloadBuiltins", IMPLEMENTED, "#114", "inventory lookup; never downloads"),
        _hook("requestFromUser", IMPLEMENTED, "#114", "form renderer", "simplified in #114, complete in #196"),
        _hook("screenSize", IMPLEMENTED, "#114", "Qt screen geometry via the UI layer"),
        # Filesystem sandbox.
        _hook("picturesPath", IMPLEMENTED, "#114", "per-run sandbox directory"),
        _hook("listFilesUnderPictures", IMPLEMENTED, "#114", "per-run sandbox directory"),
        _hook("listFilesUnderPicturesWithinDirectory", IMPLEMENTED, "#114", "per-run sandbox directory"),
        _hook("listFilesWithinDirectory", IMPLEMENTED, "#114", "per-run sandbox directory"),
        # Canvas geometry (Tier B).
        _hook("updateCanvasSize", PLANNED, "#116", "headless virtual canvas"),
        _hook("moveCanvas", PLANNED, "#116", "headless virtual canvas"),
        _hook("setCanvasZoom", PLANNED, "#116", "headless virtual canvas"),
        _hook("canvasZoom", PLANNED, "#116", "headless virtual canvas"),
        _hook("boundingBox", PLANNED, "#116", "headless virtual canvas"),
        _hook("topLeftCorner", PLANNED, "#116", "headless virtual canvas"),
        _hook("clearCanvas", PLANNED, "#116", "headless virtual canvas"),
        # Canvas pixels and layers.
        _hook("loadImageFileToCanvas", PLANNED, "#116", "services/image_preparation + sandbox"),
        _hook("loadImageSrcToCanvas", PLANNED, "#116", "services/image_preparation"),
        _hook("imageSizeFromSrc", PLANNED, "#116", "services/image_preparation"),
        _hook(
            "saveImageFileFromCanvas", IMPLEMENTED, "#114", "committed output copy into sandbox", "canvas crop in #116"
        ),
        _hook("saveImageSrcFromCanvas", IMPLEMENTED, "#114", "committed output as data URL", "canvas crop in #116"),
        _hook("loadLayerFromSrc", PLANNED, "#116", "services/image_preparation"),
        _hook("loadLayerFromJson", PLANNED, "#116", "headless virtual canvas"),
        _hook("loadLayerFromFiles", PLANNED, "#116", "native file picker, snapshot first"),
        _hook("loadLayerFromPhotos", PLANNED, "#116", "native image picker, snapshot first"),
        _hook("saveLayerSrc", PLANNED, "#116", "headless virtual canvas"),
        # Masks.
        _hook("createMask", PLANNED, "#116", "host-owned mask handles"),
        _hook("fillMaskRectangle", PLANNED, "#116", "host-owned mask handles"),
        _hook("currentMask", PLANNED, "#116", "host-owned mask handles"),
        _hook("maskSrc", PLANNED, "#116", "host-owned mask handles"),
        # Detection (first milestone, owner decision 2026-11-02).
        _hook("detectFaces", PLANNED, "#117", "services/person_selection adapter"),
        _hook("createForegroundMask", PLANNED, "#117", "services/person_selection adapter"),
        _hook("createBackgroundMask", PLANNED, "#117", "services/person_selection adapter"),
        _hook("createBodyMask", PLANNED, "#117", "services/person_selection adapter"),
        _hook(
            "detectHands",
            PLANNED,
            "#117",
            "services/person_selection adapter",
            "in upstream JSInterop but not called by the vendored JS; reachable only by raw __dtHooks calls",
        ),
        # Guidance.
        _hook("extractDepthMap", PLANNED, "#117", "depth guidance, backend evidence required"),
        _hook("clearMoodboard", PLANNED, "#117", "moodboard guidance"),
        _hook("removeFromMoodboardAt", PLANNED, "#117", "moodboard guidance"),
        _hook("setMoodboardImageWeight", PLANNED, "#117", "moodboard guidance"),
        # Unsupported by owner decision.
        _hook("answer", UNSUPPORTED, "#112", "-", "VLM (moondream2) is not offered"),
        _hook("CLIP", UNSUPPORTED, "#112", "-", "text-embedding hook is not offered"),
    ]
)

# Facade call sites that do not appear as ``__dtHooks.*`` (pure JS in the vendored script).
PURE_JS_APIS = frozenset({"console.log", "console.warn", "console.error", "requestFromUser", "__dtSleep"})

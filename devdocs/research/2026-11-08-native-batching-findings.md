# Native Draw Things batching: findings (#280, part of #274)

Documentation and source inspection only. No live generation, probe, benchmark, test or render was run, and no code changed. Sources: pinned upstream `drawthingsai/draw-things-community` at `e2c7fa474ce3bddfe34ad20bdde75f425344800e` (read-only checkout), `drawthings-py` 0.4.0 wheel, and this repository.

## Conclusion

**Do not enable native batching. Keep the 1x1 advertisement and the `unsupported_batch` rejection.** Logical single-image expansion (#275) is the only batching path DTFlow should rely on. Evidence for the local server is source-level but not run; for DT+ Cloud there is none.

## What the upstream source shows

| Question | Evidence | Strength |
|---|---|---|
| Is `batch_size` a real server-side feature? | `LocalImageGenerator` builds one noise tensor of shape `(batchSize, ...)` and denoises it in one pass. So `batch_size` N means N images in one forward pass (more memory, one pass). | Source-read |
| Is `batch_count` honored by the server? | `batchCount` appears in `config.fbs`, `Invocation` and the `MediaGenerationKit` pipeline option, but is never read in `LocalImageGenerator`, the gRPC server or `RemoteImageGenerator`. It is a client/app-level repeat count. A gRPC client sending `batch_count` > 1 gets no verified repetition. | Source-read (absence of use) |
| Per-image seeds | Only `scaleAlike` seed mode derives per-image seeds: seed, then `xorshift(seed)` per further image. `torchCpuCompatible` and `nvidiaGpuCompatible` draw one noise array for the whole batch from a single seed; `legacy` is non-reproducible. So image `i` of a batch generally cannot be regenerated alone from a per-image seed, except in `scaleAlike`. `drawthings-py` agrees: it labels other modes' per-image metadata seeds as incorrect for batch > 1. | Source-read |
| Output ordering and count | The server sends one `ImageGenerationResponse` per image (`generatedImages = [imageData]`), in tensor batch order, `chunkState = lastChunk`; images over 4 MiB are chunked when the client asks for chunking. Batch index is the only ordering evidence. | Source-read |
| Video and some models | Video models and `seedvr2` force `batchSize = 1` in the engine. Image-model coverage beyond that is not documented. | Source-read |
| Limits | The upstream scripting layer clamps `batchSize` to 1..4, a hint that larger values are not a supported general range. Memory scaling is not documented. | Source-read |
| Error, cancel, partial | An empty final result is logged as a failure server-side. Nothing documents what the stream looks like when a batch partly fails or is cancelled midway. | No evidence |
| DT+ Cloud | The cloud service is not in the public repository. Its handling of `batch_size`, `batch_count`, billing per image or per request, seeds, ordering and partial failure is undocumented. Existing DTFlow docs only record batch 1/1 on the direct-cloud path. | No evidence |
| Throughput benefit | No pre-existing reproducible measurement of batch vs sequential exists in the repository. The 2026-10-04 user-reported runs compare concurrent vs serial single-image jobs only. | None; no claim made |

## What DTFlow already does

`wire.validate_supported()` rejects anything but 1x1 (`unsupported_batch`); `build_generation_message` and `verify_wire_configuration` hard-code `batch_size=1, batch_count=1`; both adapters advertise 1x1. The receive loop in `backend.py` already counts `batch_size * batch_count` expected finals and rejects extra, missing or chunked output, so decoding would need little change, but seeds and metadata per image would not be recoverable (see seeds above).

## Why not enable it

1. `batch_count` has no verified server meaning, so it must stay 1.
2. `batch_size` image i has no recoverable seed outside `scaleAlike`, which breaks per-output seed identity, Retry-same-seed (#220) and lineage (#277).
3. Partial failure, cancellation and DT+ billing semantics are unknown, and under the epic rules they cannot be probed.
4. One failed batch would fail all siblings, the opposite of #275's "siblings survive failures".
5. There is no measured benefit to weigh against that.

## Possible later work (not for this epic)

If the owner later authorizes a controlled local-only probe, a narrow candidate is `batch_size` 2 to 4 on the local backend in `scaleAlike` seed mode for one image model, recording per-image seeds, order and cancellation. DT+ would need provider documentation first. Until then nothing is advertised beyond 1x1, and #275's logical expansion needs no native support.

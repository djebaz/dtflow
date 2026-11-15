"""Local-only, explicit q8p experiment; never opens application state or providers."""

from __future__ import annotations

import hashlib
import logging
import math
import multiprocessing
import os
import sqlite3
import stat
import struct
import tempfile
import time
from collections import Counter, deque
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import AbstractContextManager, ExitStack, closing
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import version
from itertools import islice
from pathlib import Path

import numpy as np

from dtflow.build import identity
from dtflow.diagnostics import event
from dtflow.services.lora_family import detect_lora_family
from dtflow.services.lora_q8p_codec import (
    BLOCK,
    CODECS,
    Q8P,
    QuantizationError,
    decode,
    encode,
    encode_chunk,
    q8p_fits,
    start_encoder_worker,
)

MAX_TENSORS = 20_000
MAX_TENSOR_BYTES = 64 * 1024**2
MAX_FILE_BYTES = 8 * 1024**3
WRITER_FAMILIES = {"krea_2", "z_image", "sdxl_base_v0.9"}
UPSTREAM = {
    "draw_things": "4e986724c6c65cdc8b9eeb8a2d2f4775c698e007",
    "s4nnc": "50d060dba795a6d9ca8497ed0222dde7492a8d12",
    "ccv": "5c6c18fd0a10e17071e63dc4156a5efb5d41bb01",
}
TABLE = (
    "CREATE TABLE tensors (name TEXT, type INTEGER, format INTEGER, datatype INTEGER, "
    "dim BLOB, data BLOB, PRIMARY KEY (name))"
)
log = logging.getLogger(__name__)


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


@dataclass(frozen=True)
class Tensor:
    rowid: int
    name: str
    kind: int
    format: int
    dim: bytes
    count: int
    length: int

    @property
    def codec(self) -> int:
        return self.kind >> 32


class Input(AbstractContextManager):
    def __init__(self, path: Path):
        self.path = Path(path).resolve(strict=True)
        info = self.path.stat()
        if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= MAX_FILE_BYTES:
            raise QuantizationError("input_size", "Input must be a regular CKPT file no larger than 8 GiB.")
        self._sidecars()
        self.db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            self.db.execute("PRAGMA query_only=ON")
            self.db.execute("PRAGMA trusted_schema=OFF")
            self.db.execute("BEGIN")
            tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables != {"tensors"}:
                raise QuantizationError("container_schema", "Only the standalone tensors-table CKPT is supported.")
            column_info = self.db.execute("PRAGMA table_info(tensors)").fetchall()
            columns = {row[1] for row in column_info}
            if columns != {"name", "type", "format", "datatype", "dim", "data"} or [
                row[1] for row in column_info if row[5]
            ] != ["name"]:
                raise QuantizationError("container_schema", "Unsupported CKPT tensor schema.")
            rows = self.db.execute(
                "SELECT rowid,substr(name,1,1025),type,format,datatype,substr(dim,1,49),length(data),typeof(data) "
                "FROM tensors ORDER BY name LIMIT ?",
                (MAX_TENSORS + 1,),
            ).fetchall()
            if not 1 <= len(rows) <= MAX_TENSORS:
                raise QuantizationError("tensor_limit", "Supported tensor count is 1..20000.")
            self.tensors = [self._tensor(row) for row in rows]
            names = {tensor.name for tensor in self.tensors}
            if (
                len(names) != len(self.tensors)
                or any(
                    name.removesuffix("__up__") + "__down__" not in names for name in names if name.endswith("__up__")
                )
                or any(
                    name.removesuffix("__down__") + "__up__" not in names for name in names if name.endswith("__down__")
                )
            ):
                raise QuantizationError("adapter_pairs", "Every adapter must have one up and one down tensor.")
            self.family = detect_lora_family(self.path).family
            self.sha256 = digest(self.path)
            self.size = info.st_size
        except BaseException:
            self.db.close()
            raise

    def _sidecars(self):
        if any(os.path.lexists(str(self.path) + suffix) for suffix in ("-wal", "-shm", "-journal")):
            raise QuantizationError("input_busy", "Use a closed standalone CKPT without SQLite sidecars.")

    @staticmethod
    def _tensor(row) -> Tensor:
        rowid, name, kind, format_, datatype, dim, length, storage_type = row
        if storage_type != "blob":
            raise QuantizationError("payload_type", "Tensor data must be a SQLite blob.")
        if not isinstance(name, str) or len(name) > 1024 or not name.endswith(("__up__", "__down__")):
            raise QuantizationError("adapter_names", "Input must contain only named LoRA up/down tensors.")
        if type(kind) is not int or kind < 0 or kind & 0xFFFFFFFF != 1 or kind >> 32 not in CODECS:
            raise QuantizationError("unsupported_codec", "Unsupported tensor memory type or storage codec.")
        if type(datatype) is not int or datatype != 0x20000 or type(format_) is not int or format_ not in (1, 2):
            raise QuantizationError("unsupported_dtype", "Only CPU F16 tensors with supported formats are accepted.")
        if not isinstance(dim, bytes) or len(dim) != 48:
            raise QuantizationError("tensor_shape", "Expected the 12-dimension Draw Things shape blob.")
        dimensions = struct.unpack("<12i", dim)
        rank = next((i for i, value in enumerate(dimensions) if value == 0), 12)
        if not rank or any(d <= 0 for d in dimensions[:rank]) or any(dimensions[rank:]):
            raise QuantizationError("tensor_shape", "Invalid tensor dimensions.")
        count = math.prod(dimensions[:rank])
        if count * 2 > MAX_TENSOR_BYTES or type(length) is not int or not 1 <= length <= MAX_TENSOR_BYTES + 4:
            raise QuantizationError("tensor_limit", "A tensor exceeds the 64 MiB experiment limit.")
        return Tensor(rowid, name, kind, format_, dim, count, length)

    def payload(self, tensor: Tensor) -> bytes:
        with self.db.blobopen("tensors", "data", tensor.rowid, readonly=True) as blob:
            if len(blob) != tensor.length:
                raise QuantizationError("payload_size", "Tensor payload changed during inspection.")
            return blob.read()

    def verify(self):
        self._sidecars()
        if self.path.stat().st_size != self.size or digest(self.path) != self.sha256:
            raise QuantizationError("input_changed", "Input changed while processing; no result is authorized.")

    def __exit__(self, *args):
        self.db.close()

    def summary(self) -> dict:
        return {
            "sha256": self.sha256,
            "file_bytes": self.size,
            "family": self.family or "unknown",
            "tensors": len(self.tensors),
            "elements": sum(t.count for t in self.tensors),
            "payload_bytes": sum(t.length for t in self.tensors),
            "codecs": dict(Counter(CODECS[t.codec] for t in self.tensors)),
        }


@dataclass
class Differences:
    elements: int = 0
    unequal_bits: int = 0
    absolute_sum: float = 0.0
    squared_sum: float = 0.0
    maximum: float = 0.0

    def add(self, left: np.ndarray, right: np.ndarray):
        self.elements += len(left)
        self.unequal_bits += int(np.count_nonzero(left.view("<u2") != right.view("<u2")))
        for start in range(0, len(left), 65_536):
            difference = left[start : start + 65_536].astype(np.float64) - right[start : start + 65_536]
            self.absolute_sum += float(np.abs(difference).sum())
            self.squared_sum += float(np.square(difference).sum())
            self.maximum = max(self.maximum, float(np.abs(difference).max(initial=0)))

    def summary(self):
        return {
            "elements": self.elements,
            "different_f16_bit_patterns": self.unequal_bits,
            "mean_absolute_error": self.absolute_sum / self.elements,
            "root_mean_square_error": math.sqrt(self.squared_sum / self.elements),
            "maximum_absolute_error": self.maximum,
        }


def report(action: str) -> dict:
    return {"schema": 1, "experiment": "lora-q8p-v1", "action": action, "build": identity(), "upstream": UPSTREAM}


def inspect_file(path: Path) -> dict:
    with Input(path) as source:
        for tensor in source.tensors:
            decode(source.payload(tensor), tensor.count, tensor.codec)
        source.verify()
        return {**report("inspect"), "input": source.summary(), "payloads_validated": True}


def compare_files(left: Path, right: Path) -> dict:
    with Input(left) as source, Input(right) as reference:
        if [(t.name, t.dim, t.format) for t in source.tensors] != [
            (t.name, t.dim, t.format) for t in reference.tensors
        ]:
            raise QuantizationError("inventory_mismatch", "Tensor names, dimensions or formats do not match.")
        differences = Differences()
        equal_payloads = 0
        for a, b in zip(source.tensors, reference.tensors, strict=True):
            raw_a, raw_b = source.payload(a), reference.payload(b)
            equal_payloads += int(a.codec == b.codec and raw_a == raw_b)
            differences.add(decode(raw_a, a.count, a.codec), decode(raw_b, b.count, b.codec))
        source.verify()
        reference.verify()
        return {
            **report("compare"),
            "input": source.summary(),
            "reference": reference.summary(),
            "matching_tensor_inventory": True,
            "equal_encoded_payload_tensors": equal_payloads,
            "numerical_difference": differences.summary(),
            "quality_or_provider_acceptance": "not_established",
        }


def _destination(path: Path, source: Path) -> Path:
    path = Path(os.path.abspath(path))
    if path.suffix.casefold() != ".ckpt" or path.resolve() == source:
        raise QuantizationError("destination", "Choose a separate new .ckpt output.")
    if any(part.casefold() in ("lora_files", ".dtflow") for part in path.parts):
        raise QuantizationError("destination", "Experimental output must be outside application/retained state.")
    if os.path.lexists(path):
        raise QuantizationError("destination_exists", "Output already exists; no overwrite is allowed.")
    for parent in path.parents:
        if not parent.exists():
            raise QuantizationError("destination", "Choose an existing output directory.")
        info = parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise QuantizationError("destination", "Output directories must not be redirected.")
    return path


@dataclass
class _Encoding:
    tensor: Tensor
    values: np.ndarray
    remaining: int
    payload: bytearray


@dataclass(frozen=True)
class BlockBatchEncoder:
    """Explicit in-process experiment seam; the shared writer retains file authority."""

    batch_size: int
    encode: Callable[[list[tuple[np.ndarray, bool]]], list[tuple[int, bytes]]]
    metadata: dict


def _encoding_tasks(source):
    for tensor in source.tensors:
        values = decode(source.payload(tensor), tensor.count, 0)
        if q8p_fits(tensor.count):
            work = _Encoding(tensor, values, (tensor.count + BLOCK - 1) // BLOCK, bytearray(struct.pack("<I", BLOCK)))
            for start in range(0, tensor.count, BLOCK):
                yield work, values[start : start + BLOCK], False
        else:
            yield _Encoding(tensor, values, 1, bytearray()), values, True


def _encoded_tensors(source, pool, workers, tick):
    if pool is None:
        for tensor in source.tensors:
            values = decode(source.payload(tensor), tensor.count, 0)
            codec, payload = encode(values, tick)
            yield tensor, values, codec, payload
        return
    tasks = _encoding_tasks(source)
    pending = deque()

    def submit(task):
        work, values, whole = task
        pending.append((work, pool.submit(encode_chunk, values, whole)))

    try:
        for task in islice(tasks, workers):
            submit(task)
        while pending:
            # This queue spans tensor boundaries, even when each tensor has just one block.
            work, future = pending.popleft()
            codec, payload = future.result()
            work.payload.extend(payload)
            work.remaining -= 1
            tick()
            if work.remaining == 0:
                yield work.tensor, work.values, codec, bytes(work.payload)
            task = next(tasks, None)
            if task is not None:
                submit(task)
    finally:
        for _, future in pending:
            future.cancel()


def _batch_encoded_tensors(source, encoder, tick):
    tasks = _encoding_tasks(source)
    while batch := list(islice(tasks, encoder.batch_size)):
        results = encoder.encode([(values, whole) for _, values, whole in batch])
        if len(results) != len(batch):
            raise QuantizationError("encoder_result", "Batch encoder returned an unexpected number of blocks.")
        for (work, values, whole), (codec, payload) in zip(batch, results, strict=True):
            if (
                codec not in CODECS
                or not isinstance(payload, bytes)
                or (not whole and (codec != Q8P or len(payload) != 512 + len(values)))
                or (whole and not 1 <= len(payload) <= len(values) * 2 + 4)
            ):
                raise QuantizationError("encoder_result", "Batch encoder returned an invalid block.")
            work.payload.extend(payload)
            work.remaining -= 1
            tick()
            if work.remaining == 0:
                yield work.tensor, work.values, codec, bytes(work.payload)


def quantize_file(
    path: Path,
    output: Path,
    progress=None,
    jobs: int = 0,
    on_start=None,
    batch_encoder: BlockBatchEncoder | None = None,
) -> dict:
    started = time.monotonic()
    if type(jobs) is not int or jobs < 0:
        raise QuantizationError("workers", "Worker count must be non-negative; zero selects available CPUs.")
    detected_cpus = getattr(os, "process_cpu_count", os.cpu_count)() or 1
    workers = jobs or detected_cpus
    if batch_encoder is not None:
        if (
            not isinstance(batch_encoder, BlockBatchEncoder)
            or type(batch_encoder.batch_size) is not int
            or not 1 <= batch_encoder.batch_size <= 16
            or jobs not in (0, 1)
        ):
            raise QuantizationError("encoder", "Use 1..16 blocks per custom batch and no CPU worker pool.")
        workers = 1
    if os.name == "nt":
        if jobs > 61:
            raise QuantizationError("workers", "Windows supports at most 61 encoder workers.")
        workers = min(workers, 61)
    if batch_encoder is None:
        try:
            import_module("kmeans1d")
        except (ImportError, OSError) as exc:
            raise QuantizationError(
                "clustering_unavailable", "Install the optional quantization group: uv sync --group quantization."
            ) from exc
    with ExitStack() as stack, Input(path) as source:
        destination = _destination(output, source.path)
        if source.family not in WRITER_FAMILIES or any(t.codec != 0 for t in source.tensors):
            raise QuantizationError("unsupported_input", "Writer accepts raw F16 Krea 2, Z Image or SDXL Base only.")
        handle, name = tempfile.mkstemp(prefix=".dtflow-q8p-", suffix=".part", dir=destination.parent)
        os.close(handle)
        staged = Path(name)
        counts = Counter()
        differences = Differences()
        done = 0
        total = sum((t.count + BLOCK - 1) // BLOCK for t in source.tensors)
        workers = min(workers, total)

        def tick():
            nonlocal done
            done += 1
            if progress is not None:
                progress(done, total)

        try:
            if on_start is not None:
                on_start(detected_cpus, workers)
            if progress is not None:
                progress(0, total)
            pool = None
            if workers > 1:
                pool = ProcessPoolExecutor(
                    max_workers=workers,
                    mp_context=multiprocessing.get_context("spawn"),
                    initializer=start_encoder_worker,
                )
                stack.callback(pool.shutdown, wait=True, cancel_futures=True)
            with closing(sqlite3.connect(staged)) as db:
                db.execute(TABLE)
                db.execute("BEGIN")
                encoded_tensors = (
                    _batch_encoded_tensors(source, batch_encoder, tick)
                    if batch_encoder is not None
                    else _encoded_tensors(source, pool, workers, tick)
                )
                for tensor, values, codec, payload in encoded_tensors:
                    differences.add(values, decode(payload, tensor.count, codec))
                    db.execute(
                        "INSERT INTO tensors VALUES (?,?,?,?,?,?)",
                        (
                            tensor.name,
                            codec << 32 | 1,
                            tensor.format,
                            0x20000,
                            tensor.dim,
                            payload,
                        ),
                    )
                    counts[CODECS[codec]] += 1
                db.commit()
            verified = inspect_file(staged)["input"]
            source.verify()
            _destination(destination, source.path)  # Recheck immediately before exclusive publication.
            with staged.open("r+b") as file:
                os.fsync(file.fileno())
            try:
                os.link(staged, destination)  # Atomic no-clobber; no overwrite fallback.
            except FileExistsError as exc:
                raise QuantizationError(
                    "destination_exists", "Output appeared during conversion; it was not replaced."
                ) from exc
            except OSError as exc:
                raise QuantizationError(
                    "publication_failed",
                    "Could not publish exclusively. Use an output filesystem supporting hard links.",
                ) from exc
            return {
                **report("quantize"),
                "input": source.summary(),
                "output": verified,
                "codecs_written": dict(counts),
                "clustering": {
                    **(
                        {**batch_encoder.metadata, "mode": "custom_batch", "ccv_bit_equivalence": "not_claimed"}
                        if batch_encoder is not None
                        else {
                            "library": "kmeans1d",
                            "version": version("kmeans1d"),
                            "ccv_bit_equivalence": "unverified",
                        }
                    ),
                    "workers": workers,
                    "detected_cpus": detected_cpus,
                },
                "numerical_difference_from_input": differences.summary(),
                "seconds": round(time.monotonic() - started, 3),
                "size_reduction_percent": round((1 - verified["file_bytes"] / source.size) * 100, 3),
                "quality_or_provider_acceptance": "not_established",
            }
        except BrokenProcessPool as exc:
            raise QuantizationError("worker_failed", "An encoder worker stopped; no output was published.") from exc
        finally:
            # Only our exclusive same-directory staging file/SQLite journal; never remove a published output.
            for leftover in (staged, Path(str(staged) + "-journal")):
                try:
                    leftover.unlink(missing_ok=True)
                except OSError:
                    event(log, "lora_quantize.cleanup_pending", level=logging.WARNING, data={"reason": "local_io"})

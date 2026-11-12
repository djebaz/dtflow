"""Writer of the Draw Things LoRA ``.ckpt`` (an SQLite file with a ``tensors`` table).

The container logic is moved unchanged from the old single-file converter. The additions are
cooperative cancel between tensors and the sha256/size of the finished file, computed on the
closed ``.part`` file before it is renamed (so the caller needs no second hashing pass).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import struct
import tempfile
from pathlib import Path

import numpy as np

from dtflow.services.lora_convert.context import ConversionContext
from dtflow.services.lora_convert.errors import ConversionCancelled, DestinationError

# ccv flags as stored in the tensors table (see inspect_ckpt.py for the datatype list).
DATATYPE_F16 = 0x20000
FORMAT_NHWC = 0x01  # what Draw Things writes for these 2-D weights (checked on a Krea 2 ckpt)
FORMAT_NCHW = 0x02  # assumed for 4-D (convolution) tensors, not checked against a reference
TYPE_CPU = 0x01
MAX_DIM_ALLOC = 12


def _encode_dims(shape: tuple[int, ...]) -> bytes:
    dims = list(shape) + [0] * (MAX_DIM_ALLOC - len(shape))
    return struct.pack(f"<{MAX_DIM_ALLOC}i", *dims)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_ckpt(path: Path, tensors: dict[str, np.ndarray], ctx: ConversionContext | None = None) -> tuple[str, int]:
    """Write ``tensors`` to ``path`` (never overwriting). Returns (sha256, size) of the file."""
    if path.exists():
        raise DestinationError(f"{path} already exists; refusing to overwrite.")
    tmp = None
    done = False
    total = len(tensors)

    def rows():
        for index, (name, array) in enumerate(tensors.items()):
            if ctx is not None:
                ctx.step(index, total)
            yield (
                name,
                TYPE_CPU,
                FORMAT_NHWC if array.ndim <= 2 else FORMAT_NCHW,
                DATATYPE_F16,
                _encode_dims(tuple(array.shape)),
                np.ascontiguousarray(array, dtype="<f2").tobytes(),
            )

    try:
        # Unique temp name: two conversions to the same destination must not share (and delete) one file.
        handle, tmp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".part", dir=path.parent)
        os.close(handle)
        tmp = Path(tmp_name)
        # The connection must be closed before the file is renamed or deleted (Windows locks it).
        con = sqlite3.connect(str(tmp))
        try:
            con.execute(
                "CREATE TABLE tensors (name TEXT, type INTEGER, format INTEGER, datatype INTEGER, "
                "dim BLOB, data BLOB, PRIMARY KEY (name))"
            )
            con.executemany("INSERT INTO tensors VALUES (?, ?, ?, ?, ?, ?)", rows())
            con.commit()
        finally:
            con.close()
        if ctx is not None:
            ctx.check_cancel()
        sha256, size = _sha256_file(tmp), tmp.stat().st_size
        if path.exists():  # appeared while we were writing: never overwrite
            raise DestinationError(f"{path} already exists; refusing to overwrite.")
        tmp.rename(path)  # fails on Windows if the target exists; on POSIX it was checked above
        done = True
        return sha256, size
    except ConversionCancelled:
        raise
    except OSError as error:
        raise DestinationError(f"Cannot write {path}: {error}") from error
    except sqlite3.Error as error:
        raise DestinationError(f"Cannot write {path}: {error}") from error
    finally:
        if not done and tmp is not None:
            tmp.unlink(missing_ok=True)

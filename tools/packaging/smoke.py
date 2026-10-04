"""Non-generative environment smoke for DTFlow packaging evidence. Makes no network connection."""

import json
import os
import platform
import sqlite3
import ssl
import sys
import tempfile
from importlib import metadata
from pathlib import Path


def check(name, fn):
    try:
        return {"check": name, "status": "ok", "detail": fn()}
    except Exception as exc:  # report, never hide, the failure
        return {"check": name, "status": "fail", "detail": f"{type(exc).__name__}: {exc}"}


def versions():
    # Static imports so a freezing tool can discover every module; string-based
    # importlib calls are invisible to its analysis.
    import betterproto
    import drawthings_py
    import flatbuffers
    import fpzip
    import grpclib
    import numpy
    import PIL
    import strictyaml
    import tqdm

    modules = {
        "numpy": numpy,
        "pillow": PIL,
        "fpzip": fpzip,
        "flatbuffers": flatbuffers,
        "grpclib": grpclib,
        "betterproto": betterproto,
        "drawthings-py": drawthings_py,
        "strictyaml": strictyaml,
        "tqdm": tqdm,
    }
    out = {}
    for dist, module in modules.items():
        try:
            out[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            # Frozen builds do not bundle package metadata by default. Report that
            # explicitly instead of failing the import check or inventing a version.
            out[dist] = f"imported; metadata unavailable (module version: {getattr(module, '__version__', 'n/a')})"
    return out


def numpy_cpu():
    from numpy._core._multiarray_umath import __cpu_features__ as f

    return sorted(k for k, v in f.items() if v)


def fpzip_roundtrip():
    import fpzip
    import numpy as np

    a = np.linspace(0, 1, 4096, dtype=np.float32).reshape(1, 64, 64, 1)
    b = fpzip.decompress(fpzip.compress(a, order="C"), order="C")
    return {"equal": bool(np.array_equal(a, b)), "shape": list(b.shape)}


def pillow_png():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 20, 30)).save(buf, "PNG")
    return {"png_bytes": len(buf.getvalue()), "size": list(Image.open(io.BytesIO(buf.getvalue())).size)}


def sqlite_wal():
    with tempfile.TemporaryDirectory() as d:
        con = sqlite3.connect(Path(d) / "t.db")
        mode = con.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        con.execute("PRAGMA synchronous=FULL")
        con.execute("CREATE TABLE t(x)")
        con.execute("INSERT INTO t VALUES (1)")
        con.commit()
        con.close()
    return {"sqlite_version": sqlite3.sqlite_version, "journal_mode": mode}


def tls_trust():
    import drawthings_py.grpc.grpc_service as g

    ctx = ssl.create_default_context()
    return {
        "default_verify_paths": ssl.get_default_verify_paths().cafile,
        "system_ca_count": len(ctx.get_ca_certs()),
        "bundled_ca_exists": Path(g.cert_path).exists(),
        "openssl": ssl.OPENSSL_VERSION,
    }


def user_paths():
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    p = Path(base) / "DTFlow-smoke"
    p.mkdir(exist_ok=True)
    f = p / "probe.txt"
    f.write_text("ok", encoding="utf-8")
    f.unlink()
    p.rmdir()
    return {"writable_base": base}


results = [
    {
        "check": "environment",
        "status": "ok",
        "detail": {
            "python": sys.version,
            "executable": sys.executable,
            "frozen": bool(getattr(sys, "frozen", False)),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
    },
    check("package_versions", versions),
    check("numpy_cpu_features", numpy_cpu),
    check("fpzip_roundtrip", fpzip_roundtrip),
    check("pillow_png", pillow_png),
    check("sqlite_wal_full", sqlite_wal),
    check("tls_trust_resources", tls_trust),
    check("per_user_path_writable", user_paths),
]
print(json.dumps(results, indent=2))
sys.exit(0 if all(r["status"] == "ok" for r in results) else 1)

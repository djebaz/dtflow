"""List the tensors inside a Draw Things .ckpt file (SQLite/NNC), standard library only.

Usage:
    python inspect_ckpt.py A.ckpt [B.ckpt]

With one file it prints the schema, a summary and every tensor.
With two files it also compares them: names only in one file, and tensors
whose shape or datatype differ. Read-only; the files are never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import struct
import sys
from pathlib import Path

# ccv datatype flags as stored by s4nnc (best effort; unknown values print as hex).
DATATYPES = {
    0x01000: "u8",
    0x02000: "i32",
    0x04000: "f32",
    0x08000: "i64",
    0x10000: "f64",
    0x20000: "f16",
    0x40000: "qx",
    0x80000: "bf16",
}


def open_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def decode_dims(blob: bytes | None) -> tuple[int, ...]:
    if not blob:
        return ()
    count = len(blob) // 4
    dims = struct.unpack(f"<{count}i", blob[: count * 4])
    return tuple(d for d in dims if d > 0)


def read_tensors(path: Path, verbose: bool) -> dict[str, dict]:
    con = open_readonly(path)
    try:
        if verbose:
            print(f"== {path.name} ({path.stat().st_size} bytes)")
            for name, sql in con.execute("select name, sql from sqlite_master"):
                print(f"schema {name}: {sql}")
        columns = [r[1] for r in con.execute("pragma table_info(tensors)")]
        if not columns:
            raise SystemExit(f"{path}: no 'tensors' table, not a Draw Things ckpt?")
        want = [c for c in ("name", "type", "format", "datatype", "dim") if c in columns]
        query = f"select {', '.join(want)}, length(data), substr(data, 1, 8) from tensors"
        out: dict[str, dict] = {}
        hashes = {n: hashlib.sha256(d or b"").hexdigest() for n, d in con.execute("select name, data from tensors")}
        for row in con.execute(query):
            rec = dict(zip(want, row[: len(want)], strict=True))
            size, head = row[len(want)], row[len(want) + 1]
            dtype = rec.get("datatype")
            out[rec["name"]] = {
                "shape": decode_dims(rec.get("dim")),
                "datatype": DATATYPES.get(dtype, hex(dtype) if isinstance(dtype, int) else "?"),
                "format": rec.get("format"),
                "type": rec.get("type"),
                "bytes": size or 0,
                "head": bytes(head).hex() if head else "",
                "sha": hashes[rec["name"]],
            }
        return out
    finally:
        con.close()


def describe(t: dict) -> str:
    shape = "x".join(map(str, t["shape"])) or "-"
    return f"{t['datatype']:>5} {shape:<22} {t['bytes']:>10} B  head={t['head']}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("a", type=Path)
    ap.add_argument("b", type=Path, nargs="?")
    args = ap.parse_args()

    a = read_tensors(args.a, verbose=True)
    total = sum(t["bytes"] for t in a.values())
    print(f"tensors: {len(a)}, tensor bytes: {total}")
    kinds: dict[str, int] = {}
    for t in a.values():
        kinds[t["datatype"]] = kinds.get(t["datatype"], 0) + 1
    print("datatypes:", kinds)
    for name in sorted(a):
        print(f"  {name}\n      {describe(a[name])}")

    if args.b:
        b = read_tensors(args.b, verbose=True)
        print(f"\n== compare {args.a.name} (A) vs {args.b.name} (B)")
        only_a, only_b = sorted(set(a) - set(b)), sorted(set(b) - set(a))
        print(f"only in A: {len(only_a)}, only in B: {len(only_b)}")
        for n in only_a[:50]:
            print("  A only:", n)
        for n in only_b[:50]:
            print("  B only:", n)
        diffs = [
            n for n in sorted(set(a) & set(b)) if (a[n]["shape"], a[n]["datatype"]) != (b[n]["shape"], b[n]["datatype"])
        ]
        print(f"shared tensors with different shape/datatype: {len(diffs)}")
        for n in diffs[:50]:
            print(f"  {n}\n      A {describe(a[n])}\n      B {describe(b[n])}")
        content = [n for n in sorted(set(a) & set(b)) if a[n]["sha"] != b[n]["sha"]]
        print(f"shared tensors with different data bytes: {len(content)} of {len(set(a) & set(b))}")
        for n in content[:20]:
            print(f"  {n}: A head={a[n]['head']} B head={b[n]['head']}")
        print(f"tensor bytes A: {total}, B: {sum(t['bytes'] for t in b.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

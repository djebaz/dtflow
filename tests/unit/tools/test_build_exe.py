"""Build stamping: artifact naming, generated identity module and its use by dtflow.build."""

import importlib
import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "tools/packaging/build_exe.py"
spec = importlib.util.spec_from_file_location("dtflow_build_exe", SCRIPT)
assert spec is not None and spec.loader is not None
build_exe = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = build_exe
spec.loader.exec_module(build_exe)

COMMIT = "8c4e78718f4984746e33b83707cb2bf2292dd1d0"


def test_artifact_name_follows_the_identity_convention():
    assert build_exe.artifact_name("0.1.0", COMMIT, "Windows", "AMD64") == "dtflow-0.1.0-g8c4e787-windows-x64.exe"
    assert build_exe.artifact_name("0.1.0", COMMIT, "Linux", "aarch64") == "dtflow-0.1.0-g8c4e787-linux-arm64"


def test_stamped_module_replaces_unknown_identity(monkeypatch, tmp_path):
    import dtflow.build as build

    module = tmp_path / "dtflow_buildinfo_stub.py"
    module.write_text(f'SOURCE_COMMIT = "{COMMIT}"\nSOURCE_STATUS = "clean"\n', encoding="utf-8")
    spec = importlib.util.spec_from_file_location("dtflow._buildinfo", module)
    assert spec is not None and spec.loader is not None
    stub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stub)
    monkeypatch.setitem(sys.modules, "dtflow._buildinfo", stub)
    try:
        stamped = importlib.reload(build)
        assert stamped.identity()["source_commit"] == COMMIT
        assert stamped.display_identity().endswith("(g8c4e787; source status clean)")
    finally:
        monkeypatch.delitem(sys.modules, "dtflow._buildinfo")
        importlib.reload(build)
    assert build.identity()["source_commit"] == "unknown"

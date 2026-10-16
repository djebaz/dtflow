"""Read-only access aliases for legacy Windows references on verified WSL mounts.

Never persist these aliases or use them to authorize export, editing or Trash.
Discovery is an operation-scoped snapshot, not a process-global mount cache.
"""

import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

MAX_MOUNT_BYTES = 1024 * 1024


def foreign_windows_path(value: str) -> bool:
    """Windows references must not become relative Linux filenames by accident."""
    return os.name != "nt" and bool(PureWindowsPath(value).drive or "\\" in value)


def _unescape(value: str) -> str:
    return re.sub(r"\\(040|011|012|134)", lambda match: chr(int(match[1], 8)), value)


@dataclass(frozen=True)
class ArtifactReadPaths:
    mounts: tuple[tuple[str, str], ...] = ()

    @classmethod
    def discover(cls):
        """Read a bounded Linux mount snapshot only on WSL; never invoke a shell."""
        if os.name == "nt" or "microsoft" not in platform.release().lower():
            return cls()
        try:
            with Path("/proc/self/mountinfo").open("rb") as stream:
                data = stream.read(MAX_MOUNT_BYTES + 1)
            if len(data) > MAX_MOUNT_BYTES:
                return cls()
            mounts = []
            for line in data.decode("utf-8").splitlines():
                before, separator, after = line.partition(" - ")
                fields, filesystem = before.split(), after.split()
                if not separator or len(fields) < 6 or len(filesystem) < 3:
                    continue
                # WSL 1 exposes whole Windows drives as drvfs. Do not infer
                # arbitrary 9p/bind/network mounts from their directory names.
                source = _unescape(filesystem[1])
                if filesystem[0] != "drvfs" or fields[3] != "/":
                    continue
                drive = re.fullmatch(r"([A-Za-z]):[\\/]", source)
                root = Path(_unescape(fields[4]))
                if drive and root.is_absolute() and root.resolve() == root:
                    mounts.append((drive[1].upper(), str(root)))
            return cls(tuple(mounts))
        except (OSError, ValueError, UnicodeError):
            return cls()

    def resolve(self, stored: str) -> str:
        """Return an access path or raise OSError; stored provenance stays intact."""
        if len(stored) > 32768 or "\0" in stored:
            raise OSError("Invalid output path.")
        if not foreign_windows_path(stored):
            return stored
        drive = re.match(r"^([A-Za-z]):[\\/](.*)$", stored)
        if not drive:
            raise OSError("Unsupported foreign output path.")
        parts = re.split(r"[\\/]", drive[2])
        if len(parts) > 256 or any(
            part in ("", ".", "..") or ":" in part or part.endswith((".", " ")) for part in parts
        ):
            raise OSError("Unsafe foreign output path.")
        roots = {root for letter, root in self.mounts if letter == drive[1].upper()}
        if len(roots) != 1:
            raise OSError("Windows output drive is unavailable or ambiguously mounted.")
        root = Path(roots.pop())
        if root.resolve() != root:
            raise OSError("Windows output mount is redirected.")
        target = root
        for part in parts:
            target = target / part
            if target.is_symlink():
                raise OSError("Redirected output paths are not supported.")
        if not target.resolve().is_relative_to(root):
            raise OSError("Output path escapes its mounted drive.")
        return str(target)

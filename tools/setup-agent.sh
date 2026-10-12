#!/usr/bin/env bash
# Prepare a DTFlow development/agent environment; never run tests or generation.
set -euo pipefail

export UV_LINK_MODE=copy
export QT_QPA_PLATFORM=offscreen
export UV_SYSTEM_CERTS=true
export UV_PYTHON_PREFERENCE=only-managed

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="${DTFLOW_PROJECT_ROOT:-$script_dir/..}"
if [[ ! -d "$repo_root" ]]; then
    printf 'DTFlow project directory does not exist: %s\n' "$repo_root" >&2
    exit 1
fi
cd -- "$repo_root"
if [[ ! -f pyproject.toml || ! -f uv.lock || ! -f .python-version ]]; then
    printf 'Expected pyproject.toml, uv.lock and .python-version in %s\n' "$PWD" >&2
    exit 1
fi

case "$(uname -s)" in
    Linux*)
        if ! command -v apt-get >/dev/null 2>&1; then
            printf 'Linux setup requires apt-get (Debian/Ubuntu). Install Qt libraries manually on other distributions.\n' >&2
            exit 1
        fi
        apt_command=(apt-get)
        if (( EUID != 0 )); then
            if ! command -v sudo >/dev/null 2>&1 || ! sudo -n true; then
                printf 'Installing Qt libraries requires root or non-interactive sudo.\n' >&2
                exit 1
            fi
            apt_command=(sudo -n apt-get)
        fi
        "${apt_command[@]}" -o APT::Sandbox::User=root update -q
        "${apt_command[@]}" -o APT::Sandbox::User=root install -y -q libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1
        ;;
    Darwin*|MINGW*|MSYS*|CYGWIN*)
        printf 'Skipping Linux system libraries on %s.\n' "$(uname -s)"
        ;;
    *)
        printf 'Unsupported setup platform: %s\n' "$(uname -s)" >&2
        exit 1
        ;;
esac

if ! command -v uv >/dev/null 2>&1; then
    # Bootstrap uv separately; do not install project dependencies into system Python.
    bootstrap_python="$(command -v python3 || command -v python)" || {
        printf 'Install Python with venv/pip support or install uv before setup.\n' >&2
        exit 1
    }
    "$bootstrap_python" -m venv tmp/agent-bootstrap
    if [[ -x tmp/agent-bootstrap/bin/python ]]; then
        bootstrap_bin="$PWD/tmp/agent-bootstrap/bin"
    else
        bootstrap_bin="$PWD/tmp/agent-bootstrap/Scripts"
    fi
    "$bootstrap_bin/python" -m pip install uv==0.12.23
    export PATH="$bootstrap_bin:$PATH"
fi

python_version="$(tr -d '\r\n' < .python-version)"
if [[ -z "$python_version" ]]; then
    printf '.python-version must specify the project Python version.\n' >&2
    exit 1
fi
uv python install "$python_version"
uv sync --frozen --python "$python_version"
uv run --frozen --python "$python_version" python -c 'import sqlite3, sys; print("python", sys.version.split()[0]); print("sqlite", sqlite3.sqlite_version)'

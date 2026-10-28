#!/bin/bash
# Launch Prompt Enhancer from Terminal or by double-clicking this file in Finder.
set -Eeuo pipefail

launcher_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
launcher_python="$launcher_dir/.venv/bin/python"
bootstrap_dir="$launcher_dir/.bootstrap"
download_dir=""

cleanup() {
    if [[ -n "$download_dir" ]]; then
        rm -rf -- "$download_dir"
    fi
}

failed() {
    local status=$?
    printf '\nSetup or launch failed. Check the error above, then double-click run.command to retry.\n' >&2
    if [[ -t 0 ]]; then
        read -r -p 'Press Enter to close this window.' || true
    fi
    exit "$status"
}
trap cleanup EXIT
trap failed ERR

prepare_downloads() {
    if [[ -z "$download_dir" ]]; then
        download_dir="$(mktemp -d)"
    fi
}

ensure_uv() {
    if [[ ! -x "$bootstrap_dir/bin/uv" ]]; then
        printf 'Installing the Python setup tool (uv) …\n'
        prepare_downloads
        curl --fail --show-error --location --retry 3 \
            https://astral.sh/uv/install.sh -o "$download_dir/install-uv.sh"
        env UV_UNMANAGED_INSTALL="$bootstrap_dir/bin" sh "$download_dir/install-uv.sh"
    fi
}

# Finder launches may not include Homebrew, where Ollama is installed.
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin"
cd -- "$launcher_dir"

if [[ "$(uname -s)" != Darwin ]]; then
    printf 'This launcher requires macOS.\n' >&2
    false
fi

# Keep downloaded Python and setup caches inside this checkout. No shell-profile
# changes, system Python, Homebrew, developer tools, or administrator access needed.
export UV_PYTHON_INSTALL_DIR="$bootstrap_dir/python"
export UV_CACHE_DIR="$bootstrap_dir/cache"
export UV_PYTHON_INSTALL_BIN=0

if ! "$launcher_python" -c 'import sys; assert sys.version_info >= (3, 10); assert sys.prefix != sys.base_prefix' 2>/dev/null; then
    ensure_uv
    if [[ -e "$launcher_dir/.venv" || -L "$launcher_dir/.venv" ]]; then
        backup_dir="$(mktemp -d "$launcher_dir/.venv-backup.XXXXXX")"
        mv -- "$launcher_dir/.venv" "$backup_dir/environment"
        printf 'Saved the unusable environment in %s\n' "$backup_dir"
    fi
    printf 'Setting up Python 3.12 and .venv …\n'
    "$bootstrap_dir/bin/uv" venv --python 3.12 --managed-python "$launcher_dir/.venv"
fi

# Reinstall when dependency declarations change, or an installation is incomplete.
requirements_hash="$launcher_dir:$(shasum -a 256 pyproject.toml)"
installed_hash="$(cat "$launcher_dir/.venv/.launcher-requirements" 2>/dev/null || true)"
if [[ "$requirements_hash" != "$installed_hash" ]] || \
    ! "$launcher_python" -c 'import prompt_enhancer.cli' 2>/dev/null; then
    ensure_uv
    printf 'Installing Prompt Enhancer and its dependencies …\n'
    "$bootstrap_dir/bin/uv" pip install --python "$launcher_python" --editable "$launcher_dir"
    "$launcher_python" -c 'import prompt_enhancer.cli'
    printf '%s\n' "$requirements_hash" > "$launcher_dir/.venv/.launcher-requirements"
fi

if [[ -z "${PROMPT_ENHANCER_OLLAMA_BIN:-}" ]]; then
    ollama_binary="$(command -v ollama || true)"
    if [[ -z "$ollama_binary" ]]; then
        for candidate in "/Applications/Ollama.app/Contents/Resources/ollama" \
            "$HOME/Applications/Ollama.app/Contents/Resources/ollama" \
            "$bootstrap_dir/ollama/Ollama.app/Contents/Resources/ollama"; do
            if [[ -x "$candidate" ]]; then
                ollama_binary="$candidate"
                break
            fi
        done
    fi
    if [[ -z "$ollama_binary" ]]; then
        printf 'Downloading Ollama (first launch only) …\n'
        prepare_downloads
        curl --fail --show-error --location --retry 3 \
            https://ollama.com/download/Ollama-darwin.zip -o "$download_dir/ollama.zip"
        mkdir -p "$bootstrap_dir/ollama"
        ditto -x -k "$download_dir/ollama.zip" "$bootstrap_dir/ollama"
        ollama_binary="$bootstrap_dir/ollama/Ollama.app/Contents/Resources/ollama"
        test -x "$ollama_binary"
    fi
    export PROMPT_ENHANCER_OLLAMA_BIN="$ollama_binary"
fi

cleanup
download_dir=""
printf 'Starting Prompt Enhancer …\n'
"$launcher_python" -m prompt_enhancer "$@"

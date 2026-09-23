#!/bin/sh
# Locate a Python 3.11+ interpreter (tomllib) without trusting PATH, then run the
# plugin. Herdr 0.9 passes the user's PATH, but launchers on other machines may not.
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

usable() {
    [ -n "$1" ] && [ -x "$1" ] || return 1
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}

python=""
if [ -n "${HERDR_FEATURE_PYTHON:-}" ]; then
    if usable "$HERDR_FEATURE_PYTHON"; then
        python="$HERDR_FEATURE_PYTHON"
    else
        echo "HERDR_FEATURE_PYTHON=$HERDR_FEATURE_PYTHON is not a Python 3.11+ interpreter." >&2
        exit 1
    fi
fi

if [ -z "$python" ]; then
    for candidate in \
        "$(command -v python3 2>/dev/null || true)" \
        /opt/homebrew/bin/python3 \
        /usr/local/bin/python3 \
        "$HOME/.local/share/mise/shims/python3" \
        "$HOME/.pyenv/shims/python3" \
        "$HOME/.local/bin/python3" \
        /usr/bin/python3; do
        if usable "$candidate"; then
            python="$candidate"
            break
        fi
    done
fi

if [ -z "$python" ]; then
    cat >&2 <<MSG
herdr-feature needs Python 3.11 or newer and could not find one.

Install one (brew install python) or point the plugin at it:
  export HERDR_FEATURE_PYTHON=/path/to/python3
MSG
    printf 'Enter to close.' >&2
    read -r _ 2>/dev/null || true
    exit 1
fi

export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$python" -m herdr_feature "$@"

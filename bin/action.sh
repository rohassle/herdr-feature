#!/bin/sh
# Actions run without a TTY, so every action opens the plugin's popup pane and forwards
# which action was invoked plus the invoker's workspace. The popup is a singleton modal:
# when another modal is open Herdr answers ui_busy, which we turn into a toast.
set -u

herdr="${HERDR_BIN_PATH:-herdr}"
action="${HERDR_PLUGIN_ACTION_ID:-menu}"

set --
# Popups always attach to the active pane; Herdr rejects --workspace for them, so the
# invoker's workspace travels in the environment instead.
if [ -n "${HERDR_WORKSPACE_ID:-}" ]; then
    set -- "$@" --env "FEATURE_WORKSPACE_ID=$HERDR_WORKSPACE_ID"
fi
if [ -n "${HERDR_PLUGIN_CONTEXT_JSON:-}" ]; then
    set -- "$@" --env "FEATURE_INVOKER_CONTEXT=$HERDR_PLUGIN_CONTEXT_JSON"
fi

err=$("$herdr" plugin pane open --plugin "${HERDR_PLUGIN_ID:-feature}" --entrypoint ui \
    --env "FEATURE_ACTION=$action" "$@" 2>&1 >/dev/null)
status=$?
if [ "$status" -ne 0 ]; then
    case "$err" in
        *ui_busy*)
            "$herdr" notification show "Feature" --body "Another popup is open. Close it and try again." >/dev/null 2>&1
            exit 0
            ;;
    esac
    printf '%s\n' "$err" >&2
    exit "$status"
fi

#!/bin/sh
# Stand-in for fzf. Reads candidate rows on stdin (key<TAB>display...), pops the first
# line of $FAKE_FZF_QUEUE and prints the keys whose key column contains any of that
# line's comma-separated needles. A line reading ESC cancels like a real Esc.
#
# When the caller passed --expect (FAKE_FZF_EXPECT=1), the line reads
# "<hotkey>|<needles>" and the hotkey ("" for Enter) is printed first, as fzf does.
set -eu
queue="${FAKE_FZF_QUEUE:?}"
line=$(head -n1 "$queue")
tail -n +2 "$queue" > "$queue.tmp" && mv "$queue.tmp" "$queue"
if [ "$line" = "ESC" ]; then
    cat >/dev/null
    exit 130
fi
rows=$(cat)
if [ "${FAKE_FZF_EXPECT:-}" = "1" ]; then
    case "$line" in
        *"|"*) printf '%s\n' "${line%%|*}"; line=${line#*|} ;;
        *) printf '\n' ;;
    esac
fi
[ -n "$line" ] || exit 0
printf '%s\n' "$rows" | cut -f1 | while IFS= read -r key; do
    old_ifs=$IFS; IFS=,
    for needle in $line; do
        case "$key" in *"$needle"*) printf '%s\n' "$key"; break;; esac
    done
    IFS=$old_ifs
done

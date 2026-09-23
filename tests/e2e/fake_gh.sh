#!/bin/sh
# Stand-in for the GitHub CLI. `auth status` succeeds. `pr list --head <branch> ...`
# prints the JSON array stored under that branch in $FAKE_GH_ANSWERS (a JSON object
# keyed by branch name); a branch with no entry has no pull request ([]). When
# FAKE_GH_FAIL is set, every command fails with that text, like an offline gh.
set -eu
if [ -n "${FAKE_GH_FAIL:-}" ]; then
    printf '%s\n' "$FAKE_GH_FAIL" >&2
    exit 1
fi
case "$1" in
    auth) exit 0 ;;
    pr)
        branch=""
        while [ $# -gt 0 ]; do
            if [ "$1" = "--head" ]; then branch=$2; fi
            shift
        done
        python3 - "$branch" "${FAKE_GH_ANSWERS:?}" <<'EOF'
import json, sys
branch, path = sys.argv[1], sys.argv[2]
answers = json.load(open(path))
print(json.dumps(answers.get(branch, [])))
EOF
        ;;
    *) echo "fake gh: unsupported $*" >&2; exit 1 ;;
esac

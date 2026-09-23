# An existing branch is reused, never refused

If the branch a worktree would be created on already exists locally, the worktree checks
it out. If it exists only on `origin`, a local tracking branch is created from it. Only
when neither exists is a new branch created from a freshly fetched `origin/<default>`
with `--no-track`.

Refusing an existing branch (as some tools do) makes "reopen the thread I removed last
week but kept the branches of" a chore, and blocks picking up a teammate's branch. Git's
own guard remains: a branch that is already checked out somewhere cannot be checked out
twice, and preflight turns that into a prompt for a suffix.

## Consequences

The manifest records `branch_created` per entry. Only branches the plugin created are
ever offered for deletion by `remove`; reused branches are always kept.

Because thread `x` with suffix `y` and thread `x-y` would produce the same branch in
the same repository, preflight enforces `(repository, branch)` uniqueness across every
manifest under the threads directory.

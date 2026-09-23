# A worktree is done when its pull request is merged

The popup is a board of work threads, each running through several repositories, with a
progress bar per thread. Progress needs a definition of "done" per worktree.

Git history alone cannot give one that matches how the work actually lands. A squash
merge leaves no ancestor relationship, a rebase-merge rewrites the commits, and a branch
deleted on the remote after merging looks the same as one never pushed. What people mean
by done is that the pull request was merged. So the plugin tracks the pull request whose
head is the worktree's branch, through the GitHub CLI (`gh pr list --head <branch>`), and
a worktree is done when that pull request is in state `MERGED`. A thread is done when
every worktree is.

GitHub access is a requirement, not an option. There is no git-only fallback: a wrong
"done" is worse than an "unknown". Without `gh`, or logged out, the board still lists and
opens threads, progress reads `unknown` and refresh aborts with `gh`'s own message.

Lookups only happen on an explicit refresh (`ctrl-r` on the board, `herdr-workthreads refresh`
from scripts). Opening the board never touches the network. Each result is cached in the
manifest as the worktree's `pr` object (`number`, `url`, `state`, `draft`, `review`,
`title`, `merged_at`, `error`, `checked_at`), and the board shows how old the cache is.

## Consequences

- A worktree with no pull request yet is `no PR`; one whose repository is not on GitHub
  gets an `error` from `gh` and counts as not merged. Both are visible on the board, not
  hidden.
- Refresh also fetches every repository's default branch, so the local `unpushed` state
  stays truthful alongside the pull request state.
- A done thread offers removal when opened from the board, with branch deletion
  defaulting to yes; nothing is removed without the usual confirmations (ADR 0002).
- `pr` is an optional manifest field: older plugin versions ignore it.

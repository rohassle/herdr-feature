# A thread root holds real worktrees, not symlinks

A thread needs its repositories visible to one agent as siblings under a single working
directory. Each repository is materialised as a real Git worktree inside a plain folder,
the thread root.

Symlinks into a shared folder were rejected: `rg` and `fd` do not follow symlinks by
default, which would silently hide whole repositories from an agent's search, the very
capability the thread exists to provide. Keeping checkouts next to their origin
repositories was rejected because nothing then contains the group.

## Consequences

The thread root is not a repository. `git` commands run in the worktrees, never in the
root. The root carries the manifest and nothing else.

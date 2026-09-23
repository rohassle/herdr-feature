# Closing a workspace never deletes a feature

Closing a feature's workspace leaves every file on disk. Removal happens only through the
explicit `remove` command, which shows the state of every worktree first.

Deleting on close when everything is "clean and pushed" was rejected: that describes only
tracked content. Worktrees accumulate ignored files that exist nowhere else (`.env`, local
databases, virtualenvs, caches), and `workspace.closed` is a post-hoc event that cannot be
vetoed. It also matches Herdr's own behaviour for its native worktrees.

## Consequences

Closed features accumulate until removed, so `open` (to come back) and `remove` (to let
go) are first-class commands, not polish.

## Update (close command)

`close` is the explicit form of this rule and the mirror of `open`: it shuts every Herdr
workspace of a feature (the feature workspace and the nested per-repository ones) and
touches nothing on disk. Parking a thread of work is `close`; finishing it is `remove`.

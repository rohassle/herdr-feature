# A feature is a flat top-level workspace

Herdr indents a worktree's workspace under its parent repository's workspace in the
sidebar, but only for workspaces created by its own worktree machinery, which binds
exactly one checkout to a workspace. A feature spans several repositories and has no
natural anchor, so the plugin creates an ordinary workspace with `workspace create --cwd
<feature root> --label <feature>`.

The alternative, creating the workspace through `worktree create` on one anchor
repository and then replacing its tab, relies on undocumented behaviour and forces one
repository to be special.

## Consequences

The sidebar shows the feature by name at top level, without a branch or git status badge.
That is a fair trade for robustness across Herdr upgrades.

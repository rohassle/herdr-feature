# Per-repository worktree workspaces are optional and nest under their repository

Extends [0003](0003-flat-workspace-no-nesting.md).

Herdr indents a worktree workspace under its repository's workspace in the sidebar. The
grouping key is the repository (the path of its primary `.git`), and the parent must be a
checkout of that same repository. A feature root holds worktrees of several repositories
and is not a repository itself, so it can never be a sidebar parent; Herdr exposes no API
to declare one (upstream: herdrdev/herdr discussions #4513 and #1953).

What Herdr can do is open each worktree entry as its own worktree workspace. The plugin
does this with `herdr worktree open --cwd <repository> --path <feature root>/<folder>`,
which binds the workspace to the checkout and nests it under the repository's workspace,
opening that parent workspace first when none is open. The `workspaces` config key picks
the shape:

- `feature` (default): the flat feature workspace of 0003, and nothing else.
- `repos`: only the nested per-repository workspaces.
- `both`: the feature workspace plus the nested ones.

## How the plugin tells them apart

Herdr reports worktree provenance (`worktree.checkout_path`, `is_linked_worktree`) on
`workspace list`, and that provenance survives server restarts. A workspace whose checkout
lies inside a feature root is one of that feature's per-repository workspaces, never its
feature workspace, so the pane scan of 0006 skips it. The manifest keeps no per-entry
workspace hint; the checkout path is the identity.

## Consequences

- A feature is "open" when either kind of workspace is live. `open` re-creates only what
  is missing and is idempotent; `add` opens a nested workspace for each new entry when the
  feature is open; `drop` and `remove` close the nested workspaces they make obsolete.
- Herdr opens a repository's own workspace as the group parent. The plugin never closes
  that parent: it is the user's repository workspace and may hold their work.
- Nested workspaces are labelled with the feature name (`<feature>@<suffix>` for extra
  worktrees of the same repository), so the sidebar reads `repo > feature`.
- A failure to open one nested workspace is reported and does not fail the command; the
  worktree exists and `open` retries later.

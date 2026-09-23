# Per-repository worktree workspaces are optional and nest under their repository

Extends [0003](0003-flat-workspace-no-nesting.md).

Herdr indents a worktree workspace under its repository's workspace in the sidebar. The
grouping key is the repository (the path of its primary `.git`), and the parent must be a
checkout of that same repository. A thread root holds worktrees of several repositories
and is not a repository itself, so it can never be a sidebar parent; Herdr exposes no API
to declare one (upstream: herdrdev/herdr discussions #4513 and #1953).

What Herdr can do is open each worktree entry as its own worktree workspace. The plugin
does this with `herdr worktree open --cwd <repository> --path <thread root>/<folder>`,
which binds the workspace to the checkout and nests it under the repository's workspace,
opening that parent workspace first when none is open. The `workspaces` config key picks
the shape:

- `thread` (default): the flat thread workspace of 0003, and nothing else.
- `repos`: only the nested per-repository workspaces.
- `both`: the thread workspace plus the nested ones.

## How the plugin tells them apart

Herdr reports worktree provenance (`worktree.checkout_path`, `is_linked_worktree`) on
`workspace list`, and that provenance survives server restarts. A workspace whose checkout
lies inside a thread root is one of that thread's per-repository workspaces, never its
thread workspace, so the pane scan of 0006 skips it. The manifest keeps no per-entry
workspace hint; the checkout path is the identity.

## Consequences

- A thread is "open" when either kind of workspace is live. `open` re-creates only what
  is missing and is idempotent; `add` opens a nested workspace for each new entry when the
  thread is open; `drop` and `remove` close the nested workspaces they make obsolete.
- Herdr opens a repository's own workspace as the group parent. The plugin never closes
  that parent: it is the user's repository workspace and may hold their work.
- Nested workspaces are labelled with the thread name (`<thread>@<suffix>` for extra
  worktrees of the same repository), so the sidebar reads `repo > thread`.
- A failure to open one nested workspace is reported and does not fail the command; the
  worktree exists and `open` retries later.

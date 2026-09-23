# herdr-workthreads

A command center for work that spans repositories, inside [Herdr](https://herdr.dev).
Each work thread holds one Git worktree per repository it touches, opened as Herdr
workspaces and tracked to done through its pull requests.

```
thread>   3 threads · 2 open · 1 done · 4/7 merged   ·   PRs refreshed 12m ago
           enter: open / focus   ctrl-n: new   ctrl-a: add   ctrl-w: close   ctrl-x: remove   ctrl-r: refresh

  pay-1234-retry          ████████░░░░ 2/3   open     3 worktrees · 1 PR open
  search-reindex          ░░░░░░░░░░░░ 0/2   closed   2 worktrees · 2 PRs open
  onboarding-copy         ████████████ 2/2   done     2 worktrees
```

Press `prefix+f`, pick the thread you want to work on, Enter. A worktree is done when its
pull request is merged; a thread is done when every worktree is, and then Enter offers to
remove it.

Herdr's built-in worktree support binds one worktree to one workspace. That is the right
model until a change spans several repositories and the agent working on it can only see
one of them. This plugin puts every worktree of a thread under one folder and gives that
folder one workspace, so a single agent, `rg`, `fd`, and your editor see all of them as
sibling directories.

```
~/.herdr/workthreads/pay-1234-retry/         <- the workspace's tab starts here
├── payments-api/                   <- worktree on pay-1234-retry
├── payments-api@migration/         <- second worktree of the same repo
├── payments-web/                           <- worktree on pay-1234-retry
└── .workthread.json                           <- manifest, the source of truth
```

Closing the workspace never deletes anything. Removal is a separate, explicit, reviewed
command.

Optionally, every worktree of a thread also gets its own Herdr worktree workspace, nested
in the sidebar under its repository's workspace (`workspaces = "both"` in the config):

```
payments-api                          <- the repository's own workspace
  └ pay-1234-retry                    <- worktree workspace for payments-api/
  └ pay-1234-retry@migration          <- worktree workspace for payments-api@migration/
payments-web
  └ pay-1234-retry
pay-1234-retry                        <- the thread workspace, all repos side by side
```

Herdr groups the sidebar by repository, so the nested rows sit under each repository
rather than under the thread. A thread folder that is not itself a repository cannot be
a Herdr group parent; see [docs/adr/0007](docs/adr/0007-optional-nested-worktree-workspaces.md).

## Requirements

- Herdr 0.9.0 or newer
- Python 3.11 or newer (for `tomllib`)
- [`fzf`](https://github.com/junegunn/fzf)
- [`gh`](https://cli.github.com), logged in (`gh auth login`): progress is read from pull requests
- Git 2.31 or newer
- macOS or Linux

The bootstrap probes common install locations for Python and `fzf` and does not rely on
`PATH`. Override with `HERDR_WORKTHREADS_PYTHON` and `HERDR_WORKTHREADS_FZF` if needed.

## Install

```sh
herdr plugin install rohassle/herdr-workthreads      # or: herdr plugin link /path/to/checkout
herdr plugin config-dir workthreads                 # prints where config.toml lives
```

Create `config.toml` there (the first run offers to write this template for you):

```toml
# Folders scanned one level deep for git repositories.
repo_directories = ["~/code"]

# Extra repositories anywhere on disk.
repos = []

# Where thread roots are created.
threads_directory = "~/.herdr/workthreads"

# Prepended to every branch the plugin creates. May contain '/'.
branch_prefix = ""
```

Bind the menu in `~/.config/herdr/config.toml` (every action is also bindable on its own:
`thread.new`, `thread.add`, `thread.open`, `thread.drop`, `thread.remove`):

```toml
[[keys.command]]
key = "prefix+f"
type = "plugin_action"
command = "workthreads.menu"
description = "work threads board"
```

## Use

Press `prefix+f`. The board lists every thread with a progress bar (`█` merged, `░` not
yet, `?` not refreshed), its status (`open`, `closed`, `done`) and a one-line summary. The
preview on the right shows each worktree's branch, pull request and local state.

| key | on the selected thread |
|---|---|
| `Enter` | open or focus its workspaces; on a `done` thread, offer to remove it (branch deletion defaults to yes) |
| `ctrl-n` | new thread |
| `ctrl-a` | add repositories |
| `ctrl-d` | drop worktrees |
| `ctrl-w` | close its workspaces (files, worktrees and branches are kept) |
| `ctrl-x` | remove it |
| `ctrl-r` | refresh: look up every pull request with `gh`, fetch every repository's default branch |
| `ctrl-o` | open its pull requests in the browser |
| `ctrl-t` | install the `herdr-workthreads` command line |
| `?` or `F1` | help: this table, inside the popup |

Opening the board never touches the network; the header says how old the pull request
information is. Without `gh`, or logged out, the board still opens threads and says why
progress is unknown.

**new**: type a thread name, Tab-mark repositories in `fzf` (the preview shows each
repository's recent commits), confirm the plan. Every repository gets a worktree on the
branch `<branch_prefix><thread>`, created from a freshly fetched `origin/<default>`.
The default branch is detected per repository, so a mix of `main` and `master` works,
and repositories without a remote branch from their local default. When the branch
already exists locally or on `origin` it is reused rather than refused. Creation is all
or nothing: a failure in the third repository removes what the first two created. The
workspace opens focused, with one tab rooted at the thread folder.

**add**: from inside a thread's workspace, Tab-mark more repositories. A repository that
is already part of the thread is marked with `●`; picking it again asks for a short
suffix and creates a second worktree, `<repo>@<suffix>` on `<branch>-<suffix>`.

**open** (Enter): a live thread is focused; a closed one gets its workspaces back. With
nested workspaces enabled, this also re-opens any per-repository workspace that is missing.

**close** (`ctrl-w`): the mirror of open. Every Herdr workspace of the thread is closed,
nothing on disk changes. Park a thread you are not working on; come back to it with Enter.

**drop**: remove individual worktrees from the current thread. Branches are kept.
Worktrees with uncommitted or unpushed work require typing the thread name.

**remove**: show the state of every worktree, confirm, close the workspace (and the nested
per-repository workspaces), remove the worktrees and the folder, then offer to delete the
local branches the plugin itself created. Branches it merely reused, and remote branches,
are never deleted. The repository workspaces Herdr opened as group parents stay open.

If a fetch fails (offline, VPN down) you are asked once whether to continue from the
last fetched state or abort everything.

### What "done" means

A worktree is done when the pull request whose head is its branch is merged on GitHub.
`ctrl-r` (or `herdr-workthreads refresh`) asks `gh pr list --head <branch>` inside each worktree
and caches the answer in the manifest, so the board is instant and honest about its age.
There is no guess from git history: a branch with no pull request reads `no PR`, a lookup
that failed reads its error, both count as not merged. See
[docs/adr/0008](docs/adr/0008-done-means-pr-merged.md).

## Driving it from an agent or a script

`herdr-workthreads` is the non-interactive twin of the popup: every prompt becomes a flag,
mutating commands are dry runs until `--yes`, and `--json` prints a result object on
stdout (progress goes to stderr). Put it on your PATH once, from the menu (`prefix+f`,
then `install-cli`) or without the UI:

```sh
herdr plugin action invoke install-cli --plugin workthreads      # opens the popup
"$(herdr plugin list --plugin workthreads --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["plugins"][0]["plugin_root"])')/bin/herdr-workthreads" install-cli
```

Both create `~/.local/bin/herdr-workthreads` and `~/.claude/skills/herdr-workthreads` as symlinks
into the plugin folder (`--no-skill` to skip the skill, `uninstall-cli` to remove both).

```sh
herdr-workthreads list --json                                  # progress, PRs, workspace ids per thread
herdr-workthreads refresh [--thread pay-1234-retry] --json    # look up pull requests with gh, fetch remotes
herdr-workthreads new --name pay-1234-retry --repo payments-api --repo payments-web --yes --json
herdr-workthreads add --thread pay-1234-retry --repo payments-api --suffix payments-api=migration --yes
herdr-workthreads open --thread pay-1234-retry
herdr-workthreads close --thread pay-1234-retry --yes         # workspaces only; files and branches kept
herdr-workthreads drop --thread pay-1234-retry --worktree payments-api@migration --yes
herdr-workthreads remove --thread pay-1234-retry --yes --delete-branches
```

Inside a thread's workspace `--thread` may be omitted for `add`, `close`, `drop` and `remove`.
`--on-fetch-failure continue` replaces the popup's prompt; `--force` replaces the typed
confirmation for worktrees with unsaved work. A ready-made agent skill lives in
[skills/herdr-workthreads](skills/herdr-workthreads/SKILL.md).

## Configuration

`herdr plugin config-dir workthreads` names the folder holding `config.toml`; the first run
writes a template. See [examples/config.toml](examples/config.toml).

| key | meaning |
|---|---|
| `repo_directories` | folders scanned one level deep for repositories |
| `repos` | extra repositories anywhere on disk |
| `threads_directory` | where thread roots are created (default `~/.herdr/workthreads`) |
| `branch_prefix` | prepended to every branch the plugin creates |
| `workspaces` | `"thread"` (default): one workspace rooted at the thread folder. `"repos"`: one worktree workspace per entry, nested under its repository in the sidebar. `"both"`: both. |

## How it decides which workspace is a thread's

Herdr reassigns workspace ids when its server restarts, so the manifest only keeps a hint.
The plugin scans the working directories of all live panes; a pane inside a thread folder
marks that workspace as the thread's, and the hint is refreshed. See
[docs/adr/0006](docs/adr/0006-workspace-identity-comes-from-pane-cwd.md). Nested
per-repository workspaces are recognised by the checkout path Herdr records on them, and
are never mistaken for the thread workspace.

## Development

Development uses [uv](https://docs.astral.sh/uv/); the plugin itself has no dependencies and
runs on any system Python 3.11+.

```sh
uv sync
uv run python -m unittest discover -s tests -t .   # pure functions and real git in temp dirs
uv run tests/e2e/run.py                            # inside Herdr: creates and removes zz-test-* workspaces
uv run ruff check .
herdr plugin link "$PWD"                           # run the popup from this checkout
```

See [AGENTS.md](AGENTS.md) for the working rules and [ARCHITECTURE.md](ARCHITECTURE.md) for how
the pieces fit.

Actions are not interactive, so the popup can be driven by `herdr plugin action invoke
menu --plugin workthreads`. Plugin stderr lands in `herdr plugin log list --plugin workthreads`.

Vocabulary is in [CONTEXT.md](CONTEXT.md); design decisions in [docs/adr](docs/adr).

## License

MIT

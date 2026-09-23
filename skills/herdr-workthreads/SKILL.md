---
name: herdr-workthreads
description: Work in threads, units of work that run through several Git repositories, managed by the herdr-workthreads plugin for Herdr. Use when asked to start work that touches more than one repository, to add a repository to the current thread, to report where a thread stands (which pull requests are merged), to park or reopen a thread's workspaces, or to tear a finished thread down. Requires running inside Herdr (HERDR_ENV=1) and a logged-in `gh`.
---

# herdr-workthreads

## The idea

Work here is organised in **threads**, not repositories. A thread is one piece of work
with a name (usually the ticket or the outcome), a folder `~/.herdr/workthreads/<name>/`
holding one Git worktree per repository it touches, and Herdr workspaces to work in. The
human picks the thread they want to advance from a board (`prefix+f`); you advance it.

A thread moves through one lifecycle, and your job is to move it along and report where
it is:

```
new ──► work in the worktrees ──► push ──► one pull request per worktree ──► merged ──► done ──► remove
                                                                   ▲
                                              progress = merged worktrees / all worktrees
```

**Done means merged on GitHub.** A worktree is done when the pull request whose head is
its branch is merged. A thread is done when every worktree is. Nothing is inferred from
git history: a branch without a pull request is `no PR`, and a lookup that could not be
made is `unknown`. Both count as not done.

Rules you can rely on, and must keep:

- **Closing never deletes.** `close` shuts workspaces; worktrees, folder and branches stay.
  Only `remove` and `drop` delete, and they refuse unsaved or unpushed work without `--force`.
- **Only branches the plugin created are ever deleted** (`branch_created` in the manifest).
- **Open and close are symmetric.** `open` brings a thread's workspaces up, `close` takes
  them down. Park a thread with `close`; finish it with `remove`.
- **Never bypass Herdr's `workspace_group_close_required`** by closing repository
  workspaces yourself; the plugin closes only the thread's own workspaces.

Check you are inside Herdr first: `test "${HERDR_ENV:-}" = 1`. If `herdr-workthreads` is not on
PATH, run the plugin's `install-cli` action once (`prefix+f`, then `ctrl-t`).

## How to work

1. **Look before you create.** `herdr-workthreads list --json` shows every thread, its
   status (`open`, `closed`), `done`, `progress {merged,total,unknown}`, and per worktree
   its `path`, `branch`, `progress` (`merged` · `open-pr` · `closed-pr` · `no-pr` ·
   `unknown`), `pr` (number, url, state, review) and `workspace_id`. If a thread for the
   task exists, join it; do not start a second one for the same ticket.
2. **Start a thread** with `new`, naming every repository it will touch. Add a repository
   later with `add`; a second worktree of the same repository needs `--suffix REPO=NAME`.
3. **Work inside the worktrees.** Each entry lives at `<root>/<folder>` on its own branch.
   With `workspaces = "repos"` every worktree has its own Herdr workspace nested under
   its repository; with `"thread"`/`"both"` there is also a workspace rooted at the
   thread folder, where cross-repository commands (`rg`, `fd`) see every repository.
4. **Push and open a pull request per worktree**, head = the worktree's branch. That is
   what progress is measured on.
5. **Refresh before you report.** `herdr-workthreads refresh --thread X --json` asks GitHub
   for every pull request and fetches the default branches. The board and `list` read the
   cached answer, so a report without a refresh may be stale.
6. **Park or finish.** `close` when the human moves to another thread; `remove` when the
   thread is done (`--delete-branches` to also drop the branches the plugin created).

## Commands

Every mutating command is a **dry run until you add `--yes`**. Add `--json` for a machine
readable result on stdout; progress lines go to stderr.

```bash
herdr-workthreads list --json                                   # all threads: status, done, progress, PRs, workspaces
herdr-workthreads refresh [--thread X] --json                  # look up PRs with gh + fetch; needs a logged-in gh
herdr-workthreads new --name pay-1234-retry --repo payments-api --repo payments-web            # dry run: shows the plan
herdr-workthreads new --name pay-1234-retry --repo payments-api --repo payments-web --yes --json
herdr-workthreads add --thread pay-1234-retry --repo shared-charts --yes
herdr-workthreads add --thread pay-1234-retry --repo payments-api --suffix payments-api=migration --yes
herdr-workthreads open --thread pay-1234-retry --json          # (re)opens missing workspaces; --focus to switch to it
herdr-workthreads close --thread pay-1234-retry --yes          # closes its workspaces; nothing deleted
herdr-workthreads drop --thread pay-1234-retry --worktree shared-charts --yes
herdr-workthreads remove --thread pay-1234-retry --yes         # add --delete-branches to also delete branches it created
```

- `--repo` takes a repository name from the configured folders or a path. Repeat it.
- `--on-fetch-failure continue` uses the last fetched state when a remote is unreachable;
  the default aborts with nothing created.
- Inside a thread's workspace, `add`, `close`, `drop` and `remove` default to that
  thread, so `--thread` can be omitted.
- `drop`/`remove` refuse worktrees with uncommitted or unpushed work unless `--force`;
  `close`/`remove` refuse to kill working agents unless `--force`.
- `new` and `open` leave the human's focus alone; pass `--focus` only if asked to switch.

## Reporting to the human

Report per thread, in this shape, after a refresh:

```
pay-1234-retry  2/3 merged
  payments-api        #41 merged
  payments-web        #57 open · approved        https://github.com/org/payments-web/pull/57
  shared-charts       no PR · 3 unpushed
```

Lead with what blocks done: worktrees with `no PR`, pull requests with changes requested,
worktrees with unpushed or uncommitted work. Say when a thread is done and can be removed.

## Continuing into Herdr

The JSON result of `new`/`open` carries `workspace_id` (the thread workspace, `null` when
the config opens only nested workspaces) and each worktree's `workspace_id`. Start an agent
in one of them:

```bash
ws=$(herdr-workthreads open --thread X --json | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["workspace_id"] or list(d["repo_workspaces"].values())[0])')
pane=$(herdr pane list --workspace "$ws" | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["panes"][0]["pane_id"])')
herdr agent start worker --kind claude --pane "$pane"
```

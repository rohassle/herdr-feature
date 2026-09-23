# herdr-workthreads: vocabulary

**Thread** (work thread)
A named set of worktrees across repositories, worked on together, surfaced as Herdr
workspaces and tracked to done through its pull requests. Identified by its name, which is also the workspace label and the folder name.
_Avoid_: project, space, group, task, feature (the old name).

**Thread root**
The folder `<threads_directory>/<thread>/` that holds the thread's worktrees as direct
children plus the manifest. It is not itself a repository.
_Avoid_: container, parent, checkout dir.

**Worktree entry**
One (repository, branch) checkout inside a thread root. A thread may hold several
entries for the same repository; each has its own branch and folder.
_Avoid_: repo (the repository is what the entry is a worktree *of*).

**Suffix**
The short name that distinguishes a second worktree of the same repository within a
thread: folder `<repo>@<suffix>`, branch `<prefix><thread>-<suffix>`.

**Branch prefix**
Configured text prepended to every branch the plugin creates. May contain `/`.

**Manifest**
`.workthread.json` in the thread root. The source of truth for what a thread contains and
which branches the plugin created (and may therefore delete).

**Live / closed**
A thread is live when a Herdr workspace currently holds a pane whose working directory
is inside the thread root. Closed means no such workspace exists; the files remain.
_Avoid_: orphaned, stale, dead.

**Remove**
The explicit, reviewed deletion of a thread: worktrees, folder, workspace, and optionally
the branches the plugin created.
_Avoid_: prune, clean, gc.

**Drop**
Removing individual worktree entries from a thread while keeping the thread.

## Borrowed words

Herdr owns **workspace** (shown as "space" in the sidebar), **tab**, **pane**, **agent**,
**worktree**, **session**, **plugin**, **action**, and **popup**. This project uses them
only with Herdr's meaning. A thread *has* a workspace and *contains* worktrees; it is
neither.

# herdr-feature: vocabulary

**Feature**
A named set of worktrees across repositories, worked on together, surfaced as one Herdr
workspace. Identified by its name, which is also the workspace label and the folder name.
_Avoid_: project, space, group, task.

**Feature root**
The folder `<features_directory>/<feature>/` that holds the feature's worktrees as direct
children plus the manifest. It is not itself a repository.
_Avoid_: container, parent, checkout dir.

**Worktree entry**
One (repository, branch) checkout inside a feature root. A feature may hold several
entries for the same repository; each has its own branch and folder.
_Avoid_: repo (the repository is what the entry is a worktree *of*).

**Suffix**
The short name that distinguishes a second worktree of the same repository within a
feature: folder `<repo>@<suffix>`, branch `<prefix><feature>-<suffix>`.

**Branch prefix**
Configured text prepended to every branch the plugin creates. May contain `/`.

**Manifest**
`.feature.json` in the feature root. The source of truth for what a feature contains and
which branches the plugin created (and may therefore delete).

**Live / closed**
A feature is live when a Herdr workspace currently holds a pane whose working directory
is inside the feature root. Closed means no such workspace exists; the files remain.
_Avoid_: orphaned, stale, dead.

**Remove**
The explicit, reviewed deletion of a feature: worktrees, folder, workspace, and optionally
the branches the plugin created.
_Avoid_: prune, clean, gc.

**Drop**
Removing individual worktree entries from a feature while keeping the feature.

## Borrowed words

Herdr owns **workspace** (shown as "space" in the sidebar), **tab**, **pane**, **agent**,
**worktree**, **session**, **plugin**, **action**, and **popup**. This project uses them
only with Herdr's meaning. A feature *has* a workspace and *contains* worktrees; it is
neither.

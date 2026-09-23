# herdr-feature became herdr-workthreads

The plugin started as "one Herdr workspace per feature". By the time it had a board with
pull request progress, nested per-repository workspaces, and open/close symmetry, the
name described one early decision rather than the thing. The unit it manages is a thread
of work that runs through several repositories until every pull request is merged, so the
project, plugin id, command and vocabulary all say **thread** now.

| before | after |
|---|---|
| repo `herdr-feature`, plugin id `feature` | `herdr-workthreads`, plugin id `workthreads` |
| `herdr-feature` command, `herdr_feature` package | `herdr-workthreads`, `herdr_workthreads` |
| `--feature X` | `--thread X` |
| `~/.herdr/features/<name>/.feature.json` (`"feature": …`, version 1) | `~/.herdr/workthreads/<name>/.workthread.json` (`"thread": …`, version 2) |
| config `features_directory`, `workspaces = "feature"` | `threads_directory`, `workspaces = "thread"` |
| env `HERDR_FEATURE_*`, `FEATURE_ACTION` | `HERDR_WORKTHREADS_*`, `WORKTHREADS_ACTION` |
| keybinding `feature.menu` | `workthreads.menu` |

## Migration

- A version 1 `.feature.json` is read, migrated and rewritten as `.workthread.json` the
  first time it is loaded; the old file is removed. Interrupted (`creating`) manifests are
  read but left alone for the cleanup path.
- The legacy config keys are honoured with a warning asking to rename them.
- The threads directory is not moved: git worktrees record their absolute path, so a
  move would need `git worktree repair` per repository. Existing installs point
  `threads_directory` at the old folder.
- The plugin id changed, so Herdr keeps the config under a new folder
  (`herdr plugin config-dir workthreads`); the file is copied by hand once.

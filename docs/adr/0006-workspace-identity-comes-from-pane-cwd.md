# Which workspace belongs to a thread is decided by pane working directories

Herdr's workspace ids are not stable: after a server restart, restored workspaces are
renumbered. `workspace list` exposes no working directory. The only durable signal is
where panes are: `pane list` reports every pane's `cwd` across all workspaces.

A thread is live when some pane's working directory lies inside its root (the plugin's
own popup pane is excluded, since it runs in the plugin folder). The manifest keeps the
last known workspace id and label as a hint, used only when no pane matches and the
workspace with that id still carries the expected label; a unique matching label is the
last fallback. Whenever the pane scan resolves a thread, the hint is rewritten.

## Consequences

Renaming a workspace or restarting the server does not lose the association as long as a
shell or agent is still inside the thread folder. A workspace whose every pane has left
the folder reads as closed; `open` then focuses nothing and creates a fresh workspace,
which is the safe direction to be wrong in.

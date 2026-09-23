# One popup entrypoint, dispatched by an environment variable

Plugin actions run without a TTY, and a popup is the only modal terminal Herdr offers a
plugin. Every action therefore runs the same shim, `bin/action.sh`, which opens the
single `ui` popup with `FEATURE_ACTION=<action id>` in its environment. The popup's
Python entrypoint dispatches on that variable.

Six actions still appear in the manifest so each is bindable, but there is one pane
entry and one shim. The `menu` command runs the chosen sub-command in the same process,
because a popup cannot open another popup (`ui_busy`).

The shim also forwards the invoker's workspace id and context JSON, since the popup's own
context may describe the popup rather than the pane the key was pressed in.

## Consequences

Adding a command means one `[[actions]]` entry and one Python module. When another modal
is open the shim turns `ui_busy` into a toast instead of a logged error.

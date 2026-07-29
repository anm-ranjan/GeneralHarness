# MyHarness Rust TUI

`myharness-tui` is a terminal client for a running MyHarness backend, built on
ratatui and tokio. It talks to the backend only over the documented REST and
WebSocket API — it never imports the agent — so it works equally well against a
local backend or one on another machine.

It shows backend health and the project/task/session tree, creates and deletes
projects/tasks/sessions, sends or queues prompts, resolves approvals, cancels
runs, and renders replay plus live events over WebSocket.

## Run

```bash
cargo run --manifest-path tui-rs/Cargo.toml -- \
  --backend-url http://127.0.0.1:8420
```

Select a particular session at startup:

```bash
cargo run --manifest-path tui-rs/Cargo.toml -- --session ses_1234
```

`MYHARNESS_BACKEND_URL` is used when `--backend-url` is omitted.

### Windows

`run.cmd --tui` launches this crate through Cargo (and falls back to the
legacy Python Textual TUI when Cargo is missing):

```cmd
run.cmd --tui --backend-url http://127.0.0.1:8420
```

Full Windows installation steps — including the Rust toolchain via rustup and
the required Visual Studio C++ Build Tools — are in the repository's root
`README.md` under "Running on Windows". Use Windows Terminal for `Shift+Enter`
newline support; classic `cmd.exe` windows need `Alt+Enter` instead.

## Keys

- `Tab`: switch between the left navigator and right conversation pane
- `j` / `Down`, `k` / `Up`: move through sessions while the navigator is focused
- `Enter`: open the highlighted session
- `p`: create a project from an allowed workspace root
- `t`: create a task in the selected project
- `n`: create and open a session in the selected task
- Type directly in the right-pane composer; `Enter` sends
- `Shift+Enter`: insert a newline in the composer; pasted text keeps its
  newlines. The TUI enables the kitty keyboard protocol when the terminal
  supports it so Shift+Enter is distinguishable from Enter; in terminals
  without that support (e.g. macOS Terminal.app), use `Alt+Enter` instead
- Pasting an image: on `Ctrl+V`/`Cmd+V` the TUI checks the OS clipboard for
  image data (macOS and Windows natively, Linux under X11) before falling
  back to pasted text, and attaches it to the next sent message the same way
  the web/Electron composer does. Attaching more than 4 images or one over
  10 MB is rejected with an error, matching the backend's limits. Backspace
  on an empty draft line removes the most recently attached image. Clipboard
  image paste is not supported on Linux Wayland sessions (only text paste
  works there)
- `Left` / `Right`: move the composer cursor; `Backspace` / `Delete` edit around it
- `Up` / `Down`: move the cursor between draft lines in a multi-line draft;
  at the draft's edge (or in a single-line draft) they scroll the transcript
- `Ctrl+P` / `Ctrl+N`: recall older/newer previously sent messages; editing or
  sending resets the recall position, and the in-progress draft is restored
  when cycling past the newest entry
- `Ctrl+U`: clear the composer draft
- `Ctrl+C`: cancel the open session's active run from any pane
- `Ctrl+O`: load the previous page of older stored events into the transcript
- `u`: open the queued-messages panel for the open session (navigator focus);
  inside it `j`/`k` select, `K`/`J` move the item up/down, `d` deletes, and
  `Esc` closes
- `i`: optional shortcut to focus the conversation pane
- `c`: cancel the open session's active run (navigator focus)
- `d`: delete the selected project, task, or session after confirmation
- `PageUp` / `PageDown`, `Home` / `End`: scroll the transcript
- `r`: reconnect the selected session and refresh backend health and the tree
- `q`: quit from navigator focus
- `Esc`: return from conversation to navigator, or cancel an active dialog

A dropped session stream reconnects automatically with 1s/2s/5s backoff until
the connection succeeds; `r` still forces an immediate reconnect. The
project/task/session tree also refreshes every 30 seconds so sessions created
in the web UI appear without restarting. Unsent drafts are kept per session
when switching between sessions.

Opening a session focuses the conversation pane automatically. `Esc` returns to
the navigator, and `Tab` switches between panes. Creation dialogs use `Tab` to
move between fields, `Enter` to advance or submit, and `Esc` to cancel. Opening
the session dialog re-fetches backend health so provider availability stays
current. Messages
sent during an active run are queued. Approval
prompts use `y` to approve, `n` to deny, or `c` to cancel the run. Deletion removes
MyHarness metadata and session data; it never deletes project workspace files.

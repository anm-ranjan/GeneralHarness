from __future__ import annotations

import json
import os
import sys
import threading

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import Hit, Hits, Provider
from textual.containers import VerticalScroll, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Header, Footer, Static, TextArea, Button, LoadingIndicator
from textual.widgets import Markdown as TMarkdown
from textual.message import Message
from textual import on

import harness_agent as agent
import utils

_WIN = sys.platform == "win32"
_BULLET = "*" if _WIN else "●"

if _WIN:
    from textual.keys import KEY_DISPLAY_ALIASES
    KEY_DISPLAY_ALIASES.update({
        "up": "Up", "down": "Down", "left": "Left", "right": "Right",
        "backspace": "BkSp", "enter": "Enter",
    })
    from textual._border import BORDER_CHARS
    _SOLID = (
        ("+", "-", "+"),
        ("|", " ", "|"),
        ("+", "-", "+"),
    )
    for _name in ("round", "solid", "double", "dashed", "heavy", "inner",
                   "outer", "thick", "block", "hkey", "vkey", "tall",
                   "panel", "tab", "wide"):
        BORDER_CHARS[_name] = _SOLID
    from textual.scrollbar import ScrollBar
    ScrollBar.VERTICAL_BARS = ["#", "#", "#", "#", "#", "#", "#", " "]
    ScrollBar.HORIZONTAL_BARS = ["#", "#", "#", "#", "#", "#", "#", " "]
    from textual.command import SearchIcon
    SearchIcon.icon = "*"


def _get_system_clipboard() -> str:
    import subprocess
    try:
        if _WIN:
            r = subprocess.run(
                ["powershell", "-Command", "Get-Clipboard"],
                capture_output=True, text=True, timeout=2,
            )
            return r.stdout.rstrip("\r\n")
        else:
            r = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2)
            return r.stdout
    except Exception:
        return ""


def _set_system_clipboard(text: str) -> None:
    import subprocess
    try:
        if _WIN:
            subprocess.run(
                ["powershell", "-Command", "Set-Clipboard", "-Value", text],
                capture_output=True, timeout=2,
            )
        else:
            subprocess.run(["pbcopy"], input=text.encode(), timeout=2)
    except Exception:
        pass


_TUI_STATE_FILE = os.path.join(
    os.environ.get("MYHARNESS_WEB_DATA_DIR",
                    os.path.join(os.path.dirname(__file__), "..", "..", "data")),
    "tui_state.json",
)


def _load_tui_state() -> dict:
    try:
        with open(_TUI_STATE_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_tui_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_TUI_STATE_FILE), exist_ok=True)
        with open(_TUI_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass


def _resolve_initial_workspace_root() -> str:
    original_cwd = os.environ.get("MYHARNESS_ORIGINAL_CWD", "")
    if original_cwd and utils.is_path_allowed(original_cwd) and os.path.isdir(original_cwd):
        return os.path.normpath(os.path.abspath(original_cwd))

    cached = _load_tui_state().get("workspace_root", "")
    if cached and utils.is_path_allowed(cached) and os.path.isdir(cached):
        return os.path.normpath(os.path.abspath(cached))

    return utils.ALLOWED_PATHS[0] if utils.ALLOWED_PATHS else os.getcwd()


class AgentEvent(Message):
    """Posted from the worker thread to the main Textual thread."""

    def __init__(self, event_type: str, data: dict) -> None:
        self.event_type = event_type
        self.data = data
        super().__init__()


class ApprovalRequest(Message):
    """Posted when the worker thread needs a blocking approval decision."""

    def __init__(
        self,
        tool_name: str,
        args_json: str,
        diff_preview: str | None,
        response_event: threading.Event,
        result: list,
    ) -> None:
        self.tool_name = tool_name
        self.args_json = args_json
        self.diff_preview = diff_preview
        self.response_event = response_event
        self.result = result
        super().__init__()


class TuiUI:
    """AgentUI implementation that posts messages to a Textual app."""

    def __init__(self, app: IpaAgentApp) -> None:
        self._app = app

    def show_user_message(self, text: str, images: list[dict] | None = None) -> None:
        self._app.post_message(AgentEvent("user_message", {"text": text}))

    def show_assistant_markdown(self, text: str) -> None:
        self._app.post_message(AgentEvent("assistant_markdown", {"text": text}))

    def show_thinking(self, text: str) -> None:
        self._app.post_message(AgentEvent("status", {"text": f"[dim]Thinking: {text[:200]}{'...' if len(text) > 200 else ''}[/dim]"}))

    def show_tool_call(self, name: str, args: dict, status_line: str, verbose: bool) -> None:
        self._app.post_message(AgentEvent("tool_call", {
            "name": name, "args": args, "status_line": status_line, "verbose": verbose,
        }))

    def show_tool_result(self, name: str, result_preview: str, verbose: bool) -> None:
        self._app.post_message(AgentEvent("tool_result", {
            "name": name, "preview": result_preview, "verbose": verbose,
        }))

    def show_status(self, text: str, style: str = "") -> None:
        self._app.post_message(AgentEvent("status", {"text": text}))

    def show_error(self, text: str) -> None:
        self._app.post_message(AgentEvent("error", {"text": text}))

    def show_iteration(self, n: int) -> None:
        self._app.post_message(AgentEvent("iteration", {"n": n}))

    def show_compaction(self, before_tokens: int, after_tokens: int) -> None:
        self._app.post_message(AgentEvent("compaction", {
            "before": before_tokens, "after": after_tokens,
        }))

    def show_context_usage(self, usage_str: str) -> None:
        self._app.post_message(AgentEvent("context_usage", {"usage_str": usage_str}))

    def show_api_metrics(self, metrics: dict) -> None:
        self._app.post_message(AgentEvent("api_metrics", metrics))

    def show_agent_finished(self, reason: str) -> None:
        self._app.post_message(AgentEvent("agent_finished", {"reason": reason}))

    def request_approval(
        self,
        tool_name: str,
        args_json: str,
        diff_preview: str | None,
    ) -> bool:
        event = threading.Event()
        result = [False]
        self._app.post_message(
            ApprovalRequest(tool_name, args_json, diff_preview, event, result)
        )
        event.wait()
        return result[0]

    def prompt_user_input(self, prompt_text: str) -> str:
        raise NotImplementedError("TUI input is event-driven")


class SubmittableTextArea(TextArea):
    """TextArea that posts Submitted on Enter, Ctrl+J for newline."""

    BINDING_GROUP_TITLE = "Input"
    BINDINGS = [
        Binding("enter", "noop_submit", "Submit message", show=True),
        Binding("ctrl+j", "noop_newline", "Insert newline", show=True),
        Binding("ctrl+c", "noop_interrupt", "Interrupt / Quit", show=True),
        Binding("ctrl+v", "paste", "Paste clipboard", show=True),
        Binding("ctrl+o", "copy_last", "Copy last response", show=True),
        Binding("up", "noop_history_prev", "Previous message", show=True),
        Binding("down", "noop_history_next", "Next message", show=True),
    ]

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._history: list[str] = []
        self._history_index: int = -1
        self._draft: str = ""

    def action_paste(self) -> None:
        if self.read_only:
            return
        text = _get_system_clipboard()
        if not text:
            text = self.app.clipboard
        if text:
            self.insert(text)

    def action_copy_last(self) -> None:
        self.app.action_copy_last()

    def action_noop_submit(self) -> None: pass
    def action_noop_newline(self) -> None: pass
    def action_noop_interrupt(self) -> None: pass
    def action_noop_history_prev(self) -> None: pass
    def action_noop_history_next(self) -> None: pass

    def _navigate_history(self, direction: int) -> None:
        if not self._history:
            return
        if self._history_index == -1:
            self._draft = self.text
        new_index = self._history_index + direction
        if new_index < -1 or new_index >= len(self._history):
            return
        self._history_index = new_index
        if new_index == -1:
            self.clear()
            self.insert(self._draft)
        else:
            self.clear()
            self.insert(self._history[new_index])

    async def _on_key(self, event) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            text = self.text.strip()
            if text:
                self._history.insert(0, text)
                self._history_index = -1
                self._draft = ""
                self.post_message(self.Submitted(text))
                self.clear()
            return
        if event.key in ("ctrl+j", "shift+enter"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if event.key == "up" and "\n" not in self.text:
            event.stop()
            event.prevent_default()
            self._navigate_history(1)
            return
        if event.key == "down" and "\n" not in self.text:
            event.stop()
            event.prevent_default()
            self._navigate_history(-1)
            return
        await super()._on_key(event)


class ApprovalScreen(ModalScreen[bool]):
    """Modal dialog for tool approval."""

    DEFAULT_CSS = """
    ApprovalScreen {
        align: center middle;
    }
    ApprovalScreen > #approval-container {
        width: 80%;
        max-width: 100;
        max-height: 80%;
        background: $surface;
        border: thick $warning;
        padding: 1 2;
    }
    ApprovalScreen #approval-title {
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
    }
    ApprovalScreen #approval-args {
        height: auto;
        max-height: 12;
        overflow-y: auto;
        margin-bottom: 1;
        border: solid $primary-background;
        padding: 0 1;
    }
    ApprovalScreen #approval-diff {
        height: auto;
        max-height: 16;
        overflow-y: auto;
        margin-bottom: 1;
        border: solid $primary-background;
        padding: 0 1;
    }
    ApprovalScreen .approval-buttons {
        height: 3;
        align: center middle;
    }
    ApprovalScreen .approval-buttons Button {
        margin: 0 2;
    }
    """

    def __init__(self, tool_name: str, args_json: str, diff_preview: str | None) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.args_json = args_json
        self.diff_preview = diff_preview

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="approval-container"):
            yield Static(f"Approval Required: {self.tool_name}", id="approval-title")
            yield Static(self.args_json[:3000], id="approval-args")
            if self.diff_preview:
                yield Static("Proposed Diff:", classes="diff-label")
                yield Static(self.diff_preview[:6000], id="approval-diff")
            with Horizontal(classes="approval-buttons"):
                yield Button("Approve", variant="success", id="approve")
                yield Button("Deny", variant="error", id="deny")

    @on(Button.Pressed, "#approve")
    def approve(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#deny")
    def deny(self) -> None:
        self.dismiss(False)


_HELP_ITEMS = [
    ("Help: Ctrl+J insert newline", "Add a new line in the input area"),
    ("Help: Ctrl+C interrupt or quit", "Stop a running request, or quit if idle"),
    ("Help: Ctrl+Q quit app", "Exit the application"),
    ("Help: Ctrl+L clear context", "Reset conversation history"),
    ("Help: Ctrl+V paste clipboard", "Paste system clipboard contents"),
    ("Help: Ctrl+O copy last response", "Copy last assistant response to clipboard"),
    ("Help: Up/Down arrows", "Navigate input history (previous/next messages)"),
    ("Help: Ctrl+P command palette", "Open this palette"),
    ("Help: /approve <mode>", "Change approval mode: always_ask, shell_only, auto_approve"),
    ("Help: /verbose", "Toggle verbose tool output on/off"),
    ("Help: /cd <path>", "Change workspace root directory"),
]


class HelpProvider(Provider):
    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, help_text in _HELP_ITEMS:
            if (score := matcher.match(name)) > 0:
                yield Hit(score, matcher.highlight(name), lambda: None, help=help_text)


class InfoPanel(Static):
    """Right-side panel showing model, directory, context usage, and API metrics."""

    def __init__(self) -> None:
        super().__init__("", id="info-panel")
        self._context_str = ""
        self._metrics: dict = {}

    def update_context(self, usage_str: str) -> None:
        self._context_str = usage_str
        self._rebuild()

    def update_metrics(self, metrics: dict) -> None:
        self._metrics = metrics
        self._rebuild()

    def _rebuild(self) -> None:
        model_short = utils.MODEL.rsplit("/", 1)[-1] if "/" in utils.MODEL else utils.MODEL
        cwd = getattr(self.app, '_workspace_root', None) or os.getcwd()
        max_w = 24
        if len(cwd) > max_w:
            parts = cwd.replace("\\", "/").split("/")
            short = parts[-1]
            for p in reversed(parts[:-1]):
                candidate = p + "/" + short
                if len(candidate) > max_w:
                    short = ".../" + short
                    break
                short = candidate
            cwd_display = short
        else:
            cwd_display = cwd
        lines = [
            "[bold]Model[/bold]",
            f"  {model_short}",
            "",
            "[bold]Approval[/bold]",
            f"  {utils.APPROVAL_MODE}",
            "",
            "[bold]Directory[/bold]",
            f"  {cwd_display}",
        ]
        if self._context_str:
            lines += ["", "[bold]Context[/bold]", f"  {self._context_str}"]
        if self._metrics:
            m = self._metrics
            lines += [
                "",
                "[bold]Last Response[/bold]",
                f"  {m.get('elapsed', 0)}s",
            ]
            tps = m.get("tps", 0)
            if tps > 0:
                lines.append(f"  {tps} tok/s")
            ct = m.get("completion_tokens", 0)
            pt = m.get("prompt_tokens", 0)
            if ct or pt:
                lines.append(f"  {pt} in / {ct} out")
        self.update("\n".join(lines))

    def on_mount(self) -> None:
        self._rebuild()


class IpaAgentApp(App):
    """Textual TUI for the MyHarness coding agent."""

    TITLE = "MyHarness Agent"
    COMMANDS = App.COMMANDS | {HelpProvider}
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+l", "clear", "Clear"),
    ]

    DEFAULT_CSS = """
    #main-area {
        height: 1fr;
        layout: horizontal;
    }
    #chat-panel {
        width: 1fr;
        height: 1fr;
        overflow-y: auto;
        padding: 0 1;
    }
    #info-panel {
        width: 28;
        height: 1fr;
        dock: right;
        border-left: solid $primary-background;
        padding: 1;
        color: $text-muted;
    }
    .user-msg {
        background: $primary-background;
        color: $text;
        margin: 1 0 0 0;
        padding: 0 1;
    }
    .assistant-msg {
        margin: 1 0 0 0;
        padding: 0 1;
    }
    .tool-msg {
        color: $text-muted;
        margin: 0;
        padding: 0 1;
    }
    .tool-result-msg {
        color: $success;
        margin: 0;
        padding: 0 1;
    }
    .error-msg {
        color: $error;
        margin: 0;
        padding: 0 1;
    }
    .status-msg {
        color: $warning;
        margin: 0;
        padding: 0 1;
    }
    #splash {
        width: 100%;
        height: 1fr;
        content-align: center middle;
        text-align: center;
        color: $text;
        padding: 2;
    }
    #splash .logo-art {
        text-style: bold;
        color: $accent;
    }
    #input-area {
        height: auto;
        max-height: 8;
        min-height: 3;
        dock: bottom;
        margin: 0 1;
    }
    #loading {
        height: 1;
        dock: bottom;
        display: none;
    }
    #loading.visible {
        display: block;
    }
    """

    def _build_splash(self) -> Static:
        model_short = utils.MODEL.rsplit("/", 1)[-1] if "/" in utils.MODEL else utils.MODEL
        if _WIN:
            art = (
                "  ___ ___ _  _ __  __\n"
                " |_ _/ __| || |  \\/  |\n"
                "  | |\\__ \\ __ | |\\/| |\n"
                " |___|___/_||_|_|  |_|\n"
            )
        else:
            art = (
                "  ██╗███████╗██╗  ██╗███╗   ███╗\n"
                "  ██║██╔════╝██║  ██║████╗ ████║\n"
                "  ██║███████╗███████║██╔████╔██║\n"
                "  ██║╚════██║██╔══██║██║╚██╔╝██║\n"
                "  ██║███████║██║  ██║██║ ╚═╝ ██║\n"
                "  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝\n"
            )
        logo = (
            "\n"
            f"[bold cyan]{art}[/bold cyan]"
            "\n"
            "[bold]CLI Assistant[/bold]\n"
            "\n"
            f"[dim]Model: {model_short}  |  Approval: {utils.APPROVAL_MODE}[/dim]\n"
            "[dim]Enter a message to begin  |  Ctrl+J newline  |  Ctrl+C stop/quit[/dim]\n"
        )
        return Static(logo, id="splash")

    def compose(self) -> ComposeResult:
        yield Header(icon="*") if _WIN else Header()
        with Horizontal(id="main-area"):
            yield VerticalScroll(id="chat-panel")
            yield InfoPanel()
        yield LoadingIndicator(id="loading")
        yield SubmittableTextArea(id="input-area")
        yield Footer()

    def on_mount(self) -> None:
        self._workspace_root = _resolve_initial_workspace_root()
        self.messages_history = agent.build_initial_messages(workspace_root=self._workspace_root)
        _save_tui_state({"workspace_root": self._workspace_root})
        self.sub_title = ""
        self._agent_running = False
        self._cancel_event = threading.Event()
        self._splash_visible = True
        self._last_assistant_text = ""
        panel = self.query_one("#chat-panel", VerticalScroll)
        panel.mount(self._build_splash())
        self.query_one("#input-area", SubmittableTextArea).focus()
        self.start_health_check()

    def _ping_api(self) -> bool:
        import requests
        try:
            payload = {
                "model": utils.MODEL,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            }
            provider_preferences = utils.build_openrouter_provider_preferences()
            if provider_preferences:
                payload["provider"] = provider_preferences
            r = requests.post(
                f"{utils.BASE_URL}/chat/completions",
                headers=utils.HEADERS,
                json=payload,
                timeout=5,
            )
            return r.status_code == 200
        except Exception:
            return False

    def _check_api_health(self) -> None:
        import time
        if self._ping_api():
            return
        self.post_message(AgentEvent("error", {"text": "API unreachable — retrying every 30s..."}))
        while True:
            time.sleep(30)
            if self._ping_api():
                self.post_message(AgentEvent("status", {"text": "[green]API connection restored[/green]"}))
                return
            self.post_message(AgentEvent("error", {"text": "API still unreachable — retrying in 30s..."}))

    def start_health_check(self) -> None:
        self.run_worker(self._check_api_health, thread=True, exclusive=True, group="health")

    def _remove_splash(self) -> None:
        if self._splash_visible:
            self._splash_visible = False
            try:
                self.query_one("#splash").remove()
            except Exception:
                pass

    @on(SubmittableTextArea.Submitted)
    def on_input_submitted(self, event: SubmittableTextArea.Submitted) -> None:
        user_text = event.value
        if not user_text:
            return
        if self._agent_running:
            return

        self._remove_splash()

        if user_text.lower() in ("exit", "quit", "q"):
            self.exit()
            return
        if user_text.strip().lower() == "/clear":
            self.action_clear()
            return
        if user_text.strip().lower().startswith("/approve"):
            self._handle_approve_command(user_text.strip())
            return
        if user_text.strip().lower() == "/verbose":
            self._toggle_verbose()
            return
        if user_text.strip().lower().startswith("/cd"):
            self._handle_cd_command(user_text.strip())
            return

        self._agent_running = True
        self._cancel_event.clear()
        self.query_one("#loading", LoadingIndicator).add_class("visible")
        self._update_status("Working...")
        self.run_worker(
            self._run_agent_worker(user_text),
            thread=True,
        )

    def _run_agent_worker(self, user_text: str):
        def work():
            tui_ui = TuiUI(self)
            try:
                self.messages_history = agent.run_agent(
                    user_text, self.messages_history, ui=tui_ui,
                    cancel_event=self._cancel_event,
                )
                new_wr = self._detect_workspace_change()
                if new_wr:
                    self._workspace_root = new_wr
                    self.messages_history[0] = agent.build_system_message(self._workspace_root)
                    _save_tui_state({"workspace_root": self._workspace_root})
                self.messages_history = agent.compact_agent_history_if_needed(
                    self.messages_history, ui=tui_ui,
                    workspace_root=self._workspace_root,
                )
                tui_ui.show_context_usage(
                    agent.format_context_usage(self.messages_history)
                )
            except Exception as e:
                tui_ui.show_error(f"Worker error: {e}")
            self.post_message(AgentEvent("done", {}))
        return work

    def _detect_workspace_change(self) -> str | None:
        last_wd = None
        for msg in reversed(self.messages_history):
            if msg.get("role") == "user":
                break
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if tc["function"]["name"] == "shell_run":
                        try:
                            args = json.loads(tc["function"]["arguments"])
                            wd = args.get("working_directory", "")
                            if wd:
                                last_wd = wd
                        except (json.JSONDecodeError, TypeError):
                            pass
        if not last_wd or not utils.is_path_allowed(last_wd):
            return None
        resolved_new = os.path.normpath(os.path.abspath(last_wd))
        resolved_cur = os.path.normpath(os.path.abspath(self._workspace_root))
        if resolved_new == resolved_cur:
            return None
        try:
            if os.path.commonpath([resolved_new, resolved_cur]) == resolved_cur:
                return None
        except ValueError:
            pass
        return resolved_new

    def on_agent_event(self, message: AgentEvent) -> None:
        panel = self.query_one("#chat-panel", VerticalScroll)
        t = message.event_type
        d = message.data

        if t == "user_message":
            panel.mount(Static(f"[bold blue]You:[/bold blue] {d['text']}", classes="user-msg"))
        elif t == "assistant_markdown":
            self._last_assistant_text = d["text"]
            panel.mount(TMarkdown(d["text"], classes="assistant-msg"))
        elif t == "tool_call":
            if d.get("verbose"):
                args_str = json.dumps(d["args"], indent=2, ensure_ascii=False)
                panel.mount(Static(
                    f"[bold magenta]Tool:[/bold magenta] {d['name']}\n{args_str[:500]}",
                    classes="tool-msg",
                ))
            else:
                panel.mount(Static(
                    f"  [dim]{_BULLET}[/dim] {d['status_line']}", classes="tool-msg",
                ))
        elif t == "tool_result":
            if d.get("verbose"):
                panel.mount(Static(
                    f"[green]Result:[/green] {d['preview'][:300]}",
                    classes="tool-result-msg",
                ))
        elif t == "error":
            panel.mount(Static(f"[bold red]Error:[/bold red] {d['text']}", classes="error-msg"))
            err = d["text"].lower()
            if any(k in err for k in ("api", "timeout", "connection", "unreachable")):
                self.start_health_check()
        elif t == "iteration":
            self._update_status(f"Iteration {d['n']}...")
        elif t == "compaction":
            if d["after"] == 0:
                panel.mount(Static(
                    f"[yellow]Compacting ({d['before']} tokens)...[/yellow]",
                    classes="status-msg",
                ))
            else:
                panel.mount(Static(
                    f"[green]Compacted to {d['after']} tokens[/green]",
                    classes="status-msg",
                ))
        elif t == "context_usage":
            self.query_one(InfoPanel).update_context(d["usage_str"])
        elif t == "api_metrics":
            self.query_one(InfoPanel).update_metrics(d)
        elif t == "agent_finished":
            panel.mount(Static(
                f"[dim]Agent finished ({d['reason']})[/dim]", classes="status-msg",
            ))
        elif t == "status":
            panel.mount(Static(d["text"], classes="status-msg"))
        elif t == "done":
            self._agent_running = False
            self.query_one("#loading", LoadingIndicator).remove_class("visible")
            self.query_one("#input-area", SubmittableTextArea).focus()

        panel.scroll_end(animate=False)

    def on_approval_request(self, message: ApprovalRequest) -> None:
        self.push_screen(
            ApprovalScreen(message.tool_name, message.args_json, message.diff_preview),
            callback=lambda approved: self._resolve_approval(approved, message),
        )

    def _resolve_approval(self, approved: bool, message: ApprovalRequest) -> None:
        message.result[0] = approved
        message.response_event.set()

    def action_help_quit(self) -> None:
        if self._agent_running:
            self._cancel_event.set()
            self.workers.cancel_all()
            self._agent_running = False
            self.query_one("#loading", LoadingIndicator).remove_class("visible")
            panel = self.query_one("#chat-panel", VerticalScroll)
            panel.mount(Static("[bold red]Aborted[/bold red]", classes="error-msg"))
            panel.scroll_end(animate=False)
            self._update_status("Interrupted")
            self.query_one("#input-area", SubmittableTextArea).focus()
        else:
            self.exit()

    _APPROVE_MODES = ("always_ask", "shell_only", "auto_approve")

    def _handle_approve_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        panel = self.query_one("#chat-panel", VerticalScroll)
        self._remove_splash()
        if len(parts) == 1:
            panel.mount(Static(
                f"[bold]Approval mode:[/bold] {utils.APPROVAL_MODE}\n"
                f"[dim]Usage: /approve <{'|'.join(self._APPROVE_MODES)}>[/dim]",
                classes="status-msg",
            ))
            panel.scroll_end(animate=False)
            return
        mode = parts[1].strip().lower()
        if mode not in self._APPROVE_MODES:
            panel.mount(Static(
                f"[red]Unknown mode '{mode}'. Choose from: {', '.join(self._APPROVE_MODES)}[/red]",
                classes="error-msg",
            ))
            panel.scroll_end(animate=False)
            return
        utils.APPROVAL_MODE = mode
        self.query_one(InfoPanel)._rebuild()
        panel.mount(Static(
            f"[green]Approval mode set to: {mode}[/green]",
            classes="status-msg",
        ))
        panel.scroll_end(animate=False)

    def _toggle_verbose(self) -> None:
        utils.UI_VERBOSE_TOOLS = not utils.UI_VERBOSE_TOOLS
        state = "on" if utils.UI_VERBOSE_TOOLS else "off"
        panel = self.query_one("#chat-panel", VerticalScroll)
        self._remove_splash()
        panel.mount(Static(
            f"[green]Verbose mode: {state}[/green]",
            classes="status-msg",
        ))
        panel.scroll_end(animate=False)

    def _handle_cd_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        panel = self.query_one("#chat-panel", VerticalScroll)
        self._remove_splash()
        if len(parts) == 1:
            panel.mount(Static(
                f"[bold]Workspace root:[/bold] {self._workspace_root}\n"
                f"[dim]Usage: /cd <path>[/dim]",
                classes="status-msg",
            ))
            panel.scroll_end(animate=False)
            return
        new_path = os.path.expanduser(parts[1].strip())
        if not os.path.isabs(new_path):
            new_path = os.path.join(self._workspace_root, new_path)
        new_path = os.path.normpath(os.path.abspath(new_path))
        if not utils.is_path_allowed(new_path):
            panel.mount(Static(
                f"[red]Path not allowed: {new_path}[/red]",
                classes="error-msg",
            ))
            panel.scroll_end(animate=False)
            return
        if not os.path.isdir(new_path):
            panel.mount(Static(
                f"[red]Not a directory: {new_path}[/red]",
                classes="error-msg",
            ))
            panel.scroll_end(animate=False)
            return
        self._workspace_root = new_path
        self.messages_history[0] = agent.build_system_message(self._workspace_root)
        _save_tui_state({"workspace_root": self._workspace_root})
        self.query_one(InfoPanel)._rebuild()
        panel.mount(Static(
            f"[green]Workspace root set to: {new_path}[/green]",
            classes="status-msg",
        ))
        panel.scroll_end(animate=False)

    def action_copy_last(self) -> None:
        if self._last_assistant_text:
            _set_system_clipboard(self._last_assistant_text)
            self._update_status("Copied last response to clipboard")
        else:
            self._update_status("Nothing to copy")

    def action_clear(self) -> None:
        self.messages_history = agent.build_initial_messages(workspace_root=self._workspace_root)
        panel = self.query_one("#chat-panel", VerticalScroll)
        panel.remove_children()
        self._update_status("Context cleared")

    def _update_status(self, text: str) -> None:
        self.sub_title = text

mod api;
mod app;
mod args;
mod clipboard;
mod events;
mod ui;
mod ws;

use std::{io, thread, time::Duration};

use anyhow::{Context, Result};
use app::{
    App, DeleteForm, DeleteTarget, FormFocus, FormState, Modal, NavKey, NavRow, PaneFocus,
    ProjectForm, RunState, SessionForm, TaskForm,
};
use args::Args;
use clap::Parser;
use crossterm::{
    event::{
        self, DisableBracketedPaste, EnableBracketedPaste, Event, KeyCode, KeyEventKind,
        KeyModifiers,
    },
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{backend::CrosstermBackend, Terminal};
use tokio::{sync::mpsc, task::JoinHandle};
use ws::StreamUpdate;

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    let client = api::MyHarnessClient::new(&args.backend_url)?;
    let (health, tree) = tokio::try_join!(client.health(), client.sessions())
        .with_context(|| format!("could not connect to {}", client.display_url()))?;
    if let Some(id) = args.session.as_deref() {
        if !tree.sessions.contains_key(id) || !tree.lists_session(id) {
            anyhow::bail!("session '{id}' was not returned by /api/sessions");
        }
    }

    let mut app = App::new(client.display_url(), health, tree, args.session.as_deref());
    let mut terminal = TerminalSession::enter()?;
    let (updates_tx, mut updates_rx) = mpsc::unbounded_channel();
    let (actions_tx, mut actions_rx) = mpsc::unbounded_channel();
    let mut input_rx = spawn_input_reader();
    let mut generation = 0_u64;
    let mut stream_task = connect_selected(&client, &mut app, &updates_tx, &mut generation)?;
    spawn_tree_poller(client.clone(), actions_tx.clone());

    let run_result = run_ui(
        terminal.terminal_mut(),
        &client,
        &mut app,
        &updates_tx,
        &mut updates_rx,
        &actions_tx,
        &mut actions_rx,
        &mut input_rx,
        &mut generation,
        &mut stream_task,
    )
    .await;
    if let Some(task) = stream_task {
        task.abort();
    }
    run_result
}

enum ActionUpdate {
    Created {
        request_id: u64,
        selected: NavKey,
        session_id: Option<String>,
        notice: String,
        tree: Option<api::SessionTree>,
        refresh_error: Option<String>,
    },
    Failed {
        request_id: u64,
        detail: String,
    },
    MessageSent {
        request_id: u64,
        session_id: String,
        response: api::MessageResponse,
    },
    ApprovalResolved {
        request_id: u64,
        approved: bool,
    },
    QuestionAnswered {
        request_id: u64,
        answer: String,
    },
    Cancelled {
        session_id: String,
    },
    CancelFailed {
        session_id: String,
        previous_state: RunState,
        detail: String,
    },
    Deleted {
        request_id: u64,
        label: String,
        removed_sessions: Vec<String>,
        tree: Option<api::SessionTree>,
        refresh_error: Option<String>,
    },
    HealthRefreshed {
        health: api::Health,
    },
    TreeRefreshed {
        tree: api::SessionTree,
    },
    ReconnectDue {
        session_id: String,
        scheduled_generation: u64,
    },
    OlderEvents {
        session_id: String,
        scheduled_generation: u64,
        page: std::result::Result<api::EventsPage, String>,
    },
    Notice {
        text: String,
    },
}

fn refresh_health(client: api::MyHarnessClient, actions: mpsc::UnboundedSender<ActionUpdate>) {
    tokio::spawn(async move {
        if let Ok(health) = client.health().await {
            let _ = actions.send(ActionUpdate::HealthRefreshed { health });
        }
    });
}

fn refresh_tree(client: api::MyHarnessClient, actions: mpsc::UnboundedSender<ActionUpdate>) {
    tokio::spawn(async move {
        if let Ok(tree) = client.sessions().await {
            let _ = actions.send(ActionUpdate::TreeRefreshed { tree });
        }
    });
}

const TREE_POLL_SECONDS: u64 = 30;

fn spawn_tree_poller(client: api::MyHarnessClient, actions: mpsc::UnboundedSender<ActionUpdate>) {
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_secs(TREE_POLL_SECONDS)).await;
            if actions.is_closed() {
                break;
            }
            if let Ok(tree) = client.sessions().await {
                if actions.send(ActionUpdate::TreeRefreshed { tree }).is_err() {
                    break;
                }
            }
        }
    });
}

async fn run_ui(
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
    client: &api::MyHarnessClient,
    app: &mut App,
    updates_tx: &mpsc::UnboundedSender<StreamUpdate>,
    updates_rx: &mut mpsc::UnboundedReceiver<StreamUpdate>,
    actions_tx: &mpsc::UnboundedSender<ActionUpdate>,
    actions_rx: &mut mpsc::UnboundedReceiver<ActionUpdate>,
    input_rx: &mut mpsc::UnboundedReceiver<InputUpdate>,
    generation: &mut u64,
    stream_task: &mut Option<JoinHandle<()>>,
) -> Result<()> {
    loop {
        while let Ok(update) = updates_rx.try_recv() {
            apply_update(app, update, *generation, actions_tx);
        }
        terminal.draw(|frame| ui::draw(frame, app))?;

        let input = tokio::select! {
            update = updates_rx.recv() => {
                if let Some(update) = update {
                    apply_update(app, update, *generation, actions_tx);
                }
                continue;
            }
            action = actions_rx.recv() => {
                if let Some(action) = action {
                    apply_action(
                        app,
                        action,
                        client,
                        updates_tx,
                        generation,
                        stream_task,
                    )?;
                }
                continue;
            }
            input = input_rx.recv() => input,
        };
        let Some(input) = input else {
            anyhow::bail!("terminal input reader stopped");
        };
        let event = match input {
            InputUpdate::Event(event) => event,
            InputUpdate::Error(detail) => anyhow::bail!("terminal input failed: {detail}"),
        };
        if let Event::Paste(text) = event {
            if let Some(question) = app.pending_question.as_mut() {
                if question.allow_free_text && !question.answer.state.submitting {
                    let sanitized = text.replace(['\r', '\n'], " ");
                    question.answer.insert_str(&sanitized);
                }
                continue;
            }
            if app.commands_scroll.is_some() {
                continue;
            }
            if app.pending_approval.is_some() {
                continue;
            }
            if app.modal.is_some() && !modal_submitting(app) {
                append_paste(app, &text);
            } else if app.pane_focus == PaneFocus::Conversation && !app.composer.state.submitting {
                match clipboard::read_clipboard_image() {
                    Some(image) => attach_pasted_image(app, image),
                    None => append_composer_text(app, &text),
                }
            }
            continue;
        }
        let Event::Key(key) = event else { continue };
        if key.kind != KeyEventKind::Press {
            continue;
        }
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
            request_cancel(app, client.clone(), actions_tx.clone());
            continue;
        }
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('o') {
            request_older_events(app, client.clone(), actions_tx.clone(), *generation);
            continue;
        }
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('f') {
            app.open_search();
            continue;
        }
        if app.pending_question.is_some() {
            handle_question_key(app, key, client, actions_tx);
            continue;
        }
        if app.commands_scroll.is_some() {
            handle_commands_key(app, key);
            continue;
        }
        if app.pending_approval.is_some() {
            if matches!(key.code, KeyCode::Char('q')) {
                return Ok(());
            }
            handle_approval_key(app, key, client, actions_tx);
            continue;
        }
        if app.queue_overlay.is_some() {
            handle_queue_key(app, key, client, actions_tx);
            continue;
        }
        if app.modal.is_some() {
            handle_modal_key(app, key, client, actions_tx);
            continue;
        }
        if app.search.is_some() {
            handle_search_key(app, key);
            continue;
        }
        if key.code == KeyCode::Tab {
            toggle_pane_focus(app);
            continue;
        }
        if app.pane_focus == PaneFocus::Conversation {
            handle_conversation_key(app, key, client, actions_tx);
            continue;
        }
        app.notice = None;
        match key.code {
            KeyCode::Char('q') | KeyCode::Esc => return Ok(()),
            KeyCode::Char('j') | KeyCode::Down => app.move_cursor(1),
            KeyCode::Char('k') | KeyCode::Up => app.move_cursor(-1),
            KeyCode::PageUp => app.scroll_up(10),
            KeyCode::PageDown => app.scroll_down(10),
            KeyCode::Home => app.scroll_up(u16::MAX),
            KeyCode::End => app.scroll_from_bottom = 0,
            KeyCode::Enter => {
                let is_folder = matches!(
                    app.selected(),
                    Some(NavRow::Project { .. }) | Some(NavRow::Task { .. })
                );
                if is_folder {
                    app.toggle_collapsed();
                } else {
                    let selected_id = app.selected_session_id();
                    if selected_id.is_none() {
                        app.notice = Some("Select a session to open it.".to_owned());
                    } else if selected_id != app.active_session_id {
                        replace_stream(
                            stream_task,
                            connect_session(client, app, updates_tx, generation, selected_id)?,
                        );
                    } else {
                        app.pane_focus = PaneFocus::Conversation;
                    }
                }
            }
            KeyCode::Char('p') => open_project_form(app),
            KeyCode::Char('t') => open_task_form(app),
            KeyCode::Char('n') => {
                open_session_form(app);
                refresh_health(client.clone(), actions_tx.clone());
            }
            KeyCode::Char('i') => focus_conversation(app),
            KeyCode::Char('u') => open_queue_overlay(app),
            KeyCode::Char('c') => request_cancel(app, client.clone(), actions_tx.clone()),
            KeyCode::Char('d') => open_delete_form(app),
            KeyCode::Char('r') => {
                refresh_health(client.clone(), actions_tx.clone());
                refresh_tree(client.clone(), actions_tx.clone());
                let selected_id = app.selected_session_id();
                if selected_id.is_some() {
                    replace_stream(
                        stream_task,
                        connect_session(client, app, updates_tx, generation, selected_id)?,
                    );
                } else {
                    app.notice = Some("Select a session to reconnect it.".to_owned());
                }
            }
            _ => {}
        }
    }
}

fn open_project_form(app: &mut App) {
    app.modal = Some(Modal::Project(ProjectForm {
        name: String::new(),
        root: String::new(),
        focus: FormFocus::First,
        state: FormState::default(),
    }));
}

fn open_task_form(app: &mut App) {
    let Some((project_id, project_name)) = app.selected_project_target() else {
        app.notice = Some("Select a project, task, or session first.".to_owned());
        return;
    };
    if project_id == "__chats__" {
        app.notice = Some("The reserved Chats collection cannot contain project tasks.".to_owned());
        return;
    }
    app.modal = Some(Modal::Task(TaskForm {
        project_id,
        project_name,
        name: String::new(),
        focus: FormFocus::First,
        state: FormState::default(),
    }));
}

fn open_session_form(app: &mut App) {
    let target = app.selected_task_target().or_else(|| {
        // The Chats bucket has no per-task grouping, so allow starting a
        // general chat straight from the reserved Chats project row.
        match app.selected_project_target() {
            Some((id, name)) if id == "__chats__" => Some((id.clone(), id, name.clone(), name)),
            _ => None,
        }
    });
    let Some((project_id, task_id, project_name, task_name)) = target else {
        app.notice = Some("Select a task or session first.".to_owned());
        return;
    };
    app.modal = Some(Modal::Session(SessionForm {
        project_id,
        task_id,
        project_name,
        task_name,
        title: String::new(),
        providers: app.available_providers(),
        provider_index: 0,
        focus: FormFocus::First,
        state: FormState::default(),
    }));
}

fn focus_conversation(app: &mut App) {
    if app.active_session_id.is_none() {
        app.notice = Some("Open a session before composing a message.".to_owned());
        return;
    }
    app.pane_focus = PaneFocus::Conversation;
}

fn toggle_pane_focus(app: &mut App) {
    app.pane_focus = match app.pane_focus {
        PaneFocus::Navigator if app.active_session_id.is_some() => PaneFocus::Conversation,
        PaneFocus::Conversation | PaneFocus::Navigator => PaneFocus::Navigator,
    };
}

fn handle_conversation_key(
    app: &mut App,
    key: crossterm::event::KeyEvent,
    client: &api::MyHarnessClient,
    actions: &mpsc::UnboundedSender<ActionUpdate>,
) {
    match key.code {
        KeyCode::Up => {
            if app.composer.state.submitting || !app.composer.cursor_up() {
                app.scroll_up(1);
            }
        }
        KeyCode::Down => {
            if app.composer.state.submitting || !app.composer.cursor_down() {
                app.scroll_down(1);
            }
        }
        KeyCode::PageUp => app.scroll_up(10),
        KeyCode::PageDown => app.scroll_down(10),
        KeyCode::Home => app.scroll_up(u16::MAX),
        KeyCode::End => app.scroll_from_bottom = 0,
        _ => {}
    }
    if matches!(
        key.code,
        KeyCode::Up
            | KeyCode::Down
            | KeyCode::PageUp
            | KeyCode::PageDown
            | KeyCode::Home
            | KeyCode::End
    ) {
        return;
    }
    if app.composer.state.submitting {
        return;
    }
    match key.code {
        KeyCode::Esc => app.pane_focus = PaneFocus::Navigator,
        KeyCode::Char('p') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            app.recall_older_sent();
        }
        KeyCode::Char('n') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            app.recall_newer_sent();
        }
        KeyCode::Enter
            if key
                .modifiers
                .intersects(KeyModifiers::ALT | KeyModifiers::SHIFT) =>
        {
            app.composer.insert_char('\n');
        }
        KeyCode::Enter => submit_inline_composer(app, client.clone(), actions.clone()),
        KeyCode::Left => app.composer.move_left(),
        KeyCode::Right => app.composer.move_right(),
        KeyCode::Backspace if key.modifiers.contains(KeyModifiers::ALT) => {
            app.composer.delete_word_back();
        }
        KeyCode::Backspace if app.composer.text.is_empty() && app.composer.remove_last_image() => {
            app.notice = Some("Removed the last pasted image.".to_owned());
        }
        KeyCode::Backspace => app.composer.backspace(),
        KeyCode::Delete => app.composer.delete_forward(),
        KeyCode::Char('u') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            app.composer.clear_text();
        }
        KeyCode::Char('a') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            app.composer.move_line_start();
        }
        KeyCode::Char('e') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            app.composer.move_line_end();
        }
        KeyCode::Char('w') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            app.composer.delete_word_back();
        }
        KeyCode::Char('k') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            app.composer.delete_to_line_end();
        }
        KeyCode::Char(character) if is_text_input(key.modifiers) => {
            app.composer.insert_char(character);
        }
        _ => {}
    }
}

fn is_text_input(modifiers: KeyModifiers) -> bool {
    !modifiers.intersects(KeyModifiers::CONTROL | KeyModifiers::ALT)
}

fn open_queue_overlay(app: &mut App) {
    if app.active_session_id.is_none() {
        app.notice = Some("Open a session to inspect its queue.".to_owned());
    } else if app.queue_items.is_empty() {
        app.notice = Some("The open session's queue is empty.".to_owned());
    } else {
        app.queue_overlay = Some(0);
    }
}

fn handle_queue_key(
    app: &mut App,
    key: crossterm::event::KeyEvent,
    client: &api::MyHarnessClient,
    actions: &mpsc::UnboundedSender<ActionUpdate>,
) {
    let Some(selected) = app.queue_overlay else {
        return;
    };
    let last = app.queue_items.len().saturating_sub(1);
    match key.code {
        KeyCode::Esc | KeyCode::Char('q') => app.queue_overlay = None,
        KeyCode::Up | KeyCode::Char('k') => {
            app.queue_overlay = Some(selected.saturating_sub(1));
        }
        KeyCode::Down | KeyCode::Char('j') => {
            app.queue_overlay = Some((selected + 1).min(last));
        }
        KeyCode::Char('d') | KeyCode::Delete => {
            let Some(session_id) = app.active_session_id.clone() else {
                return;
            };
            let Some(item) = app.queue_items.get(selected) else {
                return;
            };
            let message_id = item.id.clone();
            let client = client.clone();
            let actions = actions.clone();
            tokio::spawn(async move {
                let text = match client.delete_queued_message(&session_id, &message_id).await {
                    Ok(_) => "Removed the queued message.".to_owned(),
                    Err(error) => format!("Queue delete failed: {error}"),
                };
                let _ = actions.send(ActionUpdate::Notice { text });
            });
        }
        KeyCode::Char('J') => reorder_queue_item(app, selected, 1, client, actions),
        KeyCode::Char('K') => reorder_queue_item(app, selected, -1, client, actions),
        _ => {}
    }
}

fn reorder_queue_item(
    app: &mut App,
    selected: usize,
    delta: isize,
    client: &api::MyHarnessClient,
    actions: &mpsc::UnboundedSender<ActionUpdate>,
) {
    let Some(session_id) = app.active_session_id.clone() else {
        return;
    };
    let target = selected as isize + delta;
    if target < 0 || target as usize >= app.queue_items.len() {
        return;
    }
    let target = target as usize;
    let mut order: Vec<String> = app.queue_items.iter().map(|item| item.id.clone()).collect();
    order.swap(selected, target);
    // Move the selection with the item; the queue_updated event carries truth.
    app.queue_overlay = Some(target);
    let client = client.clone();
    let actions = actions.clone();
    tokio::spawn(async move {
        if let Err(error) = client.reorder_queue(&session_id, &order).await {
            let _ = actions.send(ActionUpdate::Notice {
                text: format!("Queue reorder failed: {error}"),
            });
        }
    });
}

fn request_older_events(
    app: &mut App,
    client: api::MyHarnessClient,
    actions: mpsc::UnboundedSender<ActionUpdate>,
    generation: u64,
) {
    let Some(session_id) = app.active_session_id.clone() else {
        app.notice = Some("Open a session to load older events.".to_owned());
        return;
    };
    if app.loading_older {
        return;
    }
    if app.event_offset == 0 {
        app.notice = Some("The transcript already shows the oldest stored events.".to_owned());
        return;
    }
    let offset = app.event_offset.saturating_sub(200);
    let limit = app.event_offset - offset;
    app.loading_older = true;
    app.notice = Some("Loading older events…".to_owned());
    tokio::spawn(async move {
        let page = client
            .session_events(&session_id, offset, limit)
            .await
            .map_err(|error| error.to_string());
        let _ = actions.send(ActionUpdate::OlderEvents {
            session_id,
            scheduled_generation: generation,
            page,
        });
    });
}

fn handle_search_key(app: &mut App, key: crossterm::event::KeyEvent) {
    match key.code {
        KeyCode::Esc => app.close_search(),
        KeyCode::Enter | KeyCode::Up => app.search_step(-1),
        KeyCode::Down => app.search_step(1),
        KeyCode::Backspace => app.search_backspace(),
        KeyCode::Char(character) if is_text_input(key.modifiers) => app.search_input(character),
        _ => {}
    }
}

fn handle_commands_key(app: &mut App, key: crossterm::event::KeyEvent) {
    let max_scroll = crossterm::terminal::size()
        .map(|(width, height)| ui::commands_max_scroll(width, height))
        .unwrap_or(30);
    handle_commands_key_with_max(app, key, max_scroll);
}

fn handle_commands_key_with_max(app: &mut App, key: crossterm::event::KeyEvent, max_scroll: u16) {
    let Some(scroll) = app.commands_scroll.as_mut() else {
        return;
    };
    match key.code {
        KeyCode::Esc | KeyCode::Enter | KeyCode::Char('q') => app.commands_scroll = None,
        KeyCode::Up | KeyCode::Char('k') => *scroll = scroll.saturating_sub(1),
        KeyCode::Down | KeyCode::Char('j') => *scroll = scroll.saturating_add(1).min(max_scroll),
        KeyCode::PageUp => *scroll = scroll.saturating_sub(10),
        KeyCode::PageDown => *scroll = scroll.saturating_add(10).min(max_scroll),
        KeyCode::Home => *scroll = 0,
        KeyCode::End => *scroll = max_scroll,
        _ => {}
    }
}

fn open_delete_form(app: &mut App) {
    match app.selected_delete_target() {
        Ok(target) => {
            app.modal = Some(Modal::Delete(DeleteForm {
                target,
                state: FormState::default(),
            }));
        }
        Err(detail) => app.notice = Some(detail),
    }
}

fn handle_modal_key(
    app: &mut App,
    key: crossterm::event::KeyEvent,
    client: &api::MyHarnessClient,
    actions: &mpsc::UnboundedSender<ActionUpdate>,
) {
    if modal_submitting(app) {
        return;
    }
    if matches!(app.modal, Some(Modal::Delete(_))) {
        match key.code {
            KeyCode::Esc | KeyCode::Char('n') => app.modal = None,
            KeyCode::Enter | KeyCode::Char('y') => {
                submit_delete(app, client.clone(), actions.clone())
            }
            _ => {}
        }
        return;
    }
    match key.code {
        KeyCode::Esc => {
            app.modal = None;
        }
        KeyCode::Tab => {
            move_form_focus(app, false);
        }
        KeyCode::BackTab => {
            move_form_focus(app, true);
        }
        KeyCode::Enter => {
            if modal_focus(app) == Some(FormFocus::Submit) {
                submit_modal(app, client.clone(), actions.clone());
            } else {
                move_form_focus(app, false);
            }
        }
        KeyCode::Up | KeyCode::Left | KeyCode::Char('k') if session_provider_focused(app) => {
            rotate_provider(app, -1);
        }
        KeyCode::Down | KeyCode::Right | KeyCode::Char('j') if session_provider_focused(app) => {
            rotate_provider(app, 1);
        }
        KeyCode::Backspace | KeyCode::Delete => {
            edit_focused_text(app, |value| {
                value.pop();
            });
        }
        KeyCode::Char(character) if is_text_input(key.modifiers) => {
            edit_focused_text(app, |value| value.push(character));
        }
        _ => {}
    }
}

fn modal_submitting(app: &App) -> bool {
    app.modal
        .as_ref()
        .is_some_and(|modal| modal.state().submitting)
}

fn modal_focus(app: &App) -> Option<FormFocus> {
    match app.modal.as_ref()? {
        Modal::Project(form) => Some(form.focus),
        Modal::Task(form) => Some(form.focus),
        Modal::Session(form) => Some(form.focus),
        Modal::Delete(_) => None,
    }
}

fn move_form_focus(app: &mut App, backwards: bool) {
    let move_focus = |focus: FormFocus| {
        if backwards {
            focus.previous()
        } else {
            focus.next()
        }
    };
    match app.modal.as_mut() {
        Some(Modal::Project(form)) => form.focus = move_focus(form.focus),
        Some(Modal::Task(form)) => {
            form.focus = match (form.focus, backwards) {
                (FormFocus::First, false) | (FormFocus::Second, false) => FormFocus::Submit,
                (FormFocus::Submit, false) => FormFocus::First,
                (FormFocus::Submit, true) | (FormFocus::Second, true) => FormFocus::First,
                (FormFocus::First, true) => FormFocus::Submit,
            };
        }
        Some(Modal::Session(form)) => form.focus = move_focus(form.focus),
        Some(Modal::Delete(_)) => {}
        None => {}
    }
}

fn session_provider_focused(app: &App) -> bool {
    matches!(
        app.modal.as_ref(),
        Some(Modal::Session(SessionForm {
            focus: FormFocus::Second,
            ..
        }))
    )
}

fn rotate_provider(app: &mut App, delta: isize) {
    let Some(Modal::Session(form)) = app.modal.as_mut() else {
        return;
    };
    if form.providers.is_empty() {
        return;
    }
    let len = form.providers.len() as isize;
    form.provider_index = (form.provider_index as isize + delta).rem_euclid(len) as usize;
}

fn edit_focused_text(app: &mut App, edit: impl FnOnce(&mut String)) {
    match app.modal.as_mut() {
        Some(Modal::Project(form)) if form.focus == FormFocus::First => edit(&mut form.name),
        Some(Modal::Project(form)) if form.focus == FormFocus::Second => edit(&mut form.root),
        Some(Modal::Task(form)) if form.focus == FormFocus::First => edit(&mut form.name),
        Some(Modal::Session(form)) if form.focus == FormFocus::First => edit(&mut form.title),
        _ => {}
    }
}

fn append_paste(app: &mut App, text: &str) {
    let sanitized = text.replace(['\r', '\n'], " ");
    edit_focused_text(app, |value| value.push_str(&sanitized));
}

fn append_composer_text(app: &mut App, text: &str) {
    let sanitized = text.replace("\r\n", "\n").replace('\r', "\n");
    app.composer.insert_str(&sanitized);
}

fn attach_pasted_image(app: &mut App, mut image: app::PendingImage) {
    let index = app.composer.images.len() + 1;
    image.name = format!("clipboard-{index}.png");
    match app.composer.add_image(image) {
        Ok(()) => {
            app.notice = Some(format!("Attached image {index} from the clipboard."));
        }
        Err(message) => app.composer.state.error = Some(message),
    }
}

fn submit_inline_composer(
    app: &mut App,
    client: api::MyHarnessClient,
    actions: mpsc::UnboundedSender<ActionUpdate>,
) {
    if is_commands_command(&app.composer.text) {
        app.composer.clear_text();
        app.commands_scroll = Some(0);
        return;
    }
    let Some(session_id) = app.composer.session_id.clone() else {
        app.composer.state.error = Some("Open a session before sending a message.".to_owned());
        return;
    };
    if app.composer.text.trim().is_empty() {
        app.composer.state.error = Some("Message cannot be empty.".to_owned());
        return;
    }
    if app.run_state != RunState::Idle && app.composer.text.trim_start().starts_with('/') {
        app.composer.state.error =
            Some("Slash commands can only run while the session is idle.".to_owned());
        return;
    }
    let text = app.composer.text.clone();
    let images = app.composer.images.clone();
    let request_id = app.allocate_request_id();
    app.composer.state.submitting = true;
    app.composer.state.request_id = request_id;
    app.composer.state.error = None;
    tokio::spawn(async move {
        match client.send_message(&session_id, &text, &images).await {
            Ok(response) => {
                let _ = actions.send(ActionUpdate::MessageSent {
                    request_id,
                    session_id,
                    response,
                });
            }
            Err(error) => {
                let _ = actions.send(ActionUpdate::Failed {
                    request_id,
                    detail: error.to_string(),
                });
            }
        }
    });
}

fn is_commands_command(text: &str) -> bool {
    text.trim().eq_ignore_ascii_case("/commands")
}

fn submit_delete(
    app: &mut App,
    client: api::MyHarnessClient,
    actions: mpsc::UnboundedSender<ActionUpdate>,
) {
    let Some(Modal::Delete(form)) = app.modal.as_ref() else {
        return;
    };
    let target = form.target.clone();
    let request_id = app.allocate_request_id();
    mark_modal_submitting(app, request_id);
    tokio::spawn(async move {
        let result = run_delete(&client, &target).await;
        match result {
            Ok((label, removed_sessions)) => match client.sessions().await {
                Ok(tree) => {
                    let _ = actions.send(ActionUpdate::Deleted {
                        request_id,
                        label,
                        removed_sessions,
                        tree: Some(tree),
                        refresh_error: None,
                    });
                }
                Err(error) => {
                    let _ = actions.send(ActionUpdate::Deleted {
                        request_id,
                        label,
                        removed_sessions,
                        tree: None,
                        refresh_error: Some(error.to_string()),
                    });
                }
            },
            Err(error) => {
                let _ = actions.send(ActionUpdate::Failed {
                    request_id,
                    detail: error.to_string(),
                });
            }
        }
    });
}

async fn run_delete(
    client: &api::MyHarnessClient,
    target: &DeleteTarget,
) -> anyhow::Result<(String, Vec<String>)> {
    match target {
        DeleteTarget::Project { id, name, .. } => {
            let result = client.delete_project(id).await?;
            Ok((format!("project '{name}'"), result.removed_sessions))
        }
        DeleteTarget::Task {
            project_id,
            id,
            name,
            ..
        } => {
            let result = client.delete_task(project_id, id).await?;
            Ok((format!("task '{name}'"), result.removed_sessions))
        }
        DeleteTarget::Session { id, name } => {
            client.delete_session(id).await?;
            Ok((format!("session '{name}'"), vec![id.clone()]))
        }
    }
}

fn request_cancel(
    app: &mut App,
    client: api::MyHarnessClient,
    actions: mpsc::UnboundedSender<ActionUpdate>,
) {
    if app.run_state == RunState::Cancelling {
        return;
    }
    let Some(session_id) = app.active_session_id.clone() else {
        app.notice = Some("No active session is open.".to_owned());
        return;
    };
    if app.run_state == RunState::Idle {
        app.notice = Some("The open session is already idle.".to_owned());
        return;
    }
    let previous_state = app.run_state;
    app.run_state = RunState::Cancelling;
    tokio::spawn(async move {
        match client.cancel_run(&session_id).await {
            Ok(_) => {
                let _ = actions.send(ActionUpdate::Cancelled { session_id });
            }
            Err(error) => {
                let _ = actions.send(ActionUpdate::CancelFailed {
                    session_id,
                    previous_state,
                    detail: error.to_string(),
                });
            }
        }
    });
}

fn handle_approval_key(
    app: &mut App,
    key: crossterm::event::KeyEvent,
    client: &api::MyHarnessClient,
    actions: &mpsc::UnboundedSender<ActionUpdate>,
) {
    if app.run_state == RunState::Cancelling {
        return;
    }
    let approved = match key.code {
        KeyCode::Char('y') => true,
        KeyCode::Char('n') => false,
        KeyCode::Char('c') => {
            request_cancel(app, client.clone(), actions.clone());
            return;
        }
        _ => return,
    };
    let Some(prompt) = app.pending_approval.as_ref() else {
        return;
    };
    if prompt.state.submitting {
        return;
    }
    let session_id = prompt.session_id.clone();
    let approval_id = prompt.approval_id.clone();
    let request_id = app.allocate_request_id();
    if let Some(prompt) = app.pending_approval.as_mut() {
        prompt.state.submitting = true;
        prompt.state.request_id = request_id;
        prompt.state.error = None;
    }
    let client = client.clone();
    let actions = actions.clone();
    tokio::spawn(async move {
        match client
            .resolve_approval(&session_id, &approval_id, approved)
            .await
        {
            Ok(_) => {
                let _ = actions.send(ActionUpdate::ApprovalResolved {
                    request_id,
                    approved,
                });
            }
            Err(error) => {
                let _ = actions.send(ActionUpdate::Failed {
                    request_id,
                    detail: error.to_string(),
                });
            }
        }
    });
}

fn handle_question_key(
    app: &mut App,
    key: crossterm::event::KeyEvent,
    client: &api::MyHarnessClient,
    actions: &mpsc::UnboundedSender<ActionUpdate>,
) {
    let Some(question) = app.pending_question.as_mut() else {
        return;
    };
    if question.answer.state.submitting {
        return;
    }
    match key.code {
        KeyCode::Up | KeyCode::BackTab if !question.options.is_empty() => {
            question.selected_option = question.selected_option.saturating_sub(1);
        }
        KeyCode::Down | KeyCode::Tab if !question.options.is_empty() => {
            question.selected_option =
                (question.selected_option + 1).min(question.options.len().saturating_sub(1));
        }
        KeyCode::Enter => submit_question(app, client.clone(), actions.clone()),
        KeyCode::Left if question.allow_free_text => question.answer.move_left(),
        KeyCode::Right if question.allow_free_text => question.answer.move_right(),
        KeyCode::Backspace
            if question.allow_free_text && key.modifiers.contains(KeyModifiers::ALT) =>
        {
            question.answer.delete_word_back();
        }
        KeyCode::Backspace if question.allow_free_text => question.answer.backspace(),
        KeyCode::Delete if question.allow_free_text => question.answer.delete_forward(),
        KeyCode::Char('u')
            if question.allow_free_text && key.modifiers.contains(KeyModifiers::CONTROL) =>
        {
            question.answer.clear_text();
        }
        KeyCode::Char('a')
            if question.allow_free_text && key.modifiers.contains(KeyModifiers::CONTROL) =>
        {
            question.answer.move_line_start();
        }
        KeyCode::Char('e')
            if question.allow_free_text && key.modifiers.contains(KeyModifiers::CONTROL) =>
        {
            question.answer.move_line_end();
        }
        KeyCode::Char('w')
            if question.allow_free_text && key.modifiers.contains(KeyModifiers::CONTROL) =>
        {
            question.answer.delete_word_back();
        }
        KeyCode::Char(character) if question.allow_free_text && is_text_input(key.modifiers) => {
            question.answer.insert_char(character);
        }
        KeyCode::Esc => {
            question.answer.state.error =
                Some("Answer the question or press Ctrl+C to cancel the run.".to_owned());
        }
        _ => {}
    }
}

fn submit_question(
    app: &mut App,
    client: api::MyHarnessClient,
    actions: mpsc::UnboundedSender<ActionUpdate>,
) {
    let Some(question) = app.pending_question.as_ref() else {
        return;
    };
    if question.answer.state.submitting {
        return;
    }
    let Some(answer) = question.response() else {
        if let Some(question) = app.pending_question.as_mut() {
            question.answer.state.error = Some("Enter an answer before submitting.".to_owned());
        }
        return;
    };
    let session_id = question.session_id.clone();
    let question_id = question.question_id.clone();
    let request_id = app.allocate_request_id();
    if let Some(question) = app.pending_question.as_mut() {
        question.answer.state.submitting = true;
        question.answer.state.request_id = request_id;
        question.answer.state.error = None;
    }
    tokio::spawn(async move {
        match client
            .answer_question(&session_id, &question_id, &answer)
            .await
        {
            Ok(_) => {
                let _ = actions.send(ActionUpdate::QuestionAnswered { request_id, answer });
            }
            Err(error) => {
                let _ = actions.send(ActionUpdate::Failed {
                    request_id,
                    detail: error.to_string(),
                });
            }
        }
    });
}

enum CreationRequest {
    Project {
        name: String,
        root: String,
    },
    Task {
        project_id: String,
        name: String,
    },
    Session {
        project_id: String,
        task_id: String,
        title: String,
        provider: &'static str,
    },
}

fn submit_modal(
    app: &mut App,
    client: api::MyHarnessClient,
    actions: mpsc::UnboundedSender<ActionUpdate>,
) {
    let request = match app.modal.as_ref() {
        Some(Modal::Project(form)) => {
            if form.name.trim().is_empty() || form.root.trim().is_empty() {
                set_modal_error(app, "Project name and workspace root are required.");
                return;
            }
            CreationRequest::Project {
                name: form.name.trim().to_owned(),
                root: form.root.trim().to_owned(),
            }
        }
        Some(Modal::Task(form)) => {
            if form.name.trim().is_empty() {
                set_modal_error(app, "Task name is required.");
                return;
            }
            CreationRequest::Task {
                project_id: form.project_id.clone(),
                name: form.name.trim().to_owned(),
            }
        }
        Some(Modal::Session(form)) => {
            let Some(provider) = form.providers.get(form.provider_index).copied() else {
                set_modal_error(app, "No backend provider is currently available.");
                return;
            };
            CreationRequest::Session {
                project_id: form.project_id.clone(),
                task_id: form.task_id.clone(),
                title: form.title.trim().to_owned(),
                provider: provider.api_value(),
            }
        }
        Some(Modal::Delete(_)) => return,
        None => return,
    };
    let request_id = app.allocate_request_id();
    mark_modal_submitting(app, request_id);
    tokio::spawn(async move {
        let result = run_creation(&client, request).await;
        let update = match result {
            Ok((selected, session_id, notice)) => match client.sessions().await {
                Ok(tree) => ActionUpdate::Created {
                    request_id,
                    selected,
                    session_id,
                    notice,
                    tree: Some(tree),
                    refresh_error: None,
                },
                Err(error) => ActionUpdate::Created {
                    request_id,
                    selected,
                    session_id,
                    notice,
                    tree: None,
                    refresh_error: Some(error.to_string()),
                },
            },
            Err(error) => ActionUpdate::Failed {
                request_id,
                detail: error.to_string(),
            },
        };
        let _ = actions.send(update);
    });
}

async fn run_creation(
    client: &api::MyHarnessClient,
    request: CreationRequest,
) -> anyhow::Result<(NavKey, Option<String>, String)> {
    let (selected, session_id, notice) = match request {
        CreationRequest::Project { name, root } => {
            let before = client.sessions().await?;
            let created = client.create_project(&name, &root).await?;
            let existed = before
                .projects
                .iter()
                .any(|project| project.id == created.id);
            let notice = project_creation_notice(&created.name, existed);
            (NavKey::Project(created.id), None, notice)
        }
        CreationRequest::Task { project_id, name } => {
            let created = client.create_task(&project_id, &name).await?;
            let notice = format!("Created task '{}'.", created.name);
            (
                NavKey::Task {
                    project_id,
                    task_id: created.id,
                },
                None,
                notice,
            )
        }
        CreationRequest::Session {
            project_id,
            task_id,
            title,
            provider,
        } => {
            // The Chats bucket uses a dedicated endpoint that stamps the chat
            // kind and a per-chat workspace; a plain session create would not.
            let created = if project_id == "__chats__" {
                client.create_chat(&title, provider).await?
            } else {
                client
                    .create_session(&project_id, &task_id, &title, provider)
                    .await?
            };
            let id = created.id;
            let notice = format!("Created and opened session '{}'.", created.title);
            (NavKey::Session(id.clone()), Some(id), notice)
        }
    };
    Ok((selected, session_id, notice))
}

fn project_creation_notice(name: &str, existed: bool) -> String {
    if existed {
        format!("Opened existing project '{name}'; one project is allowed per workspace root.")
    } else {
        format!("Created project '{name}'.")
    }
}

fn mark_modal_submitting(app: &mut App, request_id: u64) {
    if let Some(modal) = app.modal.as_mut() {
        let state = modal.state_mut();
        state.submitting = true;
        state.request_id = request_id;
        state.error = None;
    }
}

fn set_modal_error(app: &mut App, detail: impl Into<String>) {
    let detail = detail.into();
    match app.modal.as_mut() {
        Some(modal) => modal.state_mut().error = Some(detail),
        None => app.notice = Some(detail),
    }
}

fn modal_request_matches(app: &App, request_id: u64) -> bool {
    app.modal
        .as_ref()
        .is_some_and(|modal| modal.state().request_id == request_id)
}

fn apply_action(
    app: &mut App,
    action: ActionUpdate,
    client: &api::MyHarnessClient,
    updates: &mpsc::UnboundedSender<StreamUpdate>,
    generation: &mut u64,
    stream_task: &mut Option<JoinHandle<()>>,
) -> Result<()> {
    match action {
        ActionUpdate::Created {
            request_id,
            selected,
            session_id,
            notice,
            tree,
            refresh_error,
        } if modal_request_matches(app, request_id) => {
            app.modal = None;
            if let Some(tree) = tree {
                if app.replace_tree(tree, Some(selected)) {
                    *generation = generation.wrapping_add(1);
                    if let Some(task) = stream_task.take() {
                        task.abort();
                    }
                }
                app.notice = Some(notice);
                if session_id.is_some() {
                    let next = connect_session(client, app, updates, generation, session_id)?;
                    replace_stream(stream_task, next);
                }
            } else {
                app.notice = Some(format!(
                    "{notice} Tree refresh failed: {}",
                    refresh_error.unwrap_or_else(|| "unknown error".to_owned())
                ));
                if session_id.is_some() {
                    let next = connect_session(client, app, updates, generation, session_id)?;
                    replace_stream(stream_task, next);
                }
            }
        }
        ActionUpdate::Failed { request_id, detail }
            if app.composer.state.submitting && app.composer.state.request_id == request_id =>
        {
            app.composer.state.submitting = false;
            app.composer.state.error = Some(detail);
        }
        ActionUpdate::Failed { request_id, detail } if modal_request_matches(app, request_id) => {
            if let Some(modal) = app.modal.as_mut() {
                modal.state_mut().submitting = false;
            }
            set_modal_error(app, detail);
        }
        ActionUpdate::Failed { request_id, detail }
            if app
                .pending_approval
                .as_ref()
                .is_some_and(|prompt| prompt.state.request_id == request_id) =>
        {
            if let Some(prompt) = app.pending_approval.as_mut() {
                prompt.state.submitting = false;
                prompt.state.error = Some(detail);
            }
        }
        ActionUpdate::Failed { request_id, detail }
            if app
                .pending_question
                .as_ref()
                .is_some_and(|question| question.answer.state.request_id == request_id) =>
        {
            if let Some(question) = app.pending_question.as_mut() {
                question.answer.state.submitting = false;
                question.answer.state.error = Some(detail);
            }
        }
        ActionUpdate::MessageSent {
            request_id,
            session_id,
            response,
        } if app.composer.state.submitting
            && app.composer.state.request_id == request_id
            && app.composer.session_id.as_deref() == Some(session_id.as_str()) =>
        {
            app.composer.state.submitting = false;
            if response.status == "blocked" {
                app.composer.state.error = Some(response.detail);
            } else {
                let sent = app.composer.text.clone();
                app.record_sent_message(&session_id, &sent);
                app.composer.clear_text();
                app.run_state = match response.status.as_str() {
                    "started" | "queued" => RunState::Running,
                    _ => RunState::Idle,
                };
                app.notice = Some(match response.status.as_str() {
                    "started" => "Run started.".to_owned(),
                    "queued" => "Message queued behind the active run.".to_owned(),
                    "command" => "Command completed.".to_owned(),
                    other => format!("Message status: {other}"),
                });
            }
        }
        ActionUpdate::ApprovalResolved {
            request_id,
            approved,
        } if app
            .pending_approval
            .as_ref()
            .is_some_and(|prompt| prompt.state.request_id == request_id) =>
        {
            app.pending_approval = None;
            app.run_state = RunState::Running;
            app.notice = Some(if approved {
                "Approval granted.".to_owned()
            } else {
                "Approval denied.".to_owned()
            });
        }
        ActionUpdate::QuestionAnswered { request_id, answer }
            if app
                .pending_question
                .as_ref()
                .is_some_and(|question| question.answer.state.request_id == request_id) =>
        {
            app.pending_question = None;
            app.run_state = RunState::Running;
            app.notice = Some(format!("Question answered: {answer}"));
        }
        ActionUpdate::Cancelled { session_id }
            if app.active_session_id.as_deref() == Some(session_id.as_str()) =>
        {
            app.run_state = RunState::Cancelling;
            app.notice = Some("Cancellation requested; waiting for the run to stop.".to_owned());
        }
        ActionUpdate::CancelFailed {
            session_id,
            previous_state,
            detail,
        } if app.active_session_id.as_deref() == Some(session_id.as_str()) => {
            apply_cancel_failure(app, previous_state, detail);
        }
        ActionUpdate::Deleted {
            request_id,
            label,
            removed_sessions,
            tree,
            refresh_error,
        } if modal_request_matches(app, request_id) => {
            let removed_active = app
                .active_session_id
                .as_ref()
                .is_some_and(|active| removed_sessions.contains(active));
            if removed_active {
                *generation = generation.wrapping_add(1);
                if let Some(task) = stream_task.take() {
                    task.abort();
                }
                app.clear_active_session();
            }
            app.modal = None;
            if let Some(tree) = tree {
                if app.replace_tree(tree, None) {
                    *generation = generation.wrapping_add(1);
                    if let Some(task) = stream_task.take() {
                        task.abort();
                    }
                }
                app.notice = Some(format!("Deleted {label}."));
            } else {
                app.notice = Some(format!(
                    "Deleted {label}. Tree refresh failed: {}",
                    refresh_error.unwrap_or_else(|| "unknown error".to_owned())
                ));
            }
        }
        ActionUpdate::HealthRefreshed { health } => app.update_health(health),
        ActionUpdate::TreeRefreshed { tree } => {
            if app.replace_tree(tree, None) {
                *generation = generation.wrapping_add(1);
                if let Some(task) = stream_task.take() {
                    task.abort();
                }
            }
        }
        ActionUpdate::ReconnectDue {
            session_id,
            scheduled_generation,
        } => {
            let still_current = *generation == scheduled_generation
                && app.active_session_id.as_deref() == Some(session_id.as_str());
            if still_current {
                let next = connect_session(client, app, updates, generation, Some(session_id))?;
                replace_stream(stream_task, next);
            }
        }
        ActionUpdate::OlderEvents {
            session_id,
            scheduled_generation,
            page,
        } => {
            app.loading_older = false;
            let still_current = *generation == scheduled_generation
                && app.active_session_id.as_deref() == Some(session_id.as_str());
            match page {
                Ok(page) if still_current => {
                    app.prepend_events(&page.events, page.offset);
                    app.notice = Some(format!("Loaded {} older events.", page.events.len()));
                }
                Ok(_) => {}
                Err(detail) => {
                    app.notice = Some(format!("Loading older events failed: {detail}"));
                }
            }
        }
        ActionUpdate::Notice { text } => app.notice = Some(text),
        _ => {}
    }
    Ok(())
}

fn apply_cancel_failure(app: &mut App, previous_state: RunState, detail: String) {
    if detail.contains("No active run") {
        app.run_state = RunState::Idle;
        app.pending_approval = None;
        app.pending_question = None;
    } else {
        app.run_state = previous_state;
    }
    app.notice = Some(detail);
}

fn connect_selected(
    client: &api::MyHarnessClient,
    app: &mut App,
    updates: &mpsc::UnboundedSender<StreamUpdate>,
    generation: &mut u64,
) -> Result<Option<JoinHandle<()>>> {
    let selected_id = app.active_session_id.clone();
    connect_session(client, app, updates, generation, selected_id)
}

fn connect_session(
    client: &api::MyHarnessClient,
    app: &mut App,
    updates: &mpsc::UnboundedSender<StreamUpdate>,
    generation: &mut u64,
    session_id: Option<String>,
) -> Result<Option<JoinHandle<()>>> {
    let Some(session_id) = session_id else {
        app.connection = "no sessions".to_owned();
        return Ok(None);
    };
    *generation = generation.wrapping_add(1);
    app.begin_connection(session_id.clone());
    let url = client.session_ws_url(&session_id)?;
    let tx = updates.clone();
    let current_generation = *generation;
    Ok(Some(tokio::spawn(async move {
        ws::stream_session(url, current_generation, tx).await;
    })))
}

fn replace_stream(current: &mut Option<JoinHandle<()>>, next: Option<JoinHandle<()>>) {
    if let Some(task) = current.take() {
        task.abort();
    }
    *current = next;
}

fn apply_update(
    app: &mut App,
    update: StreamUpdate,
    generation: u64,
    actions: &mpsc::UnboundedSender<ActionUpdate>,
) {
    match update {
        StreamUpdate::Connected {
            generation: event_generation,
        } if event_generation == generation => {
            app.connection = "connected".to_owned();
            app.reconnect_attempt = 0;
        }
        StreamUpdate::Loaded {
            generation: event_generation,
            loaded,
        } if event_generation == generation => {
            app.apply_loaded(loaded);
        }
        StreamUpdate::Event {
            generation: event_generation,
            event,
        } if event_generation == generation => {
            app.apply_event(&event);
        }
        StreamUpdate::Disconnected {
            generation: event_generation,
            detail,
        } if event_generation == generation => {
            app.connection = format!("disconnected: {detail}");
            schedule_reconnect(app, generation, actions);
        }
        _ => {}
    }
}

fn schedule_reconnect(
    app: &mut App,
    generation: u64,
    actions: &mpsc::UnboundedSender<ActionUpdate>,
) {
    let Some(session_id) = app.active_session_id.clone() else {
        return;
    };
    app.reconnect_attempt = app.reconnect_attempt.saturating_add(1);
    let delay = app::reconnect_delay_secs(app.reconnect_attempt);
    app.connection = format!(
        "{} · retrying in {delay}s (attempt {})",
        app.connection, app.reconnect_attempt
    );
    let actions = actions.clone();
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_secs(delay)).await;
        let _ = actions.send(ActionUpdate::ReconnectDue {
            session_id,
            scheduled_generation: generation,
        });
    });
}

struct TerminalSession {
    terminal: Terminal<CrosstermBackend<io::Stdout>>,
    keyboard_enhanced: bool,
}

#[cfg(windows)]
fn enable_utf8_console() {
    use windows_sys::Win32::System::Console::{SetConsoleCP, SetConsoleOutputCP};
    const CP_UTF8: u32 = 65001;
    unsafe {
        SetConsoleCP(CP_UTF8);
        SetConsoleOutputCP(CP_UTF8);
    }
}

impl TerminalSession {
    fn enter() -> Result<Self> {
        #[cfg(windows)]
        enable_utf8_console();
        enable_raw_mode()?;
        let mut stdout = io::stdout();
        if let Err(error) = execute!(stdout, EnterAlternateScreen, EnableBracketedPaste) {
            let _ = disable_raw_mode();
            return Err(error.into());
        }
        // Shift+Enter is only distinguishable from Enter when the terminal
        // speaks the kitty keyboard protocol; enable it where supported.
        let keyboard_enhanced = crossterm::terminal::supports_keyboard_enhancement()
            .unwrap_or(false)
            && execute!(
                stdout,
                event::PushKeyboardEnhancementFlags(
                    event::KeyboardEnhancementFlags::DISAMBIGUATE_ESCAPE_CODES
                )
            )
            .is_ok();
        let backend = CrosstermBackend::new(stdout);
        let terminal = match Terminal::new(backend) {
            Ok(terminal) => terminal,
            Err(error) => {
                let _ = disable_raw_mode();
                let mut stdout = io::stdout();
                if keyboard_enhanced {
                    let _ = execute!(stdout, event::PopKeyboardEnhancementFlags);
                }
                let _ = execute!(stdout, DisableBracketedPaste, LeaveAlternateScreen);
                return Err(error.into());
            }
        };
        Ok(Self {
            terminal,
            keyboard_enhanced,
        })
    }

    fn terminal_mut(&mut self) -> &mut Terminal<CrosstermBackend<io::Stdout>> {
        &mut self.terminal
    }
}

enum InputUpdate {
    Event(Event),
    Error(String),
}

fn spawn_input_reader() -> mpsc::UnboundedReceiver<InputUpdate> {
    let (tx, rx) = mpsc::unbounded_channel();
    thread::spawn(move || {
        while !tx.is_closed() {
            match event::poll(Duration::from_millis(100)) {
                Ok(true) => match event::read() {
                    Ok(event) => {
                        if tx.send(InputUpdate::Event(event)).is_err() {
                            break;
                        }
                    }
                    Err(error) => {
                        let _ = tx.send(InputUpdate::Error(error.to_string()));
                        break;
                    }
                },
                Ok(false) => {}
                Err(error) => {
                    let _ = tx.send(InputUpdate::Error(error.to_string()));
                    break;
                }
            }
        }
    });
    rx
}

impl Drop for TerminalSession {
    fn drop(&mut self) {
        let _ = disable_raw_mode();
        if self.keyboard_enhanced {
            let _ = execute!(
                self.terminal.backend_mut(),
                event::PopKeyboardEnhancementFlags
            );
        }
        let _ = execute!(
            self.terminal.backend_mut(),
            DisableBracketedPaste,
            LeaveAlternateScreen
        );
        let _ = self.terminal.show_cursor();
    }
}

#[cfg(test)]
mod tests {
    use super::{
        apply_cancel_failure, handle_commands_key_with_max, handle_conversation_key,
        handle_question_key, is_commands_command, project_creation_notice, submit_inline_composer,
        submit_question, toggle_pane_focus,
    };
    use crate::{
        api::{Health, MyHarnessClient, SessionTree},
        app::{App, ComposerState, PaneFocus, QuestionPrompt, RunState},
    };
    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
    use tokio::sync::mpsc;

    #[test]
    fn duplicate_workspace_root_is_reported_as_existing_project() {
        assert_eq!(
            project_creation_notice("Existing", true),
            "Opened existing project 'Existing'; one project is allowed per workspace root."
        );
        assert_eq!(
            project_creation_notice("New", false),
            "Created project 'New'."
        );
    }

    #[test]
    fn cancel_failure_restores_or_reconciles_run_state() {
        let mut app = App::new(
            "http://localhost".to_owned(),
            Health::default(),
            SessionTree::default(),
            None,
        );
        apply_cancel_failure(
            &mut app,
            RunState::WaitingApproval,
            "temporary network failure".to_owned(),
        );
        assert_eq!(app.run_state, RunState::WaitingApproval);
        apply_cancel_failure(&mut app, RunState::Running, "No active run".to_owned());
        assert_eq!(app.run_state, RunState::Idle);
    }

    #[test]
    fn tab_focus_toggle_requires_an_open_session() {
        let mut app = App::new(
            "http://localhost".to_owned(),
            Health::default(),
            SessionTree::default(),
            None,
        );
        toggle_pane_focus(&mut app);
        assert_eq!(app.pane_focus, PaneFocus::Navigator);
        app.active_session_id = Some("ses_1".to_owned());
        toggle_pane_focus(&mut app);
        assert_eq!(app.pane_focus, PaneFocus::Conversation);
        toggle_pane_focus(&mut app);
        assert_eq!(app.pane_focus, PaneFocus::Navigator);
    }

    #[test]
    fn question_input_supports_custom_text_and_option_navigation() {
        let mut app = App::new(
            "http://localhost".to_owned(),
            Health::default(),
            SessionTree::default(),
            None,
        );
        app.pending_question = Some(QuestionPrompt {
            session_id: "ses_1".to_owned(),
            question_id: "qst_1".to_owned(),
            question: "Which branch?".to_owned(),
            options: vec!["main".to_owned(), "release".to_owned()],
            allow_free_text: true,
            selected_option: 0,
            answer: ComposerState::default(),
        });
        let client = MyHarnessClient::new("http://localhost:8420").unwrap();
        let (actions, _rx) = mpsc::unbounded_channel();

        handle_question_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('x'), KeyModifiers::NONE),
            &client,
            &actions,
        );
        assert_eq!(app.pending_question.as_ref().unwrap().answer.text, "x");
        handle_question_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('u'), KeyModifiers::CONTROL),
            &client,
            &actions,
        );
        handle_question_key(
            &mut app,
            KeyEvent::new(KeyCode::Down, KeyModifiers::NONE),
            &client,
            &actions,
        );
        let question = app.pending_question.as_ref().unwrap();
        assert!(question.answer.text.is_empty());
        assert_eq!(question.selected_option, 1);
    }

    #[test]
    fn blank_free_text_question_is_not_submitted() {
        let mut app = App::new(
            "http://localhost".to_owned(),
            Health::default(),
            SessionTree::default(),
            None,
        );
        app.pending_question = Some(QuestionPrompt {
            session_id: "ses_1".to_owned(),
            question_id: "qst_1".to_owned(),
            question: "Name it".to_owned(),
            options: Vec::new(),
            allow_free_text: true,
            selected_option: 0,
            answer: ComposerState::default(),
        });
        let client = MyHarnessClient::new("http://localhost:8420").unwrap();
        let (actions, _rx) = mpsc::unbounded_channel();

        submit_question(&mut app, client, actions);

        let question = app.pending_question.as_ref().unwrap();
        assert!(!question.answer.state.submitting);
        assert_eq!(
            question.answer.state.error.as_deref(),
            Some("Enter an answer before submitting.")
        );
    }

    #[test]
    fn arrow_keys_scroll_transcript_while_composing() {
        let mut app = App::new(
            "http://localhost".to_owned(),
            Health::default(),
            SessionTree::default(),
            None,
        );
        app.pane_focus = PaneFocus::Conversation;
        let client = MyHarnessClient::new("http://localhost").unwrap();
        let (actions, _updates) = mpsc::unbounded_channel();

        handle_conversation_key(
            &mut app,
            KeyEvent::new(KeyCode::Up, KeyModifiers::NONE),
            &client,
            &actions,
        );
        assert_eq!(app.scroll_from_bottom, 1);

        handle_conversation_key(
            &mut app,
            KeyEvent::new(KeyCode::Down, KeyModifiers::NONE),
            &client,
            &actions,
        );
        assert_eq!(app.scroll_from_bottom, 0);

        handle_conversation_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('j'), KeyModifiers::NONE),
            &client,
            &actions,
        );
        assert_eq!(app.composer.text, "j");

        app.composer.state.submitting = true;
        handle_conversation_key(
            &mut app,
            KeyEvent::new(KeyCode::Up, KeyModifiers::NONE),
            &client,
            &actions,
        );
        assert_eq!(app.scroll_from_bottom, 1);
    }

    #[test]
    fn ctrl_and_alt_modified_characters_do_not_edit_the_draft() {
        let mut app = App::new(
            "http://localhost".to_owned(),
            Health::default(),
            SessionTree::default(),
            None,
        );
        app.pane_focus = PaneFocus::Conversation;
        let client = MyHarnessClient::new("http://localhost").unwrap();
        let (actions, _updates) = mpsc::unbounded_channel();

        handle_conversation_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('x'), KeyModifiers::CONTROL),
            &client,
            &actions,
        );
        handle_conversation_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('x'), KeyModifiers::ALT),
            &client,
            &actions,
        );
        assert!(app.composer.text.is_empty());

        app.composer.insert_str("draft");
        handle_conversation_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('u'), KeyModifiers::CONTROL),
            &client,
            &actions,
        );
        assert!(app.composer.text.is_empty());
    }

    #[test]
    fn alt_enter_inserts_newline_and_cursor_keys_edit_mid_draft() {
        let mut app = App::new(
            "http://localhost".to_owned(),
            Health::default(),
            SessionTree::default(),
            None,
        );
        app.pane_focus = PaneFocus::Conversation;
        let client = MyHarnessClient::new("http://localhost").unwrap();
        let (actions, _updates) = mpsc::unbounded_channel();

        for character in ['a', 'b'] {
            handle_conversation_key(
                &mut app,
                KeyEvent::new(KeyCode::Char(character), KeyModifiers::NONE),
                &client,
                &actions,
            );
        }
        handle_conversation_key(
            &mut app,
            KeyEvent::new(KeyCode::Enter, KeyModifiers::ALT),
            &client,
            &actions,
        );
        handle_conversation_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('c'), KeyModifiers::NONE),
            &client,
            &actions,
        );
        assert_eq!(app.composer.text, "ab\nc");

        handle_conversation_key(
            &mut app,
            KeyEvent::new(KeyCode::Left, KeyModifiers::NONE),
            &client,
            &actions,
        );
        handle_conversation_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('x'), KeyModifiers::NONE),
            &client,
            &actions,
        );
        assert_eq!(app.composer.text, "ab\nxc");
    }

    #[test]
    fn pasted_text_preserves_newlines_at_the_cursor() {
        let mut app = App::new(
            "http://localhost".to_owned(),
            Health::default(),
            SessionTree::default(),
            None,
        );
        app.composer.insert_str("ab");
        app.composer.move_left();
        super::append_composer_text(&mut app, "one\r\ntwo\rthree");
        assert_eq!(app.composer.text, "aone\ntwo\nthreeb");
    }

    #[test]
    fn commands_command_is_exact_and_case_insensitive() {
        assert!(is_commands_command("/commands"));
        assert!(is_commands_command("  /COMMANDS  "));
        assert!(!is_commands_command("/commands extra"));
        assert!(!is_commands_command("show /commands"));
    }

    #[test]
    fn commands_command_opens_local_pane_during_active_run() {
        let mut app = App::new(
            "http://localhost".to_owned(),
            Health::default(),
            SessionTree::default(),
            None,
        );
        app.composer.session_id = Some("ses_1".to_owned());
        app.composer.text = " /commands ".to_owned();
        app.run_state = RunState::Running;
        let client = MyHarnessClient::new("http://localhost").unwrap();
        let (actions, _updates) = mpsc::unbounded_channel();

        submit_inline_composer(&mut app, client, actions);

        assert_eq!(app.commands_scroll, Some(0));
        assert!(app.composer.text.is_empty());
        assert!(app.composer.state.error.is_none());
        assert!(!app.composer.state.submitting);
    }

    #[test]
    fn commands_pane_scrolls_and_closes() {
        let mut app = App::new(
            "http://localhost".to_owned(),
            Health::default(),
            SessionTree::default(),
            None,
        );
        app.commands_scroll = Some(0);

        handle_commands_key_with_max(
            &mut app,
            KeyEvent::new(KeyCode::PageDown, KeyModifiers::NONE),
            12,
        );
        assert_eq!(app.commands_scroll, Some(10));
        handle_commands_key_with_max(
            &mut app,
            KeyEvent::new(KeyCode::End, KeyModifiers::NONE),
            12,
        );
        assert_eq!(app.commands_scroll, Some(12));
        handle_commands_key_with_max(
            &mut app,
            KeyEvent::new(KeyCode::Home, KeyModifiers::NONE),
            12,
        );
        assert_eq!(app.commands_scroll, Some(0));
        handle_commands_key_with_max(
            &mut app,
            KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE),
            12,
        );
        assert_eq!(app.commands_scroll, None);
    }
}

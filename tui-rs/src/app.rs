use crate::{
    api::{Health, SessionMeta, SessionTree},
    events::{render_event, EventEnvelope, SessionLoaded, TranscriptEntry},
};

#[derive(Clone, Debug)]
pub struct SessionRow {
    pub project_name: String,
    pub task_name: String,
    pub meta: SessionMeta,
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub enum NavKey {
    Project(String),
    Task { project_id: String, task_id: String },
    Session(String),
}

#[derive(Clone, Debug)]
pub enum NavRow {
    Project {
        id: String,
        name: String,
        root: String,
    },
    Task {
        project_id: String,
        id: String,
        name: String,
    },
    Session(SessionRow),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FormFocus {
    First,
    Second,
    Submit,
}

impl FormFocus {
    pub fn next(self) -> Self {
        match self {
            Self::First => Self::Second,
            Self::Second => Self::Submit,
            Self::Submit => Self::First,
        }
    }

    pub fn previous(self) -> Self {
        match self {
            Self::First => Self::Submit,
            Self::Second => Self::First,
            Self::Submit => Self::Second,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Provider {
    Native,
    Codex,
    Claude,
}

impl Provider {
    pub fn api_value(self) -> &'static str {
        match self {
            Self::Native => "native",
            Self::Codex => "codex-app-server",
            Self::Claude => "claude-agent",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Native => "Native",
            Self::Codex => "Codex app-server",
            Self::Claude => "Claude",
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct FormState {
    pub error: Option<String>,
    pub submitting: bool,
    pub request_id: u64,
}

#[derive(Clone, Debug)]
pub struct ProjectForm {
    pub name: String,
    pub root: String,
    pub focus: FormFocus,
    pub state: FormState,
}

#[derive(Clone, Debug)]
pub struct TaskForm {
    pub project_id: String,
    pub project_name: String,
    pub name: String,
    pub focus: FormFocus,
    pub state: FormState,
}

#[derive(Clone, Debug)]
pub struct SessionForm {
    pub project_id: String,
    pub task_id: String,
    pub project_name: String,
    pub task_name: String,
    pub title: String,
    pub providers: Vec<Provider>,
    pub provider_index: usize,
    pub focus: FormFocus,
    pub state: FormState,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PaneFocus {
    Navigator,
    Conversation,
}

/// Maximum number of image attachments per message, mirroring the backend's
/// `_MAX_ATTACHMENTS_PER_MESSAGE` in `backend/web_helpers.py`.
pub const MAX_PENDING_IMAGES: usize = 4;
/// Maximum size of a single image attachment, mirroring the backend's
/// `_MAX_ATTACHMENT_BYTES` in `backend/web_helpers.py`.
pub const MAX_IMAGE_BYTES: usize = 10 * 1024 * 1024;

#[derive(Clone, Debug)]
pub struct PendingImage {
    pub name: String,
    pub mime: String,
    pub data_url: String,
    pub size_bytes: usize,
}

#[derive(Clone, Debug, Default)]
pub struct ComposerState {
    pub session_id: Option<String>,
    pub text: String,
    pub cursor: usize,
    pub state: FormState,
    pub history_pos: Option<usize>,
    pub stash: String,
    pub images: Vec<PendingImage>,
}

impl ComposerState {
    /// Attempts to add a pasted clipboard image, enforcing the same limits
    /// the backend applies when it saves message attachments.
    pub fn add_image(&mut self, image: PendingImage) -> Result<(), String> {
        if self.images.len() >= MAX_PENDING_IMAGES {
            return Err(format!(
                "Only {MAX_PENDING_IMAGES} image attachments are allowed per message."
            ));
        }
        if image.size_bytes > MAX_IMAGE_BYTES {
            return Err("Pasted image is larger than the 10 MB attachment limit.".to_owned());
        }
        self.images.push(image);
        self.state.error = None;
        Ok(())
    }

    pub fn remove_last_image(&mut self) -> bool {
        self.images.pop().is_some()
    }

    pub fn insert_char(&mut self, character: char) {
        self.text.insert(self.cursor, character);
        self.cursor += character.len_utf8();
        self.state.error = None;
        self.history_pos = None;
    }

    pub fn insert_str(&mut self, text: &str) {
        self.text.insert_str(self.cursor, text);
        self.cursor += text.len();
        self.state.error = None;
        self.history_pos = None;
    }

    pub fn backspace(&mut self) {
        if let Some(character) = self.text[..self.cursor].chars().next_back() {
            self.cursor -= character.len_utf8();
            self.text.remove(self.cursor);
        }
        self.state.error = None;
        self.history_pos = None;
    }

    pub fn delete_forward(&mut self) {
        if self.cursor < self.text.len() {
            self.text.remove(self.cursor);
        }
        self.state.error = None;
        self.history_pos = None;
    }

    pub fn move_left(&mut self) {
        if let Some(character) = self.text[..self.cursor].chars().next_back() {
            self.cursor -= character.len_utf8();
        }
    }

    pub fn move_right(&mut self) {
        if let Some(character) = self.text[self.cursor..].chars().next() {
            self.cursor += character.len_utf8();
        }
    }

    /// Move the cursor to the same column of the previous draft line.
    /// Returns false when the cursor is already on the first line.
    pub fn cursor_up(&mut self) -> bool {
        let before = &self.text[..self.cursor];
        let Some(line_start) = before.rfind('\n') else {
            return false;
        };
        let column = before[line_start + 1..].chars().count();
        let previous_start = before[..line_start].rfind('\n').map_or(0, |i| i + 1);
        let previous_line = &self.text[previous_start..line_start];
        self.cursor = previous_start + byte_offset_of_column(previous_line, column);
        true
    }

    /// Move the cursor to the same column of the next draft line.
    /// Returns false when the cursor is already on the last line.
    pub fn cursor_down(&mut self) -> bool {
        let Some(line_end) = self.text[self.cursor..].find('\n').map(|i| self.cursor + i) else {
            return false;
        };
        let current_start = self.text[..self.cursor].rfind('\n').map_or(0, |i| i + 1);
        let column = self.text[current_start..self.cursor].chars().count();
        let next_start = line_end + 1;
        let next_end = self.text[next_start..]
            .find('\n')
            .map_or(self.text.len(), |i| next_start + i);
        let next_line = &self.text[next_start..next_end];
        self.cursor = next_start + byte_offset_of_column(next_line, column);
        true
    }

    /// Byte range of the logical draft line holding the cursor.
    fn current_line(&self) -> std::ops::Range<usize> {
        let start = self.text[..self.cursor]
            .rfind('\n')
            .map_or(0, |index| index + 1);
        let end = self.text[self.cursor..]
            .find('\n')
            .map_or(self.text.len(), |index| self.cursor + index);
        start..end
    }

    pub fn move_line_start(&mut self) {
        self.cursor = self.current_line().start;
    }

    pub fn move_line_end(&mut self) {
        self.cursor = self.current_line().end;
    }

    /// Delete the whitespace-delimited word before the cursor (Ctrl+W).
    pub fn delete_word_back(&mut self) {
        let before = &self.text[..self.cursor];
        let trimmed = before.trim_end_matches(char::is_whitespace);
        let start = trimmed
            .rfind(char::is_whitespace)
            .map_or(0, |index| index + 1);
        if start < self.cursor {
            self.text.replace_range(start..self.cursor, "");
            self.cursor = start;
        }
        self.state.error = None;
        self.history_pos = None;
    }

    /// Delete from the cursor to the end of its draft line (Ctrl+K).
    pub fn delete_to_line_end(&mut self) {
        let end = self.current_line().end;
        if end > self.cursor {
            self.text.replace_range(self.cursor..end, "");
        } else if end < self.text.len() {
            // Already at the end of a line: swallow the newline itself.
            self.text.replace_range(end..end + 1, "");
        }
        self.state.error = None;
        self.history_pos = None;
    }

    /// Replace the draft without resetting history navigation (used by recall).
    fn set_recalled_text(&mut self, text: String) {
        self.cursor = text.len();
        self.text = text;
        self.state.error = None;
    }

    pub fn clear_text(&mut self) {
        self.text.clear();
        self.cursor = 0;
        self.state.error = None;
        self.history_pos = None;
        self.stash.clear();
        self.images.clear();
    }
}

fn byte_offset_of_column(line: &str, column: usize) -> usize {
    line.char_indices()
        .nth(column)
        .map_or(line.len(), |(offset, _)| offset)
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct QueueItem {
    pub id: String,
    pub text: String,
}

pub fn queue_items_from_values(values: &[serde_json::Value]) -> Vec<QueueItem> {
    values
        .iter()
        .map(|value| QueueItem {
            id: value
                .get("id")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .to_owned(),
            text: value
                .get("text")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .to_owned(),
        })
        .collect()
}

#[derive(Clone, Debug, Default)]
pub struct ComposerDraft {
    pub text: String,
    pub cursor: usize,
}

#[derive(Clone, Debug, Default)]
pub struct SearchState {
    pub query: String,
    pub cursor: usize,
    /// Transcript indices that match the query, in ascending order.
    pub matches: Vec<usize>,
    /// Position within `matches` of the highlighted entry.
    pub current: usize,
}

#[derive(Clone, Debug)]
pub enum DeleteTarget {
    Project {
        id: String,
        name: String,
        session_count: usize,
    },
    Task {
        project_id: String,
        id: String,
        name: String,
        session_count: usize,
    },
    Session {
        id: String,
        name: String,
    },
}

impl DeleteTarget {
    pub fn label(&self) -> &str {
        match self {
            Self::Project { name, .. } | Self::Task { name, .. } | Self::Session { name, .. } => {
                name
            }
        }
    }
}

#[derive(Clone, Debug)]
pub struct DeleteForm {
    pub target: DeleteTarget,
    pub state: FormState,
}

#[derive(Clone, Debug)]
pub struct ApprovalPrompt {
    pub session_id: String,
    pub approval_id: String,
    pub tool_name: String,
    pub args_json: String,
    pub diff_preview: String,
    pub state: FormState,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RunState {
    Idle,
    Running,
    WaitingApproval,
    Cancelling,
}

impl RunState {
    pub fn label(self) -> &'static str {
        match self {
            Self::Idle => "idle",
            Self::Running => "running",
            Self::WaitingApproval => "waiting approval",
            Self::Cancelling => "cancelling",
        }
    }
}

#[derive(Clone, Debug)]
pub enum Modal {
    Project(ProjectForm),
    Task(TaskForm),
    Session(SessionForm),
    Delete(DeleteForm),
}

impl Modal {
    pub fn state(&self) -> &FormState {
        match self {
            Self::Project(form) => &form.state,
            Self::Task(form) => &form.state,
            Self::Session(form) => &form.state,
            Self::Delete(form) => &form.state,
        }
    }

    pub fn state_mut(&mut self) -> &mut FormState {
        match self {
            Self::Project(form) => &mut form.state,
            Self::Task(form) => &mut form.state,
            Self::Session(form) => &mut form.state,
            Self::Delete(form) => &mut form.state,
        }
    }
}

pub struct App {
    pub backend_url: String,
    pub health: Health,
    pub tree: SessionTree,
    pub rows: Vec<NavRow>,
    pub cursor: usize,
    pub collapsed: std::collections::HashSet<NavKey>,
    pub active_session_id: Option<String>,
    pub transcript: Vec<TranscriptEntry>,
    pub scroll_from_bottom: u16,
    pub connection: String,
    pub history_label: String,
    pub modal: Option<Modal>,
    pub commands_scroll: Option<u16>,
    pub search: Option<SearchState>,
    pub notice: Option<String>,
    pub next_request_id: u64,
    pub pane_focus: PaneFocus,
    pub composer: ComposerState,
    pub run_state: RunState,
    pub queue_items: Vec<QueueItem>,
    pub queue_overlay: Option<usize>,
    pub pending_approval: Option<ApprovalPrompt>,
    pub verbose_tools: bool,
    pub context_percent: Option<f64>,
    pub event_offset: usize,
    pub event_total: usize,
    pub loading_older: bool,
    pub reconnect_attempt: u32,
    pub drafts: std::collections::HashMap<String, ComposerDraft>,
    pub sent_history: std::collections::HashMap<String, Vec<String>>,
    assistant_delta_index: Option<usize>,
}

impl App {
    pub fn new(
        backend_url: String,
        health: Health,
        tree: SessionTree,
        initial_session: Option<&str>,
    ) -> Self {
        let mut collapsed: std::collections::HashSet<NavKey> = tree
            .projects
            .iter()
            .map(|project| NavKey::Project(project.id.clone()))
            .collect();
        if let Some(id) = initial_session {
            expand_ancestors(&tree, &mut collapsed, &NavKey::Session(id.to_owned()));
        }
        let rows = flatten_tree(&tree, &collapsed);
        let cursor = initial_session
            .and_then(|id| {
                rows.iter().position(
                    |row| matches!(row, NavRow::Session(session) if session.meta.id == id),
                )
            })
            .or_else(|| {
                rows.iter()
                    .position(|row| matches!(row, NavRow::Session(_)))
            })
            .unwrap_or(0);
        let active_session_id = rows.get(cursor).and_then(|row| match row {
            NavRow::Session(session) => Some(session.meta.id.clone()),
            NavRow::Project { .. } | NavRow::Task { .. } => None,
        });
        let verbose_tools = effective_verbose(
            &health,
            active_session_id
                .as_deref()
                .and_then(|id| tree.sessions.get(id)),
        );
        Self {
            backend_url,
            health,
            tree,
            rows,
            cursor,
            collapsed,
            active_session_id: active_session_id.clone(),
            transcript: Vec::new(),
            scroll_from_bottom: 0,
            connection: "connecting".to_owned(),
            history_label: String::new(),
            modal: None,
            commands_scroll: None,
            search: None,
            notice: None,
            next_request_id: 0,
            pane_focus: if active_session_id.is_some() {
                PaneFocus::Conversation
            } else {
                PaneFocus::Navigator
            },
            composer: ComposerState {
                session_id: active_session_id.clone(),
                ..ComposerState::default()
            },
            run_state: RunState::Idle,
            queue_items: Vec::new(),
            queue_overlay: None,
            pending_approval: None,
            verbose_tools,
            context_percent: None,
            event_offset: 0,
            event_total: 0,
            loading_older: false,
            reconnect_attempt: 0,
            drafts: std::collections::HashMap::new(),
            sent_history: std::collections::HashMap::new(),
            assistant_delta_index: None,
        }
    }

    pub fn selected(&self) -> Option<&NavRow> {
        self.rows.get(self.cursor)
    }

    pub fn selected_session_id(&self) -> Option<String> {
        match self.selected()? {
            NavRow::Session(row) => Some(row.meta.id.clone()),
            NavRow::Project { .. } | NavRow::Task { .. } => None,
        }
    }

    pub fn selected_project_target(&self) -> Option<(String, String)> {
        let project_id = match self.selected()? {
            NavRow::Project { id, .. } => id,
            NavRow::Task { project_id, .. } => project_id,
            NavRow::Session(row) => &row.meta.project_id,
        };
        self.tree
            .projects
            .iter()
            .find(|project| project.id == *project_id)
            .map(|project| (project.id.clone(), project.name.clone()))
    }

    pub fn selected_task_target(&self) -> Option<(String, String, String, String)> {
        let (project_id, task_id) = match self.selected()? {
            NavRow::Task { project_id, id, .. } => (project_id, id),
            NavRow::Session(row) => (&row.meta.project_id, &row.meta.task_id),
            NavRow::Project { .. } => return None,
        };
        let project = self
            .tree
            .projects
            .iter()
            .find(|project| project.id == *project_id)?;
        let task = project.tasks.iter().find(|task| task.id == *task_id)?;
        Some((
            project.id.clone(),
            task.id.clone(),
            project.name.clone(),
            task.name.clone(),
        ))
    }

    pub fn active(&self) -> Option<&SessionRow> {
        let id = self.active_session_id.as_deref()?;
        self.rows.iter().find_map(|row| match row {
            NavRow::Session(session) if session.meta.id == id => Some(session),
            NavRow::Project { .. } | NavRow::Task { .. } | NavRow::Session(_) => None,
        })
    }

    pub fn move_cursor(&mut self, delta: isize) {
        if self.rows.is_empty() {
            return;
        }
        let last = self.rows.len() - 1;
        self.cursor = if delta.is_negative() {
            self.cursor.saturating_sub(delta.unsigned_abs())
        } else {
            self.cursor.saturating_add(delta as usize).min(last)
        };
    }

    /// Returns true if the active session vanished from the refreshed tree,
    /// in which case its stream must be torn down by the caller.
    pub fn replace_tree(&mut self, tree: SessionTree, selected: Option<NavKey>) -> bool {
        let previous = self.selected().map(nav_key);
        self.tree = tree;
        if let Some(key) = selected.as_ref() {
            expand_ancestors(&self.tree, &mut self.collapsed, key);
        }
        self.rows = flatten_tree(&self.tree, &self.collapsed);
        let target = selected.or(previous);
        self.cursor = target
            .and_then(|key| self.rows.iter().position(|row| nav_key(row) == key))
            .unwrap_or_else(|| self.cursor.min(self.rows.len().saturating_sub(1)));
        if self
            .active_session_id
            .as_ref()
            .is_some_and(|id| !self.tree.sessions.contains_key(id))
        {
            self.clear_active_session();
            return true;
        }
        false
    }

    pub fn update_health(&mut self, health: Health) {
        self.health = health;
        let providers = self.available_providers();
        if let Some(Modal::Session(form)) = self.modal.as_mut() {
            if !form.state.submitting {
                let current = form.providers.get(form.provider_index).copied();
                form.provider_index = current
                    .and_then(|provider| providers.iter().position(|entry| *entry == provider))
                    .unwrap_or(0);
                form.providers = providers;
            }
        }
    }

    pub fn available_providers(&self) -> Vec<Provider> {
        let mut providers = Vec::new();
        if self.health.native_enabled {
            providers.push(Provider::Native);
        }
        if self.health.codex_app_server_enabled {
            providers.push(Provider::Codex);
        }
        if self.health.claude_agent_enabled {
            providers.push(Provider::Claude);
        }
        providers
    }

    pub fn selected_delete_target(&self) -> Result<DeleteTarget, String> {
        match self
            .selected()
            .ok_or_else(|| "Nothing is selected.".to_owned())?
        {
            NavRow::Project { id, name, .. } => {
                if id == "__chats__" {
                    return Err("The reserved Chats collection cannot be deleted.".to_owned());
                }
                let project = self
                    .tree
                    .projects
                    .iter()
                    .find(|project| project.id == *id)
                    .ok_or_else(|| "Selected project is no longer available.".to_owned())?;
                let session_count = project.tasks.iter().map(|task| task.sessions.len()).sum();
                Ok(DeleteTarget::Project {
                    id: id.clone(),
                    name: name.clone(),
                    session_count,
                })
            }
            NavRow::Task {
                project_id,
                id,
                name,
            } => {
                if project_id == "__chats__" {
                    return Err("The reserved Chats task cannot be deleted.".to_owned());
                }
                let session_count = self
                    .tree
                    .projects
                    .iter()
                    .find(|project| project.id == *project_id)
                    .and_then(|project| project.tasks.iter().find(|task| task.id == *id))
                    .map(|task| task.sessions.len())
                    .unwrap_or(0);
                Ok(DeleteTarget::Task {
                    project_id: project_id.clone(),
                    id: id.clone(),
                    name: name.clone(),
                    session_count,
                })
            }
            NavRow::Session(row) => Ok(DeleteTarget::Session {
                id: row.meta.id.clone(),
                name: row.meta.title.clone(),
            }),
        }
    }

    pub fn allocate_request_id(&mut self) -> u64 {
        self.next_request_id = self.next_request_id.wrapping_add(1);
        self.next_request_id
    }

    pub fn begin_connection(&mut self, session_id: String) {
        self.stash_composer_draft();
        self.commands_scroll = None;
        self.search = None;
        self.queue_overlay = None;
        self.verbose_tools = effective_verbose(&self.health, self.tree.sessions.get(&session_id));
        self.active_session_id = Some(session_id.clone());
        self.pane_focus = PaneFocus::Conversation;
        let draft = self.drafts.get(&session_id).cloned().unwrap_or_default();
        self.composer = ComposerState {
            session_id: Some(session_id),
            cursor: draft.cursor.min(draft.text.len()),
            text: draft.text,
            ..ComposerState::default()
        };
        self.transcript.clear();
        self.scroll_from_bottom = 0;
        self.history_label.clear();
        self.connection = "connecting".to_owned();
        self.run_state = RunState::Idle;
        self.queue_items.clear();
        self.pending_approval = None;
        self.context_percent = None;
        self.event_offset = 0;
        self.event_total = 0;
        self.loading_older = false;
        self.assistant_delta_index = None;
    }

    fn stash_composer_draft(&mut self) {
        let Some(previous) = self.composer.session_id.clone() else {
            return;
        };
        if self.composer.text.is_empty() {
            self.drafts.remove(&previous);
        } else {
            self.drafts.insert(
                previous,
                ComposerDraft {
                    text: std::mem::take(&mut self.composer.text),
                    cursor: self.composer.cursor,
                },
            );
        }
    }

    pub fn apply_loaded(&mut self, loaded: SessionLoaded) {
        self.verbose_tools = effective_verbose(&self.health, Some(&loaded.meta));
        self.context_percent = loaded.events.iter().filter_map(context_percent).next_back();
        let is_running = loaded.meta.status == "running";
        self.queue_items = queue_items_from_values(&loaded.meta.message_queue);
        self.clamp_queue_overlay();
        self.pending_approval = if is_running {
            unmatched_approval(&loaded.events, &loaded.meta.id)
        } else {
            None
        };
        self.run_state = if self.pending_approval.is_some() {
            RunState::WaitingApproval
        } else if is_running {
            RunState::Running
        } else {
            RunState::Idle
        };
        if let Some(NavRow::Session(row)) = self.rows.iter_mut().find(
            |row| matches!(row, NavRow::Session(session) if session.meta.id == loaded.meta.id),
        ) {
            row.meta = loaded.meta;
        }
        let mut replay_verbose = initial_replay_verbose(&loaded.events, self.verbose_tools);
        self.transcript = Vec::with_capacity(loaded.events.len());
        for event in &loaded.events {
            if let Some(entry) = render_event(event, replay_verbose) {
                self.transcript.push(entry);
            }
            if event.event_type == "status" {
                if let Some(verbose) = event
                    .data
                    .get("verbose")
                    .and_then(serde_json::Value::as_bool)
                {
                    replay_verbose = verbose;
                }
            }
        }
        self.event_offset = loaded.event_offset;
        self.event_total = loaded.event_total;
        self.loading_older = false;
        self.update_history_label();
        self.connection = "live".to_owned();
        self.scroll_from_bottom = 0;
        self.assistant_delta_index = None;
    }

    fn update_history_label(&mut self) {
        self.history_label = if self.event_offset > 0 {
            format!(
                "showing {}-{} of {} events · Ctrl+O loads older",
                self.event_offset + 1,
                self.event_total,
                self.event_total
            )
        } else {
            format!("{} events", self.event_total)
        };
    }

    /// Prepend an older page of replayed events ahead of the current
    /// transcript. Bottom-anchored scrolling keeps the view position stable.
    pub fn prepend_events(&mut self, events: &[EventEnvelope], page_offset: usize) {
        let mut older: Vec<TranscriptEntry> = events
            .iter()
            .filter_map(|event| render_event(event, self.verbose_tools))
            .collect();
        if let Some(index) = self.assistant_delta_index.as_mut() {
            *index += older.len();
        }
        older.append(&mut self.transcript);
        self.transcript = older;
        self.event_offset = page_offset;
        self.update_history_label();
    }

    fn clamp_queue_overlay(&mut self) {
        self.queue_overlay = match (self.queue_overlay, self.queue_items.len()) {
            (Some(_), 0) | (None, _) => None,
            (Some(selected), len) => Some(selected.min(len - 1)),
        };
    }

    pub fn recall_older_sent(&mut self) {
        let Some(history) = self
            .composer
            .session_id
            .as_ref()
            .and_then(|id| self.sent_history.get(id))
        else {
            return;
        };
        let position = match self.composer.history_pos {
            None => {
                if history.is_empty() {
                    return;
                }
                self.composer.stash = self.composer.text.clone();
                history.len() - 1
            }
            Some(position) => position.saturating_sub(1),
        };
        let text = history[position].clone();
        self.composer.set_recalled_text(text);
        self.composer.history_pos = Some(position);
    }

    pub fn recall_newer_sent(&mut self) {
        let Some(history) = self
            .composer
            .session_id
            .as_ref()
            .and_then(|id| self.sent_history.get(id))
        else {
            return;
        };
        match self.composer.history_pos {
            None => {}
            Some(position) if position + 1 < history.len() => {
                let text = history[position + 1].clone();
                self.composer.set_recalled_text(text);
                self.composer.history_pos = Some(position + 1);
            }
            Some(_) => {
                let stash = std::mem::take(&mut self.composer.stash);
                self.composer.set_recalled_text(stash);
                self.composer.history_pos = None;
            }
        }
    }

    pub fn record_sent_message(&mut self, session_id: &str, text: &str) {
        if text.trim().is_empty() {
            return;
        }
        let history = self.sent_history.entry(session_id.to_owned()).or_default();
        if history.last().map(String::as_str) != Some(text) {
            history.push(text.to_owned());
        }
    }

    pub fn apply_event(&mut self, event: &EventEnvelope) {
        if let Some(percent) = context_percent(event) {
            self.context_percent = Some(percent);
        }
        if event.event_type == "status" {
            if let Some(verbose) = event
                .data
                .get("verbose")
                .and_then(serde_json::Value::as_bool)
            {
                self.verbose_tools = verbose;
            }
        }
        match event.event_type.as_str() {
            "user_message" => self.run_state = RunState::Running,
            "approval_required" => {
                self.run_state = RunState::WaitingApproval;
                self.pending_approval = approval_from_event(event);
            }
            "approval_resolved" => {
                let resolved_id = event
                    .data
                    .get("approval_id")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("");
                if self
                    .pending_approval
                    .as_ref()
                    .is_some_and(|approval| approval.approval_id == resolved_id)
                {
                    self.pending_approval = None;
                }
                self.run_state = RunState::Running;
            }
            "queue_updated" => {
                self.queue_items = event
                    .data
                    .get("items")
                    .and_then(serde_json::Value::as_array)
                    .map_or_else(Vec::new, |items| queue_items_from_values(items));
                self.clamp_queue_overlay();
            }
            "run_finished" => {
                self.run_state = RunState::Idle;
                self.pending_approval = None;
            }
            _ => {}
        }
        if event.event_type == "assistant_delta" {
            let delta = event
                .data
                .get("text")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("");
            if delta.is_empty() {
                return;
            }
            if let Some(index) = self.assistant_delta_index {
                if let Some(entry) = self.transcript.get_mut(index) {
                    entry.text.push_str(delta);
                    self.pin_scroll_add(delta.matches('\n').count());
                    return;
                }
            }
            let entry = TranscriptEntry {
                kind: crate::events::EntryKind::Assistant,
                text: format!("Assistant\n{delta}"),
            };
            self.pin_scroll_add(entry_line_estimate(&entry));
            self.transcript.push(entry);
            self.assistant_delta_index = Some(self.transcript.len() - 1);
            return;
        }
        if event.event_type == "assistant_message" {
            if let Some(index) = self.assistant_delta_index.take() {
                if index < self.transcript.len() {
                    let removed = self.transcript.remove(index);
                    self.pin_scroll_sub(entry_line_estimate(&removed));
                }
            }
        }
        if let Some(entry) = render_event(event, self.verbose_tools) {
            self.pin_scroll_add(entry_line_estimate(&entry));
            self.transcript.push(entry);
        }
        if self.search.is_some() {
            self.recompute_search(false);
        }
    }

    /// Keep the reading position roughly stable while scrolled up: new content
    /// grows the bottom, so the bottom-anchored offset must grow with it. The
    /// estimate ignores soft wrapping, so long wrapped lines drift slightly.
    fn pin_scroll_add(&mut self, lines: usize) {
        if self.scroll_from_bottom > 0 {
            self.scroll_from_bottom = self
                .scroll_from_bottom
                .saturating_add(lines.min(u16::MAX as usize) as u16);
        }
    }

    fn pin_scroll_sub(&mut self, lines: usize) {
        if self.scroll_from_bottom > 0 {
            self.scroll_from_bottom = self
                .scroll_from_bottom
                .saturating_sub(lines.min(u16::MAX as usize) as u16);
        }
    }

    pub fn scroll_up(&mut self, amount: u16) {
        self.scroll_from_bottom = self.scroll_from_bottom.saturating_add(amount);
    }

    pub fn scroll_down(&mut self, amount: u16) {
        self.scroll_from_bottom = self.scroll_from_bottom.saturating_sub(amount);
    }

    /// Fold or unfold the selected project/task, keeping the cursor on it.
    pub fn toggle_collapsed(&mut self) {
        let key = match self.selected() {
            Some(NavRow::Project { id, .. }) => NavKey::Project(id.clone()),
            Some(NavRow::Task { project_id, id, .. }) => NavKey::Task {
                project_id: project_id.clone(),
                task_id: id.clone(),
            },
            _ => return,
        };
        if !self.collapsed.remove(&key) {
            self.collapsed.insert(key);
        }
        self.rows = flatten_tree(&self.tree, &self.collapsed);
        if self.cursor >= self.rows.len() {
            self.cursor = self.rows.len().saturating_sub(1);
        }
    }

    pub fn open_search(&mut self) {
        if self.active_session_id.is_none() {
            self.notice = Some("Open a session to search its transcript.".to_owned());
            return;
        }
        self.search = Some(SearchState::default());
    }

    pub fn close_search(&mut self) {
        self.search = None;
    }

    pub fn search_input(&mut self, character: char) {
        if let Some(search) = self.search.as_mut() {
            search.query.insert(search.cursor, character);
            search.cursor += character.len_utf8();
        }
        self.recompute_search(true);
    }

    pub fn search_backspace(&mut self) {
        if let Some(search) = self.search.as_mut() {
            if let Some(character) = search.query[..search.cursor].chars().next_back() {
                search.cursor -= character.len_utf8();
                search.query.remove(search.cursor);
            }
        }
        self.recompute_search(true);
    }

    /// Move to the previous (delta < 0, older) or next (delta > 0, newer)
    /// match, wrapping around, and scroll it into view.
    pub fn search_step(&mut self, delta: isize) {
        let (len, current) = match self.search.as_ref() {
            Some(search) if !search.matches.is_empty() => (search.matches.len(), search.current),
            _ => return,
        };
        let next = (current as isize + delta).rem_euclid(len as isize) as usize;
        let entry = self.search.as_ref().map(|search| search.matches[next]);
        if let Some(search) = self.search.as_mut() {
            search.current = next;
        }
        if let Some(entry) = entry {
            self.scroll_to_entry(entry);
        }
    }

    /// Recompute transcript matches for the active query. When `jump` is set the
    /// view scrolls to the newest match, mirroring the bottom-anchored default.
    fn recompute_search(&mut self, jump: bool) {
        let Some(query) = self
            .search
            .as_ref()
            .map(|search| search.query.to_lowercase())
        else {
            return;
        };
        let matches: Vec<usize> = if query.is_empty() {
            Vec::new()
        } else {
            self.transcript
                .iter()
                .enumerate()
                .filter_map(|(index, entry)| {
                    entry.text.to_lowercase().contains(&query).then_some(index)
                })
                .collect()
        };
        let newest = matches.len().saturating_sub(1);
        if let Some(search) = self.search.as_mut() {
            search.current = if jump {
                newest
            } else {
                search.current.min(newest)
            };
            search.matches = matches;
        }
        if jump {
            if let Some(entry) = self
                .search
                .as_ref()
                .and_then(|search| search.matches.get(search.current).copied())
            {
                self.scroll_to_entry(entry);
            }
        }
    }

    /// Bottom-anchor the view so `index` sits at the bottom of the viewport.
    /// Uses the unwrapped line estimate, so very long entries drift slightly.
    fn scroll_to_entry(&mut self, index: usize) {
        let lines_after: usize = self
            .transcript
            .iter()
            .skip(index + 1)
            .map(entry_line_estimate)
            .sum();
        self.scroll_from_bottom = lines_after.min(u16::MAX as usize) as u16;
    }

    pub fn clear_active_session(&mut self) {
        self.commands_scroll = None;
        self.search = None;
        self.queue_overlay = None;
        if let Some(id) = self.active_session_id.take() {
            self.drafts.remove(&id);
            self.sent_history.remove(&id);
        }
        self.pane_focus = PaneFocus::Navigator;
        self.composer = ComposerState::default();
        self.transcript.clear();
        self.history_label.clear();
        self.connection = "no session".to_owned();
        self.run_state = RunState::Idle;
        self.queue_items.clear();
        self.pending_approval = None;
        self.context_percent = None;
        self.event_offset = 0;
        self.event_total = 0;
        self.loading_older = false;
        self.assistant_delta_index = None;
    }
}

fn entry_line_estimate(entry: &TranscriptEntry) -> usize {
    entry.text.lines().count().max(1) + 1
}

/// Reconnect backoff: 1s, 2s, then 5s for every later attempt.
pub fn reconnect_delay_secs(attempt: u32) -> u64 {
    match attempt {
        0 | 1 => 1,
        2 => 2,
        _ => 5,
    }
}

fn effective_verbose(health: &Health, meta: Option<&SessionMeta>) -> bool {
    meta.and_then(|meta| meta.run_settings.verbose_tools)
        .unwrap_or(health.verbose)
}

/// The setting in effect at the start of a replay: /verbose is a toggle, so
/// events before the first recorded toggle ran with the opposite of its value.
fn initial_replay_verbose(events: &[EventEnvelope], fallback: bool) -> bool {
    events
        .iter()
        .find_map(|event| {
            if event.event_type != "status" {
                return None;
            }
            event
                .data
                .get("verbose")
                .and_then(serde_json::Value::as_bool)
        })
        .map(|first_toggle| !first_toggle)
        .unwrap_or(fallback)
}

fn context_percent(event: &EventEnvelope) -> Option<f64> {
    let percent = match event.event_type.as_str() {
        "run_metrics" => event.data.get("context_percent")?.as_f64(),
        "context_usage" => event
            .data
            .get("usage_str")?
            .as_str()?
            .split_once('%')?
            .0
            .trim()
            .parse()
            .ok(),
        _ => None,
    }?;
    percent.is_finite().then_some(percent)
}

fn approval_from_event(event: &EventEnvelope) -> Option<ApprovalPrompt> {
    let text = |key: &str| {
        event
            .data
            .get(key)
            .and_then(serde_json::Value::as_str)
            .unwrap_or("")
            .to_owned()
    };
    let approval_id = text("approval_id");
    if approval_id.is_empty() {
        return None;
    }
    Some(ApprovalPrompt {
        session_id: event.session_id.clone(),
        approval_id,
        tool_name: text("tool_name"),
        args_json: text("args_json"),
        diff_preview: text("diff_preview"),
        state: FormState::default(),
    })
}

fn unmatched_approval(events: &[EventEnvelope], session_id: &str) -> Option<ApprovalPrompt> {
    let mut pending = None;
    for event in events {
        match event.event_type.as_str() {
            "approval_required" => pending = approval_from_event(event),
            "approval_resolved" => {
                let resolved_id = event
                    .data
                    .get("approval_id")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("");
                if pending
                    .as_ref()
                    .is_some_and(|approval: &ApprovalPrompt| approval.approval_id == resolved_id)
                {
                    pending = None;
                }
            }
            _ => {}
        }
    }
    if let Some(approval) = pending.as_mut() {
        approval.session_id = session_id.to_owned();
    }
    pending
}

fn flatten_tree(tree: &SessionTree, collapsed: &std::collections::HashSet<NavKey>) -> Vec<NavRow> {
    let mut rows = Vec::new();
    // Keep the reserved Chats collection pinned to the bottom, like the desktop
    // app; the stable sort preserves the backend order of every other project.
    let mut ordered: Vec<&crate::api::ProjectInfo> = tree.projects.iter().collect();
    ordered.sort_by_key(|project| project.id == "__chats__");
    for project in ordered {
        rows.push(NavRow::Project {
            id: project.id.clone(),
            name: project.name.clone(),
            root: project.root.clone(),
        });
        if collapsed.contains(&NavKey::Project(project.id.clone())) {
            continue;
        }
        for task in &project.tasks {
            rows.push(NavRow::Task {
                project_id: project.id.clone(),
                id: task.id.clone(),
                name: task.name.clone(),
            });
            if collapsed.contains(&NavKey::Task {
                project_id: project.id.clone(),
                task_id: task.id.clone(),
            }) {
                continue;
            }
            for session_id in &task.sessions {
                let Some(meta) = tree.sessions.get(session_id) else {
                    continue;
                };
                rows.push(NavRow::Session(SessionRow {
                    project_name: project.name.clone(),
                    task_name: task.name.clone(),
                    meta: meta.clone(),
                }));
            }
        }
    }
    rows
}

/// Ensures every ancestor of `key` is expanded, so a specific navigation
/// target (e.g. a just-created task or an explicitly requested session) is
/// reachable in the flattened rows even if its project/task was folded.
fn expand_ancestors(
    tree: &SessionTree,
    collapsed: &mut std::collections::HashSet<NavKey>,
    key: &NavKey,
) {
    match key {
        NavKey::Project(id) => {
            collapsed.remove(&NavKey::Project(id.clone()));
        }
        NavKey::Task {
            project_id,
            task_id,
        } => {
            collapsed.remove(&NavKey::Project(project_id.clone()));
            collapsed.remove(&NavKey::Task {
                project_id: project_id.clone(),
                task_id: task_id.clone(),
            });
        }
        NavKey::Session(session_id) => {
            let ancestors = tree.projects.iter().find_map(|project| {
                project.tasks.iter().find_map(|task| {
                    task.sessions
                        .iter()
                        .any(|id| id == session_id)
                        .then(|| (project.id.clone(), task.id.clone()))
                })
            });
            if let Some((project_id, task_id)) = ancestors {
                collapsed.remove(&NavKey::Project(project_id.clone()));
                collapsed.remove(&NavKey::Task {
                    project_id,
                    task_id,
                });
            }
        }
    }
}

fn nav_key(row: &NavRow) -> NavKey {
    match row {
        NavRow::Project { id, .. } => NavKey::Project(id.clone()),
        NavRow::Task { project_id, id, .. } => NavKey::Task {
            project_id: project_id.clone(),
            task_id: id.clone(),
        },
        NavRow::Session(row) => NavKey::Session(row.meta.id.clone()),
    }
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;

    use super::*;
    use crate::api::{ProjectInfo, RunSettings, TaskInfo};

    #[test]
    fn flattens_tree_in_project_order() {
        let meta = SessionMeta {
            id: "ses_1".to_owned(),
            title: "One".to_owned(),
            ..SessionMeta::default()
        };
        let tree = SessionTree {
            projects: vec![ProjectInfo {
                name: "Project".to_owned(),
                tasks: vec![TaskInfo {
                    name: "Task".to_owned(),
                    sessions: vec![meta.id.clone()],
                    ..TaskInfo::default()
                }],
                ..ProjectInfo::default()
            }],
            sessions: HashMap::from([(meta.id.clone(), meta)]),
        };
        let rows = flatten_tree(&tree, &std::collections::HashSet::new());
        assert_eq!(rows.len(), 3);
        assert!(matches!(rows[0], NavRow::Project { .. }));
        assert!(matches!(rows[1], NavRow::Task { .. }));
        assert!(matches!(rows[2], NavRow::Session(_)));
    }

    #[test]
    fn includes_empty_projects_and_tasks_in_navigation() {
        let tree = SessionTree {
            projects: vec![ProjectInfo {
                id: "project".to_owned(),
                name: "Project".to_owned(),
                tasks: vec![TaskInfo {
                    id: "empty".to_owned(),
                    name: "Empty task".to_owned(),
                    ..TaskInfo::default()
                }],
                ..ProjectInfo::default()
            }],
            ..SessionTree::default()
        };
        let rows = flatten_tree(&tree, &std::collections::HashSet::new());
        assert_eq!(rows.len(), 2);
        assert!(matches!(rows[0], NavRow::Project { .. }));
        assert!(matches!(rows[1], NavRow::Task { .. }));
    }

    #[test]
    fn chats_project_sorts_to_the_bottom() {
        let tree = SessionTree {
            projects: vec![
                ProjectInfo {
                    id: "__chats__".to_owned(),
                    name: "Chats".to_owned(),
                    ..ProjectInfo::default()
                },
                ProjectInfo {
                    id: "work".to_owned(),
                    name: "Work".to_owned(),
                    ..ProjectInfo::default()
                },
            ],
            ..SessionTree::default()
        };
        let rows = flatten_tree(&tree, &std::collections::HashSet::new());
        assert!(
            matches!(&rows[0], NavRow::Project { id, .. } if id == "work"),
            "non-chat project stays first"
        );
        assert!(
            matches!(&rows[1], NavRow::Project { id, .. } if id == "__chats__"),
            "Chats is pinned to the bottom"
        );
    }

    #[test]
    fn collapsing_a_project_hides_its_descendants() {
        let meta = SessionMeta {
            id: "ses_1".to_owned(),
            ..SessionMeta::default()
        };
        let tree = SessionTree {
            projects: vec![ProjectInfo {
                id: "project".to_owned(),
                name: "Project".to_owned(),
                tasks: vec![TaskInfo {
                    id: "task".to_owned(),
                    name: "Task".to_owned(),
                    sessions: vec![meta.id.clone()],
                    ..TaskInfo::default()
                }],
                ..ProjectInfo::default()
            }],
            sessions: HashMap::from([(meta.id.clone(), meta)]),
        };
        let mut app = App::new("http://localhost".to_owned(), Health::default(), tree, None);
        assert_eq!(app.rows.len(), 1, "projects start folded");
        app.cursor = 0; // the project row
        app.toggle_collapsed();
        assert_eq!(app.rows.len(), 3, "expanding reveals task and session");
        app.toggle_collapsed();
        assert_eq!(app.rows.len(), 1, "task and session are folded away again");
    }

    #[test]
    fn navigator_starts_with_every_project_folded() {
        let projects = ["alpha", "beta", "__chats__"].map(|id| ProjectInfo {
            id: id.to_owned(),
            name: id.to_owned(),
            tasks: vec![TaskInfo {
                id: format!("{id}-task"),
                name: "Task".to_owned(),
                sessions: vec![format!("{id}-session")],
            }],
            ..ProjectInfo::default()
        });
        let sessions = projects
            .iter()
            .map(|project| {
                let id = format!("{}-session", project.id);
                (
                    id.clone(),
                    SessionMeta {
                        id,
                        ..SessionMeta::default()
                    },
                )
            })
            .collect();
        let tree = SessionTree {
            projects: projects.to_vec(),
            sessions,
        };
        let app = App::new("http://localhost".to_owned(), Health::default(), tree, None);
        assert_eq!(
            app.rows.len(),
            3,
            "only the three project rows are visible by default"
        );
        assert!(app
            .rows
            .iter()
            .all(|row| matches!(row, NavRow::Project { .. })));
    }

    #[test]
    fn requested_session_unfolds_only_its_own_project() {
        let make_project = |id: &str| ProjectInfo {
            id: id.to_owned(),
            name: id.to_owned(),
            tasks: vec![TaskInfo {
                id: format!("{id}-task"),
                name: "Task".to_owned(),
                sessions: vec![format!("{id}-session")],
            }],
            ..ProjectInfo::default()
        };
        let projects = vec![make_project("alpha"), make_project("beta")];
        let sessions = projects
            .iter()
            .map(|project| {
                let id = format!("{}-session", project.id);
                (
                    id.clone(),
                    SessionMeta {
                        id,
                        ..SessionMeta::default()
                    },
                )
            })
            .collect();
        let tree = SessionTree { projects, sessions };
        let app = App::new(
            "http://localhost".to_owned(),
            Health::default(),
            tree,
            Some("beta-session"),
        );
        assert_eq!(
            app.active_session_id.as_deref(),
            Some("beta-session"),
            "the requested session is selected"
        );
        assert!(
            app.rows
                .iter()
                .any(|row| matches!(row, NavRow::Session(s) if s.meta.id == "beta-session")),
            "its project unfolds so the session is visible"
        );
        assert!(
            !app
                .rows
                .iter()
                .any(|row| matches!(row, NavRow::Session(s) if s.meta.id == "alpha-session")),
            "unrelated projects stay folded"
        );
    }

    #[test]
    fn transcript_search_matches_step_and_clear() {
        let mut app = empty_app();
        app.active_session_id = Some("ses_1".to_owned());
        app.transcript = vec![
            TranscriptEntry {
                kind: crate::events::EntryKind::User,
                text: "hello world".to_owned(),
            },
            TranscriptEntry {
                kind: crate::events::EntryKind::Assistant,
                text: "goodbye".to_owned(),
            },
            TranscriptEntry {
                kind: crate::events::EntryKind::Assistant,
                text: "hello again".to_owned(),
            },
        ];
        app.open_search();
        for character in "HELLO".chars() {
            app.search_input(character);
        }
        let search = app.search.as_ref().unwrap();
        assert_eq!(search.matches, vec![0, 2], "case-insensitive match");
        assert_eq!(search.current, 1, "newest match highlighted first");
        app.search_step(-1);
        assert_eq!(
            app.search.as_ref().unwrap().current,
            0,
            "steps to older match"
        );
        app.search_step(-1);
        assert_eq!(app.search.as_ref().unwrap().current, 1, "wraps around");
        app.close_search();
        assert!(app.search.is_none());
    }

    #[test]
    fn provider_choices_follow_backend_health() {
        let mut app = App::new(
            "http://localhost".to_owned(),
            Health {
                native_enabled: true,
                ..Health::default()
            },
            SessionTree::default(),
            None,
        );
        assert_eq!(app.available_providers(), vec![Provider::Native]);
        app.health.codex_app_server_enabled = true;
        assert_eq!(
            app.available_providers(),
            vec![Provider::Native, Provider::Codex]
        );
    }

    #[test]
    fn replacing_tree_selects_new_task() {
        let initial = SessionTree {
            projects: vec![ProjectInfo {
                id: "project".to_owned(),
                name: "Project".to_owned(),
                ..ProjectInfo::default()
            }],
            ..SessionTree::default()
        };
        let mut app = App::new(
            "http://localhost".to_owned(),
            Health::default(),
            initial,
            None,
        );
        let refreshed = SessionTree {
            projects: vec![ProjectInfo {
                id: "project".to_owned(),
                name: "Project".to_owned(),
                tasks: vec![TaskInfo {
                    id: "new_task".to_owned(),
                    name: "New task".to_owned(),
                    ..TaskInfo::default()
                }],
                ..ProjectInfo::default()
            }],
            ..SessionTree::default()
        };
        app.replace_tree(
            refreshed,
            Some(NavKey::Task {
                project_id: "project".to_owned(),
                task_id: "new_task".to_owned(),
            }),
        );
        assert!(matches!(
            app.selected(),
            Some(NavRow::Task { id, .. }) if id == "new_task"
        ));
    }

    fn empty_app() -> App {
        App::new(
            "http://localhost".to_owned(),
            Health::default(),
            SessionTree::default(),
            None,
        )
    }

    fn approval_event(event_type: &str, approval_id: &str) -> EventEnvelope {
        EventEnvelope {
            session_id: "ses_1".to_owned(),
            event_type: event_type.to_owned(),
            data: serde_json::json!({
                "approval_id": approval_id,
                "tool_name": "shell",
                "args_json": "{}"
            }),
            ..EventEnvelope::default()
        }
    }

    #[test]
    fn idle_loaded_session_ignores_stale_unresolved_approval() {
        let mut app = empty_app();
        app.apply_loaded(SessionLoaded {
            meta: SessionMeta {
                id: "ses_1".to_owned(),
                status: "idle".to_owned(),
                ..SessionMeta::default()
            },
            events: vec![approval_event("approval_required", "apr_1")],
            ..SessionLoaded::default()
        });
        assert_eq!(app.run_state, RunState::Idle);
        assert!(app.pending_approval.is_none());
    }

    #[test]
    fn running_loaded_session_restores_only_unresolved_approval() {
        let mut app = empty_app();
        app.apply_loaded(SessionLoaded {
            meta: SessionMeta {
                id: "ses_1".to_owned(),
                status: "running".to_owned(),
                ..SessionMeta::default()
            },
            events: vec![approval_event("approval_required", "apr_1")],
            ..SessionLoaded::default()
        });
        assert_eq!(app.run_state, RunState::WaitingApproval);
        assert_eq!(
            app.pending_approval
                .as_ref()
                .map(|item| item.approval_id.as_str()),
            Some("apr_1")
        );
        app.apply_event(&approval_event("approval_resolved", "apr_1"));
        assert!(app.pending_approval.is_none());
        assert_eq!(app.run_state, RunState::Running);
    }

    #[test]
    fn live_queue_and_run_finished_update_state() {
        let mut app = empty_app();
        app.apply_event(&EventEnvelope {
            event_type: "queue_updated".to_owned(),
            data: serde_json::json!({"items": [{"id": "one", "text": "first"}, {"id": "two", "text": "second"}]}),
            ..EventEnvelope::default()
        });
        assert_eq!(app.queue_items.len(), 2);
        assert_eq!(app.queue_items[0].id, "one");
        assert_eq!(app.queue_items[1].text, "second");
        app.run_state = RunState::Cancelling;
        app.apply_event(&EventEnvelope {
            event_type: "run_finished".to_owned(),
            data: serde_json::json!({"reason": "interrupted"}),
            ..EventEnvelope::default()
        });
        assert_eq!(app.run_state, RunState::Idle);
        assert!(app.pending_approval.is_none());
    }

    #[test]
    fn loaded_session_uses_verbose_override_when_rendering_diagnostics() {
        let mut app = App::new(
            "http://localhost".to_owned(),
            Health {
                verbose: true,
                ..Health::default()
            },
            SessionTree::default(),
            None,
        );
        app.apply_loaded(SessionLoaded {
            meta: SessionMeta {
                id: "ses_1".to_owned(),
                run_settings: RunSettings {
                    verbose_tools: Some(false),
                },
                ..SessionMeta::default()
            },
            events: vec![
                EventEnvelope {
                    event_type: "api_metrics".to_owned(),
                    data: serde_json::json!({"completion_tokens": 159}),
                    ..EventEnvelope::default()
                },
                EventEnvelope {
                    event_type: "context_usage".to_owned(),
                    data: serde_json::json!({"usage_str": "0.8% context"}),
                    ..EventEnvelope::default()
                },
            ],
            ..SessionLoaded::default()
        });

        assert!(!app.verbose_tools);
        assert!(app.transcript.is_empty());
        assert_eq!(app.context_percent, Some(0.8));
    }

    #[test]
    fn live_verbose_status_controls_later_diagnostics() {
        let mut app = empty_app();
        let metrics = EventEnvelope {
            event_type: "api_metrics".to_owned(),
            data: serde_json::json!({"completion_tokens": 159}),
            ..EventEnvelope::default()
        };
        app.apply_event(&metrics);
        assert!(app.transcript.is_empty());

        app.apply_event(&EventEnvelope {
            event_type: "status".to_owned(),
            data: serde_json::json!({"text": "Verbose tools enabled", "verbose": true}),
            ..EventEnvelope::default()
        });
        app.apply_event(&metrics);
        assert!(app.verbose_tools);
        assert!(app
            .transcript
            .iter()
            .any(|entry| entry.text.contains("api_metrics")));

        app.apply_event(&EventEnvelope {
            event_type: "status".to_owned(),
            data: serde_json::json!({"text": "Verbose tools disabled", "verbose": false}),
            ..EventEnvelope::default()
        });
        let transcript_len = app.transcript.len();
        app.apply_event(&EventEnvelope {
            event_type: "context_usage".to_owned(),
            data: serde_json::json!({"usage_str": "0.8% context"}),
            ..EventEnvelope::default()
        });
        assert!(!app.verbose_tools);
        assert_eq!(app.transcript.len(), transcript_len);
        assert_eq!(app.context_percent, Some(0.8));
    }

    #[test]
    fn replay_restores_latest_context_percent() {
        let mut app = empty_app();
        app.apply_loaded(SessionLoaded {
            meta: SessionMeta {
                id: "ses_1".to_owned(),
                ..SessionMeta::default()
            },
            events: vec![
                EventEnvelope {
                    event_type: "context_usage".to_owned(),
                    data: serde_json::json!({"usage_str": "0.4% context"}),
                    ..EventEnvelope::default()
                },
                EventEnvelope {
                    event_type: "run_metrics".to_owned(),
                    data: serde_json::json!({"context_percent": 0.6}),
                    ..EventEnvelope::default()
                },
            ],
            ..SessionLoaded::default()
        });

        assert_eq!(app.context_percent, Some(0.6));
        assert!(app.transcript.is_empty());
    }

    #[test]
    fn clearing_active_session_removes_transient_run_state() {
        let mut app = empty_app();
        app.active_session_id = Some("ses_1".to_owned());
        app.run_state = RunState::Running;
        app.queue_items = vec![QueueItem::default(), QueueItem::default()];
        app.context_percent = Some(0.6);
        app.transcript.push(TranscriptEntry {
            kind: crate::events::EntryKind::Status,
            text: "work".to_owned(),
        });
        app.clear_active_session();
        assert!(app.active_session_id.is_none());
        assert!(app.transcript.is_empty());
        assert_eq!(app.run_state, RunState::Idle);
        assert!(app.queue_items.is_empty());
        assert_eq!(app.context_percent, None);
        assert_eq!(app.pane_focus, PaneFocus::Navigator);
        assert!(app.composer.session_id.is_none());
    }

    #[test]
    fn opening_session_focuses_inline_composer() {
        let mut app = empty_app();
        app.composer.text = "old draft".to_owned();
        app.begin_connection("ses_2".to_owned());
        assert_eq!(app.pane_focus, PaneFocus::Conversation);
        assert_eq!(app.composer.session_id.as_deref(), Some("ses_2"));
        assert!(app.composer.text.is_empty());
    }

    #[test]
    fn composer_edits_at_cursor_across_multibyte_text() {
        let mut composer = ComposerState::default();
        composer.insert_str("héllo");
        assert_eq!(composer.cursor, "héllo".len());
        composer.move_left();
        composer.move_left();
        composer.insert_char('X');
        assert_eq!(composer.text, "hélXlo");
        composer.backspace();
        assert_eq!(composer.text, "héllo");
        composer.move_left();
        composer.delete_forward();
        assert_eq!(composer.text, "hélo");
        composer.move_right();
        composer.insert_char('\n');
        assert_eq!(composer.text, "hél\no");
        composer.clear_text();
        assert!(composer.text.is_empty());
        assert_eq!(composer.cursor, 0);
    }

    #[test]
    fn composer_edits_german_umlauts_and_eszett() {
        let mut composer = ComposerState::default();
        composer.insert_str("Größe: über Straße");
        assert_eq!(composer.cursor, "Größe: über Straße".len());
        // Walk left until the cursor sits right after the "S" in "Straße".
        while composer.text[..composer.cursor] != *"Größe: über S" {
            composer.move_left();
        }
        composer.backspace();
        assert_eq!(composer.text, "Größe: über traße");
        composer.insert_char('S');
        assert_eq!(composer.text, "Größe: über Straße");
        composer.insert_char('ẞ');
        assert_eq!(composer.text, "Größe: über Sẞtraße");
        composer.delete_forward();
        assert_eq!(composer.text, "Größe: über Sẞraße");
    }

    #[test]
    fn composer_supports_readline_line_and_word_editing() {
        let mut composer = ComposerState::default();
        composer.insert_str("first line\nsecond word");

        composer.move_line_start();
        assert_eq!(composer.cursor, "first line\n".len());
        composer.move_line_end();
        assert_eq!(composer.cursor, composer.text.len());

        composer.delete_word_back();
        assert_eq!(composer.text, "first line\nsecond ");
        composer.delete_word_back();
        assert_eq!(composer.text, "first line\n");

        composer.insert_str("tail");
        composer.move_line_start();
        composer.delete_to_line_end();
        assert_eq!(composer.text, "first line\n");
        // At the end of a line the kill swallows the newline instead.
        composer.move_left();
        composer.delete_to_line_end();
        assert_eq!(composer.text, "first line");
    }

    #[test]
    fn replacing_tree_clears_state_when_active_session_disappears() {
        let mut app = empty_app();
        app.active_session_id = Some("ses_gone".to_owned());
        app.composer.session_id = Some("ses_gone".to_owned());
        app.pane_focus = PaneFocus::Conversation;
        app.run_state = RunState::Running;

        let removed = app.replace_tree(SessionTree::default(), None);

        assert!(removed);
        assert!(app.active_session_id.is_none());
        assert!(app.composer.session_id.is_none());
        assert_eq!(app.pane_focus, PaneFocus::Navigator);
        assert_eq!(app.run_state, RunState::Idle);
    }

    #[test]
    fn health_refresh_updates_open_session_form_providers() {
        let mut app = empty_app();
        app.modal = Some(Modal::Session(SessionForm {
            project_id: "project".to_owned(),
            task_id: "task".to_owned(),
            project_name: "Project".to_owned(),
            task_name: "Task".to_owned(),
            title: String::new(),
            providers: vec![Provider::Native],
            provider_index: 0,
            focus: FormFocus::First,
            state: FormState::default(),
        }));
        app.update_health(Health {
            native_enabled: true,
            codex_app_server_enabled: true,
            ..Health::default()
        });
        let Some(Modal::Session(form)) = app.modal.as_ref() else {
            panic!("session form should stay open");
        };
        assert_eq!(form.providers, vec![Provider::Native, Provider::Codex]);
        assert_eq!(form.provider_index, 0);
    }

    #[test]
    fn draft_cursor_moves_between_lines_preserving_column() {
        let mut composer = ComposerState::default();
        composer.insert_str("short\nlonger line\nmid");
        assert!(!composer.cursor_down(), "cursor starts on the last line");
        assert!(composer.cursor_up());
        assert_eq!(&composer.text[..composer.cursor], "short\nlon");
        assert!(composer.cursor_up());
        assert_eq!(&composer.text[..composer.cursor], "sho");
        assert!(!composer.cursor_up(), "first line reached");
        assert!(composer.cursor_down());
        assert_eq!(&composer.text[..composer.cursor], "short\nlon");
    }

    #[test]
    fn switching_sessions_preserves_per_session_drafts() {
        let mut app = empty_app();
        app.begin_connection("ses_1".to_owned());
        app.composer.insert_str("draft one");
        app.begin_connection("ses_2".to_owned());
        assert!(app.composer.text.is_empty());
        app.composer.insert_str("draft two");
        app.begin_connection("ses_1".to_owned());
        assert_eq!(app.composer.text, "draft one");
        assert_eq!(app.composer.cursor, "draft one".len());
        app.begin_connection("ses_2".to_owned());
        assert_eq!(app.composer.text, "draft two");
    }

    #[test]
    fn sent_history_recall_cycles_and_restores_stash() {
        let mut app = empty_app();
        app.begin_connection("ses_1".to_owned());
        app.record_sent_message("ses_1", "first");
        app.record_sent_message("ses_1", "second");
        app.composer.insert_str("in progress");

        app.recall_older_sent();
        assert_eq!(app.composer.text, "second");
        app.recall_older_sent();
        assert_eq!(app.composer.text, "first");
        app.recall_older_sent();
        assert_eq!(app.composer.text, "first", "clamped at oldest");
        app.recall_newer_sent();
        assert_eq!(app.composer.text, "second");
        app.recall_newer_sent();
        assert_eq!(app.composer.text, "in progress", "stash restored");
        assert!(app.composer.history_pos.is_none());
    }

    #[test]
    fn editing_resets_history_navigation() {
        let mut app = empty_app();
        app.begin_connection("ses_1".to_owned());
        app.record_sent_message("ses_1", "first");
        app.recall_older_sent();
        assert_eq!(app.composer.history_pos, Some(0));
        app.composer.insert_char('!');
        assert!(app.composer.history_pos.is_none());
    }

    #[test]
    fn prepending_older_events_shifts_delta_index_and_offset() {
        let mut app = empty_app();
        app.apply_loaded(SessionLoaded {
            meta: SessionMeta {
                id: "ses_1".to_owned(),
                status: "running".to_owned(),
                ..SessionMeta::default()
            },
            events: vec![],
            event_offset: 200,
            event_total: 260,
        });
        app.apply_event(&EventEnvelope {
            event_type: "assistant_delta".to_owned(),
            data: serde_json::json!({"text": "streaming"}),
            ..EventEnvelope::default()
        });
        assert_eq!(app.transcript.len(), 1);

        let older = vec![
            EventEnvelope {
                event_type: "user_message".to_owned(),
                data: serde_json::json!({"text": "earlier question"}),
                ..EventEnvelope::default()
            },
            EventEnvelope {
                event_type: "assistant_message".to_owned(),
                data: serde_json::json!({"markdown": "earlier answer"}),
                ..EventEnvelope::default()
            },
        ];
        app.prepend_events(&older, 0);

        assert_eq!(app.transcript.len(), 3);
        assert!(app.transcript[0].text.contains("earlier question"));
        assert_eq!(app.event_offset, 0);
        assert!(app.history_label.starts_with("260 events"));
        // The streamed entry moved to index 2; further deltas must extend it.
        app.apply_event(&EventEnvelope {
            event_type: "assistant_delta".to_owned(),
            data: serde_json::json!({"text": " more"}),
            ..EventEnvelope::default()
        });
        assert_eq!(app.transcript.len(), 3);
        assert!(app.transcript[2].text.contains("streaming more"));
    }

    #[test]
    fn scroll_position_pins_while_reading_older_content() {
        let mut app = empty_app();
        let status = |text: &str| EventEnvelope {
            event_type: "status".to_owned(),
            data: serde_json::json!({ "text": text }),
            ..EventEnvelope::default()
        };
        app.apply_event(&status("one"));
        app.scroll_up(4);
        app.apply_event(&status("two"));
        assert_eq!(app.scroll_from_bottom, 6, "offset grew by the new entry");
        app.scroll_down(u16::MAX);
        app.apply_event(&status("three"));
        assert_eq!(
            app.scroll_from_bottom, 0,
            "at bottom the view keeps following"
        );
    }

    #[test]
    fn queue_overlay_selection_clamps_to_shrinking_queue() {
        let mut app = empty_app();
        app.queue_items = queue_items_from_values(&[
            serde_json::json!({"id": "a", "text": "one"}),
            serde_json::json!({"id": "b", "text": "two"}),
        ]);
        app.queue_overlay = Some(1);
        app.apply_event(&EventEnvelope {
            event_type: "queue_updated".to_owned(),
            data: serde_json::json!({"items": [{"id": "b", "text": "two"}]}),
            ..EventEnvelope::default()
        });
        assert_eq!(app.queue_overlay, Some(0));
        app.apply_event(&EventEnvelope {
            event_type: "queue_updated".to_owned(),
            data: serde_json::json!({"items": []}),
            ..EventEnvelope::default()
        });
        assert_eq!(app.queue_overlay, None);
    }

    #[test]
    fn reconnect_backoff_caps_at_five_seconds() {
        assert_eq!(reconnect_delay_secs(1), 1);
        assert_eq!(reconnect_delay_secs(2), 2);
        assert_eq!(reconnect_delay_secs(3), 5);
        assert_eq!(reconnect_delay_secs(9), 5);
    }

    #[test]
    fn replay_respects_mid_history_verbose_toggles() {
        let mut app = empty_app();
        let metrics = |label: &str| EventEnvelope {
            event_type: "api_metrics".to_owned(),
            data: serde_json::json!({ "label": label }),
            ..EventEnvelope::default()
        };
        app.apply_loaded(SessionLoaded {
            meta: SessionMeta {
                id: "ses_1".to_owned(),
                run_settings: RunSettings {
                    verbose_tools: Some(true),
                },
                ..SessionMeta::default()
            },
            events: vec![
                metrics("before"),
                EventEnvelope {
                    event_type: "status".to_owned(),
                    data: serde_json::json!({"text": "Verbose tools enabled", "verbose": true}),
                    ..EventEnvelope::default()
                },
                metrics("after"),
            ],
            ..SessionLoaded::default()
        });

        assert!(!app
            .transcript
            .iter()
            .any(|entry| entry.text.contains("before")));
        assert!(app
            .transcript
            .iter()
            .any(|entry| entry.text.contains("after")));
        assert!(app.verbose_tools);
    }
}

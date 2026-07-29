use std::ops::Range;

use ratatui::{
    layout::{Alignment, Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span, Text},
    widgets::{Block, Borders, Clear, List, ListItem, ListState, Paragraph, Wrap},
    Frame,
};

use crate::{
    app::{
        App, DeleteTarget, FormFocus, Modal, NavKey, NavRow, PaneFocus, SearchState, SessionRow,
    },
    events::{EntryKind, TranscriptEntry},
};

const FOCUSED_COMPOSER_HINT: &str =
    "/commands opens command pane · Enter sends · Shift+Enter newline · Ctrl+C cancels run · Esc returns to navigator";

/// Rows of draft text the inline composer may grow to before it scrolls.
const COMPOSER_MAX_ROWS: u16 = 10;
/// Width of the per-row gutter (`> ` on the first row, blanks after it).
const COMPOSER_GUTTER: u16 = 2;

pub fn draw(frame: &mut Frame, app: &App) {
    let outer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(5),
            Constraint::Length(2),
        ])
        .split(frame.area());

    draw_header(frame, outer[0], app);
    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(32), Constraint::Percentage(68)])
        .split(outer[1]);
    draw_sessions(frame, body[0], app);
    draw_conversation(frame, body[1], app);
    draw_footer(frame, outer[2], app);
    if let Some(modal) = &app.modal {
        draw_modal(frame, modal);
    }
    if let Some(selected) = app.queue_overlay {
        draw_queue_pane(frame, app, selected);
    }
    if let Some(approval) = &app.pending_approval {
        draw_approval(frame, approval);
    }
    if let Some(scroll) = app.commands_scroll {
        draw_commands_pane(frame, scroll);
    }
}

fn draw_queue_pane(frame: &mut Frame, app: &App, selected: usize) {
    let area = centered_rect(72, 60, frame.area());
    frame.render_widget(Clear, area);
    let mut lines = vec![
        Line::styled(
            format!(
                "{} queued message(s), first in first out",
                app.queue_items.len()
            ),
            Style::default().fg(Color::DarkGray),
        ),
        Line::default(),
    ];
    for (index, item) in app.queue_items.iter().enumerate() {
        let marker = if index == selected { "> " } else { "  " };
        let preview: String = item.text.replace('\n', " ⏎ ").chars().take(120).collect();
        let style = if index == selected {
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD)
        } else {
            Style::default()
        };
        lines.push(Line::styled(
            format!("{marker}{}. {preview}", index + 1),
            style,
        ));
    }
    lines.push(Line::default());
    lines.push(Line::styled(
        "j/k select · K/J move up/down · d delete · Esc close",
        Style::default().fg(Color::DarkGray),
    ));
    frame.render_widget(
        Paragraph::new(lines)
            .block(
                Block::default()
                    .title(" Message queue ")
                    .borders(Borders::ALL)
                    .border_style(Style::default().fg(Color::Cyan)),
            )
            .wrap(Wrap { trim: false }),
        area,
    );
}

fn draw_header(frame: &mut Frame, area: Rect, app: &App) {
    let app_name = if app.health.app_name.is_empty() {
        "MyHarness"
    } else {
        app.health.app_name.as_str()
    };
    let line = Line::from(vec![
        Span::styled(app_name.to_owned(), Style::default().add_modifier(Modifier::BOLD)),
        Span::raw(format!(" · {}  ", app.backend_url)),
        Span::raw(format!(
            "approval: {}",
            value_or(&app.health.approval_mode, "unknown")
        )),
    ]);
    frame.render_widget(
        Paragraph::new(line).block(Block::default().borders(Borders::ALL)),
        area,
    );
}

fn draw_sessions(frame: &mut Frame, area: Rect, app: &App) {
    let items: Vec<ListItem> = app
        .rows
        .iter()
        .map(|row| {
            ListItem::new(nav_line(
                row,
                app.active_session_id.as_deref(),
                &app.collapsed,
            ))
        })
        .collect();
    let title = format!(
        " Projects ({}) · Items ({}) ",
        app.tree.projects.len(),
        app.rows.len()
    );
    let list = List::new(items)
        .block(
            Block::default()
                .title(title)
                .borders(Borders::ALL)
                .border_style(pane_border(app.pane_focus == PaneFocus::Navigator)),
        )
        .highlight_style(
            Style::default()
                .bg(Color::DarkGray)
                .add_modifier(Modifier::BOLD),
        )
        .highlight_symbol("› ");
    let mut state =
        ListState::default().with_selected((!app.rows.is_empty()).then_some(app.cursor));
    frame.render_stateful_widget(list, area, &mut state);
}

fn draw_conversation(frame: &mut Frame, area: Rect, app: &App) {
    if app.active_session_id.is_none() {
        draw_transcript(frame, area, app);
        return;
    }
    let rows = wrap_rows(&app.composer.text, composer_text_width(area.width));
    // The transcript keeps at least its 5-row minimum; the composer grows into
    // whatever is left, up to COMPOSER_MAX_ROWS, and scrolls beyond that.
    let room = area.height.saturating_sub(8).clamp(1, COMPOSER_MAX_ROWS);
    let draft_rows = (rows.len().min(u16::MAX as usize) as u16).clamp(1, room);
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Min(5),
            Constraint::Length(draft_rows + 2),
            Constraint::Length(1),
        ])
        .split(area);
    draw_transcript(frame, chunks[0], app);
    draw_inline_composer(frame, chunks[1], app, &rows);
    if let Some(search) = &app.search {
        draw_search_bar(frame, chunks[2], search);
    } else {
        draw_composer_status(frame, chunks[2], app);
    }
}

/// Columns available to draft text inside the composer block.
pub(crate) fn composer_text_width(area_width: u16) -> usize {
    area_width.saturating_sub(2 + COMPOSER_GUTTER).max(1) as usize
}

/// Soft-wrap `text` into display rows of at most `width` columns, returning the
/// byte range each row covers. Rows break after the last space that fits, and
/// fall back to a hard break for words longer than the composer is wide.
pub(crate) fn wrap_rows(text: &str, width: usize) -> Vec<Range<usize>> {
    let width = width.max(1);
    let mut rows = Vec::new();
    let mut offset = 0;
    for line in text.split('\n') {
        wrap_line(text, offset, offset + line.len(), width, &mut rows);
        offset += line.len() + 1;
    }
    rows
}

fn wrap_line(text: &str, start: usize, end: usize, width: usize, rows: &mut Vec<Range<usize>>) {
    let mut cursor = start;
    loop {
        let mut hard_break = None;
        let mut last_space = None;
        for (column, (index, character)) in text[cursor..end].char_indices().enumerate() {
            if column == width {
                hard_break = Some(cursor + index);
                break;
            }
            if index > 0 && character == ' ' {
                last_space = Some(cursor + index);
            }
        }
        let Some(hard_break) = hard_break else {
            rows.push(cursor..end);
            return;
        };
        // Breaking after the space keeps it on the row it terminates, which is
        // what a trailing-space-preserving wrap looks like in other composers.
        let split = match last_space {
            Some(space) if space + 1 > cursor && space < hard_break => space + 1,
            _ => hard_break,
        };
        rows.push(cursor..split);
        cursor = split;
        if cursor >= end {
            rows.push(end..end);
            return;
        }
    }
}

fn rows_column(text: &str, row_start: usize, offset: usize) -> usize {
    text[row_start..offset].chars().count()
}

/// Locate a byte cursor within wrapped rows as a (row, column) pair.
pub(crate) fn composer_cursor(text: &str, rows: &[Range<usize>], cursor: usize) -> (usize, usize) {
    let index = rows
        .iter()
        .rposition(|row| row.start <= cursor)
        .unwrap_or(0);
    let Some(row) = rows.get(index) else {
        return (0, 0);
    };
    (index, rows_column(text, row.start, cursor.min(row.end)))
}

fn draw_inline_composer(frame: &mut Frame, area: Rect, app: &App, rows: &[Range<usize>]) {
    let focused = app.pane_focus == PaneFocus::Conversation;
    let text = &app.composer.text;
    let lines: Vec<Line> = rows
        .iter()
        .enumerate()
        .map(|(index, row)| {
            Line::from(vec![
                Span::styled(
                    if index == 0 && focused { "> " } else { "  " },
                    Style::default().fg(Color::Cyan),
                ),
                Span::raw(text[row.clone()].to_owned()),
            ])
        })
        .collect();
    let viewport = area.height.saturating_sub(2).max(1);
    let (cursor_row, cursor_column) = composer_cursor(text, rows, app.composer.cursor);
    let scroll = composer_scroll(cursor_row, rows.len(), viewport);
    let title = if app.composer.images.is_empty() {
        " Message ".to_owned()
    } else {
        format!(
            " Message ({} image{} attached, Backspace on empty line to remove) ",
            app.composer.images.len(),
            if app.composer.images.len() == 1 { "" } else { "s" }
        )
    };
    frame.render_widget(
        Paragraph::new(lines)
            .block(
                Block::default()
                    .title(title)
                    .borders(Borders::ALL)
                    .border_style(pane_border(focused)),
            )
            .scroll((scroll, 0)),
        area,
    );
    if focused && !app.composer.state.submitting {
        // A real terminal cursor keeps copying the draft free of a placeholder
        // glyph and lets the terminal blink it where the next character lands.
        let column =
            (cursor_column.min(u16::MAX as usize) as u16).saturating_add(1 + COMPOSER_GUTTER);
        let row = (cursor_row.min(u16::MAX as usize) as u16).saturating_sub(scroll);
        frame.set_cursor_position((
            (area.x + column).min(area.x + area.width.saturating_sub(1)),
            (area.y + 1 + row).min(area.y + area.height.saturating_sub(1)),
        ));
    }
}

/// Keep the cursor row inside the composer viewport, preferring to show the
/// tail of the draft once it is taller than the visible area.
pub(crate) fn composer_scroll(cursor_row: usize, total_rows: usize, viewport: u16) -> u16 {
    let viewport = viewport.max(1) as usize;
    if total_rows <= viewport {
        return 0;
    }
    let max_scroll = total_rows - viewport;
    (cursor_row + 1).saturating_sub(viewport).min(max_scroll) as u16
}

fn draw_composer_status(frame: &mut Frame, area: Rect, app: &App) {
    let focused = app.pane_focus == PaneFocus::Conversation;
    let status = if app.composer.state.submitting {
        "Sending…"
    } else if let Some(error) = &app.composer.state.error {
        error
    } else if focused {
        FOCUSED_COMPOSER_HINT
    } else {
        "Tab or i focuses the conversation"
    };
    let status_color = if app.composer.state.error.is_some() {
        Color::Red
    } else {
        Color::DarkGray
    };
    frame.render_widget(
        Paragraph::new(Line::styled(
            format!(" {status}"),
            Style::default().fg(status_color),
        )),
        area,
    );
}

fn nav_line<'a>(
    row: &'a NavRow,
    active_id: Option<&str>,
    collapsed: &std::collections::HashSet<NavKey>,
) -> Line<'a> {
    match row {
        NavRow::Project { id, name, root } => {
            let folded = collapsed.contains(&NavKey::Project(id.clone()));
            Line::from(vec![
                Span::styled(fold_marker(folded), Style::default().fg(Color::Yellow)),
                Span::styled(name, Style::default().add_modifier(Modifier::BOLD)),
                Span::styled(format!("  {root}"), Style::default().fg(Color::DarkGray)),
            ])
        }
        NavRow::Task {
            project_id,
            id,
            name,
        } => {
            let folded = collapsed.contains(&NavKey::Task {
                project_id: project_id.clone(),
                task_id: id.clone(),
            });
            Line::from(vec![
                Span::raw("  "),
                Span::styled(fold_marker(folded), Style::default().fg(Color::Yellow)),
                Span::styled(name, Style::default().add_modifier(Modifier::BOLD)),
            ])
        }
        NavRow::Session(session) => session_line(session, active_id),
    }
}

/// Folder indicator: a down triangle when expanded, a chevron when collapsed.
/// Both render on Windows consoles (the collapsed chevron avoids the small
/// right triangle that was tofu in the composer).
fn fold_marker(folded: bool) -> &'static str {
    if folded {
        "> "
    } else {
        "▾ "
    }
}

fn session_line<'a>(row: &'a SessionRow, active_id: Option<&str>) -> Line<'a> {
    let active = if active_id == Some(row.meta.id.as_str()) {
        "*"
    } else {
        " "
    };
    let provider = match row.meta.provider.as_str() {
        "codex-app-server" => "cdx",
        "claude-agent" => "cld",
        _ => "nat",
    };
    let title = if row.meta.title.is_empty() {
        row.meta.id.as_str()
    } else {
        row.meta.title.as_str()
    };
    Line::from(vec![
        Span::styled(
            format!("{active} [{provider}] "),
            Style::default().fg(Color::Cyan),
        ),
        Span::raw(title),
        Span::styled(
            format!("\n  {}/{}", row.project_name, row.task_name),
            Style::default().fg(Color::DarkGray),
        ),
    ])
}

fn draw_transcript(frame: &mut Frame, area: Rect, app: &App) {
    let lines: Vec<Line> = if app.transcript.is_empty() {
        vec![Line::styled(
            match app.active_session_id {
                Some(_) => "Waiting for session events…",
                None => "No sessions available.",
            },
            Style::default().fg(Color::DarkGray),
        )]
    } else {
        let current_match = app
            .search
            .as_ref()
            .and_then(|search| search.matches.get(search.current).copied());
        app.transcript
            .iter()
            .enumerate()
            .flat_map(|(index, entry)| {
                let mut lines = entry_lines(entry);
                let modifier = if current_match == Some(index) {
                    Some(Modifier::REVERSED)
                } else if app
                    .search
                    .as_ref()
                    .is_some_and(|search| search.matches.binary_search(&index).is_ok())
                {
                    Some(Modifier::UNDERLINED)
                } else {
                    None
                };
                if let Some(modifier) = modifier {
                    for line in &mut lines {
                        for span in &mut line.spans {
                            span.style = span.style.add_modifier(modifier);
                        }
                    }
                }
                lines
            })
            .collect()
    };
    let viewport = area.height.saturating_sub(2);
    let inner_width = area.width.saturating_sub(2).max(1);
    let paragraph = Paragraph::new(Text::from(lines)).wrap(Wrap { trim: false });
    let visual_line_count = paragraph.line_count(inner_width).min(u16::MAX as usize) as u16;
    let bottom = visual_line_count.saturating_sub(viewport);
    let scroll = bottom.saturating_sub(app.scroll_from_bottom.min(bottom));
    let active = app.active();
    let title = active
        .map(|row| {
            format!(
                " {} · {} · {} · {} · queue {} ",
                row.meta.title,
                row.meta.provider,
                app.connection,
                app.run_state.label(),
                app.queue_items.len()
            )
        })
        .unwrap_or_else(|| " Transcript ".to_owned());
    frame.render_widget(
        paragraph
            .block(
                Block::default()
                    .title(title)
                    .borders(Borders::ALL)
                    .border_style(pane_border(app.pane_focus == PaneFocus::Conversation)),
            )
            .scroll((scroll, 0)),
        area,
    );
}

fn entry_lines(entry: &TranscriptEntry) -> Vec<Line<'static>> {
    let alignment = if entry.kind == EntryKind::User {
        Alignment::Right
    } else {
        Alignment::Left
    };
    let style = match entry.kind {
        EntryKind::User => Style::default()
            .fg(Color::Cyan)
            .add_modifier(Modifier::BOLD),
        EntryKind::Assistant => Style::default().fg(Color::White),
        EntryKind::Thinking => Style::default()
            .fg(Color::DarkGray)
            .add_modifier(Modifier::ITALIC),
        EntryKind::Tool => Style::default().fg(Color::Yellow),
        EntryKind::Error => Style::default().fg(Color::Red).add_modifier(Modifier::BOLD),
        EntryKind::Status => Style::default().fg(Color::Green),
        EntryKind::Muted => Style::default().fg(Color::DarkGray),
    };
    let mut lines: Vec<Line<'static>> = entry
        .text
        .lines()
        .map(|line| Line::styled(line.to_owned(), style).alignment(alignment))
        .collect();
    lines.push(Line::default());
    lines
}

fn draw_search_bar(frame: &mut Frame, area: Rect, search: &SearchState) {
    let summary = if search.query.is_empty() {
        "type to search the transcript".to_owned()
    } else if search.matches.is_empty() {
        "no matches".to_owned()
    } else {
        format!("match {}/{}", search.current + 1, search.matches.len())
    };
    let line = Line::from(vec![
        Span::styled(
            " Search: ",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw(search.query.clone()),
        Span::styled("│", Style::default().fg(Color::Cyan)),
        Span::styled(
            format!("  {summary}  ·  Enter/↑ prev · ↓ next · Esc close"),
            Style::default().fg(Color::DarkGray),
        ),
    ]);
    frame.render_widget(Paragraph::new(line), area);
}

fn draw_footer(frame: &mut Frame, area: Rect, app: &App) {
    let model = value_or(app.health.display_model(), "unknown model");
    let left = if let Some(notice) = &app.notice {
        format!(" {notice}")
    } else {
        format!(" {model} · {}", app.history_label)
    };
    let right = footer_right(app);
    let chunks = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(45), Constraint::Percentage(55)])
        .split(area);
    frame.render_widget(
        Paragraph::new(left).style(Style::default().fg(Color::DarkGray)),
        chunks[0],
    );
    frame.render_widget(
        Paragraph::new(right)
            .alignment(Alignment::Right)
            .style(Style::default().fg(Color::DarkGray)),
        chunks[1],
    );
}

fn footer_right(app: &App) -> String {
    if app.search.is_some() {
        "Enter/↑ prev · ↓ next · Esc close search ".to_owned()
    } else if app.pending_approval.is_some() {
        "y approve · n deny · c cancel · q quit ".to_owned()
    } else if matches!(app.modal, Some(Modal::Delete(_))) {
        "y/Enter delete · n/Esc cancel ".to_owned()
    } else if app.modal.is_some() {
        "Tab focus · Enter confirm · Esc cancel ".to_owned()
    } else if app.pane_focus == PaneFocus::Conversation {
        let context = app
            .context_percent
            .map(|percent| format!("{percent:.1}% context · "))
            .unwrap_or_default();
        format!("{context}Enter send · Ctrl+F search · Esc navigator · Tab panes ")
    } else {
        "Tab panes · Enter open/fold · Ctrl+F search · i chat · u queue · c cancel · d delete · p/t/n create · q quit "
            .to_owned()
    }
}

fn draw_modal(frame: &mut Frame, modal: &Modal) {
    let area = centered_rect(70, 60, frame.area());
    frame.render_widget(Clear, area);
    let (title, lines, error, submitting) = match modal {
        Modal::Project(form) => (
            " Create project ",
            vec![
                Line::styled(
                    "A workspace root can belong to only one project; an existing root opens that project.",
                    Style::default().fg(Color::DarkGray),
                ),
                field_line("Name", &form.name, form.focus == FormFocus::First),
                field_line(
                    "Workspace root",
                    &form.root,
                    form.focus == FormFocus::Second,
                ),
                submit_line(form.focus == FormFocus::Submit, "Create project"),
            ],
            form.state.error.as_deref(),
            form.state.submitting,
        ),
        Modal::Task(form) => (
            " Create task ",
            vec![
                Line::styled(
                    format!("Project: {}", form.project_name),
                    Style::default().fg(Color::DarkGray),
                ),
                field_line("Name", &form.name, form.focus == FormFocus::First),
                submit_line(form.focus == FormFocus::Submit, "Create task"),
            ],
            form.state.error.as_deref(),
            form.state.submitting,
        ),
        Modal::Session(form) => {
            let provider = form
                .providers
                .get(form.provider_index)
                .map(|provider| provider.label())
                .unwrap_or("Unavailable");
            (
                " Create session ",
                vec![
                    Line::styled(
                        format!("Target: {} / {}", form.project_name, form.task_name),
                        Style::default().fg(Color::DarkGray),
                    ),
                    field_line(
                        "Title (optional)",
                        &form.title,
                        form.focus == FormFocus::First,
                    ),
                    field_line("Provider ←/→", provider, form.focus == FormFocus::Second),
                    submit_line(form.focus == FormFocus::Submit, "Create and open session"),
                ],
                form.state.error.as_deref(),
                form.state.submitting,
            )
        }
        Modal::Delete(form) => {
            let detail = match &form.target {
                DeleteTarget::Project { session_count, .. } => format!(
                    "Removes the project and {session_count} session(s) from the Harness; workspace files stay untouched."
                ),
                DeleteTarget::Task { session_count, .. } => {
                    format!("Removes the task and {session_count} session(s) from the Harness.")
                }
                DeleteTarget::Session { .. } => {
                    "Removes the transcript, events, attachments, and session data.".to_owned()
                }
            };
            (
                " Confirm deletion ",
                vec![
                    Line::styled(
                        format!("Delete {}?", form.target.label()),
                        Style::default().fg(Color::Red).add_modifier(Modifier::BOLD),
                    ),
                    Line::raw(detail),
                    Line::styled(
                        "y or Enter deletes · n or Esc cancels",
                        Style::default().fg(Color::DarkGray),
                    ),
                ],
                form.state.error.as_deref(),
                form.state.submitting,
            )
        }
    };
    let mut content = lines;
    content.push(Line::default());
    if submitting {
        let progress = match modal {
            Modal::Delete(_) => "Deleting…",
            Modal::Project(_) | Modal::Task(_) | Modal::Session(_) => "Creating…",
        };
        content.push(Line::styled(
            progress,
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        ));
    } else if let Some(error) = error {
        content.push(Line::styled(
            error.to_owned(),
            Style::default().fg(Color::Red),
        ));
    } else {
        let hint = match modal {
            Modal::Delete(_) => "y or Enter deletes · n or Esc cancels",
            Modal::Project(_) | Modal::Task(_) | Modal::Session(_) => {
                "Tab changes focus · Enter advances/submits · Esc cancels"
            }
        };
        content.push(Line::styled(hint, Style::default().fg(Color::DarkGray)));
    }
    frame.render_widget(
        Paragraph::new(content)
            .block(
                Block::default()
                    .title(title)
                    .borders(Borders::ALL)
                    .border_style(Style::default().fg(Color::Cyan)),
            )
            .wrap(Wrap { trim: false }),
        area,
    );
}

fn draw_commands_pane(frame: &mut Frame, scroll: u16) {
    let area = centered_rect(82, 82, frame.area());
    frame.render_widget(Clear, area);
    frame.render_widget(
        Paragraph::new(command_help_lines())
            .block(
                Block::default()
                    .title(" Commands ")
                    .borders(Borders::ALL)
                    .border_style(Style::default().fg(Color::Cyan)),
            )
            .wrap(Wrap { trim: false })
            .scroll((scroll, 0)),
        area,
    );
}

pub(crate) fn commands_max_scroll(terminal_width: u16, terminal_height: u16) -> u16 {
    let pane_width = terminal_width.saturating_mul(82) / 100;
    let pane_height = terminal_height.saturating_mul(82) / 100;
    let inner_width = pane_width.saturating_sub(2).max(1);
    let viewport = pane_height.saturating_sub(2);
    let paragraph = Paragraph::new(command_help_lines()).wrap(Wrap { trim: false });
    let visual_lines = paragraph.line_count(inner_width).min(u16::MAX as usize) as u16;
    visual_lines.saturating_sub(viewport)
}

fn command_help_lines() -> Vec<Line<'static>> {
    let command = Style::default()
        .fg(Color::Cyan)
        .add_modifier(Modifier::BOLD);
    let option = Style::default().fg(Color::Yellow);
    let muted = Style::default().fg(Color::DarkGray);
    vec![
        Line::styled("Session commands", command),
        Line::raw("Backend commands run only while the session is idle. Bare settings commands show the current effective value."),
        Line::default(),
        Line::styled("/commands", command),
        Line::raw("Open this local command reference. Available even while a run is active."),
        Line::default(),
        Line::styled("/clear", command),
        Line::raw("Clear the session's model context."),
        Line::default(),
        Line::styled("/chdir", command),
        Line::styled("  /chdir <directory>", option),
        Line::styled("  /chdir --reset    (alias: /chdir -)", option),
        Line::raw("Show, change, or reset the session working directory. Paths may be quoted and must be allowed existing directories."),
        Line::default(),
        Line::styled("/approve [always_ask|shell_only|auto_approve]", command),
        Line::raw("Show or set the session approval mode."),
        Line::default(),
        Line::styled("/verbose", command),
        Line::raw("Toggle detailed tool results, iterations, API metrics, context events, and run metrics."),
        Line::default(),
        Line::styled("/maxiters [positive integer]", command),
        Line::raw("Show or set the maximum agent iterations for this session."),
        Line::default(),
        Line::styled("/thinking [low|medium|high]", command),
        Line::raw("Show or set the session reasoning effort."),
        Line::default(),
        Line::styled("/skills [name]", command),
        Line::raw("List installed Harness skills or display one skill's complete instructions."),
        Line::default(),
        Line::styled("/model [native|codex|claude]", command),
        Line::styled("  Codex aliases: app-server | codex-app-server", option),
        Line::styled("  Claude alias: claude-agent", option),
        Line::raw("Show or switch the session model provider. Codex requires an available app-server; Claude requires the claude CLI."),
        Line::default(),
        Line::styled("Composer keys", command),
        Line::styled("  Ctrl+A / Ctrl+E", option),
        Line::raw("Jump to the start or end of the current draft line."),
        Line::styled("  Ctrl+W / Alt+Backspace", option),
        Line::raw("Delete the word before the cursor."),
        Line::styled("  Ctrl+K", option),
        Line::raw("Delete from the cursor to the end of the draft line."),
        Line::styled("  Ctrl+U", option),
        Line::raw("Clear the whole draft."),
        Line::styled("  Ctrl+P / Ctrl+N", option),
        Line::raw("Recall an older or newer sent message."),
        Line::default(),
        Line::styled(
            "↑/↓ or j/k scroll · PgUp/PgDn page · Home/End · Esc, Enter, or q closes",
            muted,
        ),
    ]
}

fn draw_approval(frame: &mut Frame, approval: &crate::app::ApprovalPrompt) {
    let area = centered_rect(78, 72, frame.area());
    frame.render_widget(Clear, area);
    let mut lines = vec![
        Line::styled(
            format!("Tool: {}", approval.tool_name),
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        ),
        Line::default(),
        Line::raw(truncate(&approval.args_json, 1800)),
    ];
    if !approval.diff_preview.is_empty() {
        lines.push(Line::default());
        lines.push(Line::styled(
            "Proposed diff:",
            Style::default().fg(Color::Cyan),
        ));
        lines.push(Line::raw(truncate(&approval.diff_preview, 2400)));
    }
    lines.push(Line::default());
    if approval.state.submitting {
        lines.push(Line::styled(
            "Resolving…",
            Style::default().fg(Color::Yellow),
        ));
    } else if let Some(error) = &approval.state.error {
        lines.push(Line::styled(error.clone(), Style::default().fg(Color::Red)));
    }
    lines.push(Line::styled(
        "y approve · n deny · c cancel run",
        Style::default().fg(Color::DarkGray),
    ));
    frame.render_widget(
        Paragraph::new(lines)
            .block(
                Block::default()
                    .title(" Approval required ")
                    .borders(Borders::ALL)
                    .border_style(Style::default().fg(Color::Yellow)),
            )
            .wrap(Wrap { trim: false }),
        area,
    );
}

fn truncate(value: &str, limit: usize) -> String {
    if value.chars().count() <= limit {
        value.to_owned()
    } else {
        format!("{}…", value.chars().take(limit).collect::<String>())
    }
}

fn field_line<'a>(label: &'a str, value: &'a str, focused: bool) -> Line<'a> {
    let marker = if focused { ">" } else { " " };
    let cursor = if focused { "│" } else { "" };
    Line::from(vec![
        Span::styled(
            format!("{marker} {label}: "),
            if focused {
                Style::default()
                    .fg(Color::Cyan)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default()
            },
        ),
        Span::raw(value),
        Span::styled(cursor, Style::default().fg(Color::Cyan)),
    ])
}

fn submit_line(focused: bool, label: &str) -> Line<'static> {
    Line::styled(
        format!("{} [ {label} ]", if focused { ">" } else { " " }),
        if focused {
            Style::default().fg(Color::Black).bg(Color::Cyan)
        } else {
            Style::default().fg(Color::DarkGray)
        },
    )
}

fn centered_rect(percent_x: u16, percent_y: u16, area: Rect) -> Rect {
    let vertical = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Percentage((100 - percent_y) / 2),
            Constraint::Percentage(percent_y),
            Constraint::Percentage((100 - percent_y) / 2),
        ])
        .split(area);
    Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage((100 - percent_x) / 2),
            Constraint::Percentage(percent_x),
            Constraint::Percentage((100 - percent_x) / 2),
        ])
        .split(vertical[1])[1]
}

fn pane_border(focused: bool) -> Style {
    Style::default().fg(if focused {
        Color::Cyan
    } else {
        Color::DarkGray
    })
}

fn value_or<'a>(value: &'a str, fallback: &'a str) -> &'a str {
    if value.is_empty() {
        fallback
    } else {
        value
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn user_lines_are_right_aligned() {
        let lines = entry_lines(&TranscriptEntry {
            kind: EntryKind::User,
            text: "You\nHello".to_owned(),
        });

        assert_eq!(lines[0].alignment, Some(Alignment::Right));
        assert_eq!(lines[1].alignment, Some(Alignment::Right));
    }

    #[test]
    fn assistant_lines_are_left_aligned() {
        let lines = entry_lines(&TranscriptEntry {
            kind: EntryKind::Assistant,
            text: "Assistant\nHello".to_owned(),
        });

        assert_eq!(lines[0].alignment, Some(Alignment::Left));
        assert_eq!(lines[1].alignment, Some(Alignment::Left));
    }

    #[test]
    fn conversation_footer_shows_rounded_context_before_key_hints() {
        let mut app = App::new(
            "http://localhost".to_owned(),
            crate::api::Health::default(),
            crate::api::SessionTree::default(),
            None,
        );
        app.pane_focus = PaneFocus::Conversation;
        app.context_percent = Some(0.64);

        assert_eq!(
            footer_right(&app),
            "0.6% context · Enter send · Ctrl+F search · Esc navigator · Tab panes "
        );
    }

    #[test]
    fn command_help_lists_every_supported_command_and_options() {
        let rendered = command_help_lines()
            .iter()
            .flat_map(|line| line.spans.iter())
            .map(|span| span.content.as_ref())
            .collect::<Vec<_>>()
            .join("\n");

        for command in [
            "/commands",
            "/clear",
            "/chdir <directory>",
            "/approve [always_ask|shell_only|auto_approve]",
            "/verbose",
            "/maxiters [positive integer]",
            "/thinking [low|medium|high]",
            "/skills [name]",
            "/model [native|codex|claude]",
        ] {
            assert!(rendered.contains(command), "missing {command}");
        }
        assert!(rendered.contains("app-server | codex-app-server"));
    }

    #[test]
    fn draft_wraps_at_word_boundaries_and_hard_breaks_long_words() {
        let text = "hello world again";
        let rows = wrap_rows(text, 12);
        let rendered: Vec<&str> = rows.iter().map(|row| &text[row.clone()]).collect();
        assert_eq!(rendered, vec!["hello world ", "again"]);

        let long = "abcdefghij";
        let rows = wrap_rows(long, 4);
        let rendered: Vec<&str> = rows.iter().map(|row| &long[row.clone()]).collect();
        assert_eq!(rendered, vec!["abcd", "efgh", "ij"]);
    }

    #[test]
    fn draft_rows_track_explicit_newlines_and_multibyte_text() {
        let text = "héllo\n\nwörld";
        let rows = wrap_rows(text, 20);
        let rendered: Vec<&str> = rows.iter().map(|row| &text[row.clone()]).collect();
        assert_eq!(rendered, vec!["héllo", "", "wörld"]);
        // The cursor sits after "wö", which is three bytes past the row start.
        assert_eq!(composer_cursor(text, &rows, text.len() - 3), (2, 2));
        // Byte 6 is the end of the first row; byte 7 starts the blank row.
        assert_eq!(composer_cursor(text, &rows, 6), (0, 5));
        assert_eq!(composer_cursor(text, &rows, 7), (1, 0));
    }

    #[test]
    fn long_drafts_scroll_to_keep_the_cursor_visible() {
        // Well past the old six-line cap, where draft text used to disappear.
        let text = (0..20)
            .map(|index| format!("line {index}"))
            .collect::<Vec<_>>()
            .join("\n");
        let rows = wrap_rows(&text, 40);
        assert_eq!(rows.len(), 20);
        assert_eq!(composer_cursor(&text, &rows, text.len()), (19, 7));
        assert_eq!(composer_scroll(19, 20, COMPOSER_MAX_ROWS), 10);
        assert_eq!(composer_scroll(0, 20, COMPOSER_MAX_ROWS), 0);
        assert_eq!(composer_scroll(19, 20, 40), 0, "short drafts never scroll");
    }

    #[test]
    fn command_scroll_limit_adapts_to_terminal_viewport() {
        let compact = commands_max_scroll(80, 24);
        let spacious = commands_max_scroll(160, 60);

        assert!(compact > spacious);
        assert_eq!(
            commands_max_scroll(200, 100),
            0,
            "a tall terminal shows it all"
        );
    }

    #[test]
    fn focused_composer_hint_advertises_commands_pane_and_newlines() {
        assert_eq!(
            FOCUSED_COMPOSER_HINT,
            "/commands opens command pane · Enter sends · Shift+Enter newline · Ctrl+C cancels run · Esc returns to navigator"
        );
    }
}

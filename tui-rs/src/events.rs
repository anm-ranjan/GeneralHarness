use serde::Deserialize;
use serde_json::Value;

use crate::api::SessionMeta;

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct EventEnvelope {
    pub id: String,
    pub session_id: String,
    #[serde(rename = "type")]
    pub event_type: String,
    pub created_at: String,
    pub data: Value,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct SessionLoaded {
    pub meta: SessionMeta,
    pub events: Vec<EventEnvelope>,
    pub event_offset: usize,
    pub event_total: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EntryKind {
    User,
    Assistant,
    Thinking,
    Tool,
    Error,
    Status,
    Muted,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TranscriptEntry {
    pub kind: EntryKind,
    pub text: String,
}

impl TranscriptEntry {
    fn new(kind: EntryKind, text: impl Into<String>) -> Self {
        Self {
            kind,
            text: text.into(),
        }
    }
}

pub fn decode_session_loaded(event: &EventEnvelope) -> Option<SessionLoaded> {
    if event.event_type != "session_loaded" {
        return None;
    }
    serde_json::from_value(event.data.clone()).ok()
}

pub fn render_event(event: &EventEnvelope, verbose_tools: bool) -> Option<TranscriptEntry> {
    let data = &event.data;
    let text = |key: &str| data.get(key).and_then(Value::as_str).unwrap_or("");
    let entry = match event.event_type.as_str() {
        "session_loaded" | "assistant_delta" | "plan_update" => return None,
        "user_message" => TranscriptEntry::new(EntryKind::User, format!("You\n{}", text("text"))),
        "assistant_message" => TranscriptEntry::new(
            EntryKind::Assistant,
            format!("Assistant\n{}", text("markdown")),
        ),
        "thinking" => TranscriptEntry::new(
            EntryKind::Thinking,
            format!("Thinking\n{}", text("markdown")),
        ),
        "status" => TranscriptEntry::new(EntryKind::Status, text("text")),
        "error" => TranscriptEntry::new(EntryKind::Error, format!("Error: {}", text("text"))),
        "iteration" if verbose_tools => {
            let n = display_scalar(data.get("n"));
            let max = display_scalar(data.get("max"));
            let suffix = if max.is_empty() {
                String::new()
            } else {
                format!("/{max}")
            };
            TranscriptEntry::new(EntryKind::Muted, format!("Iteration {n}{suffix}"))
        }
        "iteration" => return None,
        "tool_call" => {
            let label = first_text(data, &["status_line", "name"]).unwrap_or("tool");
            TranscriptEntry::new(EntryKind::Tool, format!("● {label}"))
        }
        "tool_result" if verbose_tools => {
            let ok = data.get("ok").and_then(Value::as_bool).unwrap_or(true);
            let marker = if ok { "ok" } else { "failed" };
            let preview = text("preview");
            TranscriptEntry::new(EntryKind::Tool, format!("  {marker}: {preview}"))
        }
        "tool_result" => return None,
        "approval_required" => TranscriptEntry::new(
            EntryKind::Status,
            format!("Approval required: {}", text("tool_name")),
        ),
        "approval_resolved" => {
            let approved = data
                .get("approved")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            TranscriptEntry::new(
                EntryKind::Status,
                if approved {
                    "Approval approved"
                } else {
                    "Approval denied"
                },
            )
        }
        "question_required" => {
            TranscriptEntry::new(EntryKind::Status, format!("Question\n{}", text("question")))
        }
        "question_resolved" => {
            let answered = data
                .get("answered")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            TranscriptEntry::new(
                EntryKind::Status,
                if answered {
                    format!("Answer\n{}", text("answer"))
                } else {
                    "Question was not answered".to_owned()
                },
            )
        }
        "context_usage" if verbose_tools => {
            TranscriptEntry::new(EntryKind::Muted, format!("Context: {}", text("usage_str")))
        }
        "context_usage" => return None,
        "api_metrics" if verbose_tools => TranscriptEntry::new(
            EntryKind::Muted,
            format!("[api_metrics] {}", compact_json(data)),
        ),
        "api_metrics" => return None,
        "run_metrics" if verbose_tools => {
            TranscriptEntry::new(EntryKind::Muted, render_metrics(data))
        }
        "run_metrics" => return None,
        "queue_updated" => {
            let count = data
                .get("items")
                .and_then(Value::as_array)
                .map_or(0, Vec::len);
            TranscriptEntry::new(EntryKind::Muted, format!("Queue: {count} pending"))
        }
        "file_change" => TranscriptEntry::new(
            EntryKind::Tool,
            format!(
                "File {}: {}",
                fallback(text("action"), "changed"),
                text("path")
            ),
        ),
        "generated_artifact" => TranscriptEntry::new(
            EntryKind::Tool,
            format!("Generated artifact: {}", text("path")),
        ),
        "run_finished" => TranscriptEntry::new(
            EntryKind::Status,
            format!("Run finished: {}", fallback(text("reason"), "completed")),
        ),
        unknown => TranscriptEntry::new(
            EntryKind::Muted,
            format!("[{unknown}] {}", compact_json(data)),
        ),
    };
    if entry.text.trim().is_empty() {
        None
    } else {
        Some(entry)
    }
}

fn first_text<'a>(data: &'a Value, keys: &[&str]) -> Option<&'a str> {
    keys.iter().find_map(|key| {
        data.get(*key)
            .and_then(Value::as_str)
            .filter(|text| !text.is_empty())
    })
}

fn fallback<'a>(value: &'a str, default: &'a str) -> &'a str {
    if value.is_empty() {
        default
    } else {
        value
    }
}

fn display_scalar(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(value)) => value.clone(),
        Some(Value::Number(value)) => value.to_string(),
        _ => String::new(),
    }
}

fn render_metrics(data: &Value) -> String {
    let mut parts = Vec::new();
    if let Some(elapsed) = data.get("elapsed_s").or_else(|| data.get("elapsed")) {
        parts.push(format!("{}s", display_scalar(Some(elapsed))));
    }
    if let Some(tokens) = data.get("total_tokens") {
        parts.push(format!("{} tokens", display_scalar(Some(tokens))));
    }
    if let Some(percent) = data.get("context_percent") {
        parts.push(format!("{}% context", display_scalar(Some(percent))));
    }
    if parts.is_empty() {
        "Run metrics updated".to_owned()
    } else {
        format!("Run metrics: {}", parts.join(" · "))
    }
}

fn compact_json(value: &Value) -> String {
    let text = value.to_string();
    const LIMIT: usize = 240;
    if text.chars().count() <= LIMIT {
        return text;
    }
    format!("{}…", text.chars().take(LIMIT).collect::<String>())
}

#[cfg(test)]
mod tests {
    use pretty_assertions::assert_eq;
    use serde_json::json;

    use super::*;

    fn event(event_type: &str, data: Value) -> EventEnvelope {
        EventEnvelope {
            event_type: event_type.to_owned(),
            data,
            ..EventEnvelope::default()
        }
    }

    #[test]
    fn renders_known_event() {
        assert_eq!(
            render_event(
                &event("assistant_message", json!({"markdown": "Hello"})),
                false,
            ),
            Some(TranscriptEntry::new(
                EntryKind::Assistant,
                "Assistant\nHello"
            ))
        );
    }

    #[test]
    fn renders_unknown_event_without_failing() {
        let rendered = render_event(&event("future_event", json!({"answer": 42})), false).unwrap();
        assert_eq!(rendered.kind, EntryKind::Muted);
        assert!(rendered.text.contains("future_event"));
        assert!(rendered.text.contains("42"));
    }

    #[test]
    fn plan_updates_are_owned_by_the_plan_panel() {
        assert_eq!(
            render_event(
                &event(
                    "plan_update",
                    json!({"items": [{"content": "Inspect", "status": "in_progress"}]})
                ),
                false,
            ),
            None
        );
    }

    #[test]
    fn questions_render_as_readable_transcript_entries() {
        assert_eq!(
            render_event(
                &event(
                    "question_required",
                    json!({"question_id": "qst_1", "question": "Which branch?"})
                ),
                false,
            ),
            Some(TranscriptEntry::new(
                EntryKind::Status,
                "Question\nWhich branch?"
            ))
        );
        assert_eq!(
            render_event(
                &event(
                    "question_resolved",
                    json!({"question_id": "qst_1", "answer": "main", "answered": true})
                ),
                false,
            ),
            Some(TranscriptEntry::new(EntryKind::Status, "Answer\nmain"))
        );
    }

    #[test]
    fn verbose_diagnostics_are_hidden_by_default() {
        assert_eq!(
            render_event(
                &event("context_usage", json!({"usage_str": "0.8% context"})),
                false,
            ),
            None
        );
        assert_eq!(
            render_event(
                &event("api_metrics", json!({"completion_tokens": 159})),
                false,
            ),
            None
        );
        assert_eq!(
            render_event(&event("iteration", json!({"n": 1, "max": 50})), false),
            None
        );
        assert_eq!(
            render_event(&event("run_metrics", json!({"elapsed_s": 4.1})), false,),
            None
        );
        assert_eq!(
            render_event(
                &event(
                    "tool_result",
                    json!({"ok": true, "preview": "elaborate output"}),
                ),
                false,
            ),
            None
        );
        let tool_header = render_event(
            &event("tool_call", json!({"status_line": "Read file"})),
            false,
        )
        .unwrap();
        assert_eq!(tool_header.text, "● Read file");
    }

    #[test]
    fn verbose_diagnostics_are_rendered_when_enabled() {
        let context = render_event(
            &event("context_usage", json!({"usage_str": "0.8% context"})),
            true,
        )
        .unwrap();
        assert_eq!(context.text, "Context: 0.8% context");

        let metrics = render_event(
            &event("api_metrics", json!({"completion_tokens": 159})),
            true,
        )
        .unwrap();
        assert!(metrics.text.contains("api_metrics"));
        assert!(metrics.text.contains("159"));

        let iteration =
            render_event(&event("iteration", json!({"n": 1, "max": 50})), true).unwrap();
        assert_eq!(iteration.text, "Iteration 1/50");

        let run_metrics =
            render_event(&event("run_metrics", json!({"elapsed_s": 4.1})), true).unwrap();
        assert!(run_metrics.text.contains("Run metrics"));

        let tool_result = render_event(
            &event(
                "tool_result",
                json!({"ok": true, "preview": "elaborate output"}),
            ),
            true,
        )
        .unwrap();
        assert!(tool_result.text.contains("elaborate output"));
    }

    #[test]
    fn decodes_loaded_replay() {
        let loaded = event(
            "session_loaded",
            json!({
                "meta": {"id": "ses_1", "provider": "native"},
                "events": [{"type": "status", "data": {"text": "ready"}}],
                "event_offset": 12,
                "event_total": 13
            }),
        );
        let decoded = decode_session_loaded(&loaded).unwrap();
        assert_eq!(decoded.meta.id, "ses_1");
        assert_eq!(decoded.events.len(), 1);
        assert_eq!(decoded.event_offset, 12);
        assert_eq!(decoded.event_total, 13);
    }
}

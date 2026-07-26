use std::collections::HashMap;

use anyhow::{bail, Context, Result};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use url::Url;

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct Health {
    pub status: String,
    pub model: String,
    pub read_model: String,
    pub approval_mode: String,
    pub app_name: String,
    pub native_enabled: bool,
    pub default_provider: String,
    pub codex_app_server_enabled: bool,
    pub claude_agent_enabled: bool,
    pub verbose: bool,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct RunSettings {
    pub verbose_tools: Option<bool>,
}

impl Health {
    pub fn display_model(&self) -> &str {
        if self.model.is_empty() {
            self.read_model.as_str()
        } else {
            self.model.as_str()
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct SessionMeta {
    pub id: String,
    pub project_id: String,
    pub task_id: String,
    pub title: String,
    pub status: String,
    pub provider: String,
    pub kind: String,
    pub message_queue: Vec<Value>,
    pub run_settings: RunSettings,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct MessageResponse {
    pub status: String,
    pub session_id: String,
    pub detail: String,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct DeleteResult {
    pub status: String,
    pub removed_sessions: Vec<String>,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct QueueResponse {
    pub status: String,
    pub items: Vec<Value>,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct EventsPage {
    pub offset: usize,
    pub total: usize,
    pub events: Vec<crate::events::EventEnvelope>,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct TaskInfo {
    pub id: String,
    pub name: String,
    pub sessions: Vec<String>,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct ProjectInfo {
    pub id: String,
    pub name: String,
    pub root: String,
    pub tasks: Vec<TaskInfo>,
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct SessionTree {
    pub projects: Vec<ProjectInfo>,
    pub sessions: HashMap<String, SessionMeta>,
}

impl SessionTree {
    pub fn lists_session(&self, session_id: &str) -> bool {
        self.projects.iter().any(|project| {
            project.tasks.iter().any(|task| {
                task.sessions
                    .iter()
                    .any(|candidate| candidate == session_id)
            })
        })
    }
}

#[derive(Clone)]
pub struct MyHarnessClient {
    http: Client,
    base_url: Url,
}

impl MyHarnessClient {
    pub fn new(raw_url: &str) -> Result<Self> {
        let base_url = normalize_backend_url(raw_url)?;
        let http = Client::builder()
            .build()
            .context("failed to create HTTP client")?;
        Ok(Self { http, base_url })
    }

    pub fn display_url(&self) -> String {
        self.base_url.as_str().trim_end_matches('/').to_owned()
    }

    pub async fn health(&self) -> Result<Health> {
        self.get_json("api/health").await
    }

    pub async fn sessions(&self) -> Result<SessionTree> {
        self.get_json("api/sessions").await
    }

    pub async fn create_project(&self, name: &str, root: &str) -> Result<ProjectInfo> {
        self.post_json("api/projects", &json!({ "name": name, "root": root }))
            .await
    }

    pub async fn create_task(&self, project_id: &str, name: &str) -> Result<TaskInfo> {
        self.post_json(
            "api/tasks",
            &json!({ "project_id": project_id, "name": name }),
        )
        .await
    }

    pub async fn create_session(
        &self,
        project_id: &str,
        task_id: &str,
        title: &str,
        provider: &str,
    ) -> Result<SessionMeta> {
        self.post_json(
            "api/sessions",
            &json!({
                "project_id": project_id,
                "task_id": task_id,
                "title": title,
                "provider": provider,
            }),
        )
        .await
    }

    pub async fn create_chat(&self, title: &str, provider: &str) -> Result<SessionMeta> {
        self.post_json(
            "api/chats",
            &json!({ "title": title, "provider": provider }),
        )
        .await
    }

    pub async fn send_message(&self, session_id: &str, text: &str) -> Result<MessageResponse> {
        self.post_json(
            &format!("api/sessions/{session_id}/message"),
            &json!({ "text": text, "images": [], "attachments": [] }),
        )
        .await
    }

    pub async fn resolve_approval(
        &self,
        session_id: &str,
        approval_id: &str,
        approved: bool,
    ) -> Result<Value> {
        self.post_json(
            &format!("api/sessions/{session_id}/approval"),
            &json!({ "approval_id": approval_id, "approved": approved }),
        )
        .await
    }

    pub async fn delete_queued_message(
        &self,
        session_id: &str,
        message_id: &str,
    ) -> Result<QueueResponse> {
        self.delete_json(&format!("api/sessions/{session_id}/queue/{message_id}"))
            .await
    }

    pub async fn reorder_queue(&self, session_id: &str, order: &[String]) -> Result<QueueResponse> {
        self.post_json(
            &format!("api/sessions/{session_id}/queue/reorder"),
            &json!({ "order": order }),
        )
        .await
    }

    pub async fn session_events(
        &self,
        session_id: &str,
        offset: usize,
        limit: usize,
    ) -> Result<EventsPage> {
        self.get_json(&format!(
            "api/sessions/{session_id}/events?offset={offset}&limit={limit}"
        ))
        .await
    }

    pub async fn cancel_run(&self, session_id: &str) -> Result<Value> {
        self.post_json(&format!("api/sessions/{session_id}/cancel"), &json!({}))
            .await
    }

    pub async fn delete_project(&self, project_id: &str) -> Result<DeleteResult> {
        self.delete_json(&format!("api/projects/{project_id}"))
            .await
    }

    pub async fn delete_task(&self, project_id: &str, task_id: &str) -> Result<DeleteResult> {
        self.delete_json(&format!("api/projects/{project_id}/tasks/{task_id}"))
            .await
    }

    pub async fn delete_session(&self, session_id: &str) -> Result<DeleteResult> {
        self.delete_json(&format!("api/sessions/{session_id}"))
            .await
    }

    pub fn session_ws_url(&self, session_id: &str) -> Result<Url> {
        let mut url = self
            .base_url
            .join(&format!("api/sessions/{session_id}/events"))
            .context("failed to construct session WebSocket URL")?;
        let scheme = match url.scheme() {
            "http" => "ws",
            "https" => "wss",
            other => bail!("unsupported backend URL scheme: {other}"),
        };
        url.set_scheme(scheme)
            .map_err(|_| anyhow::anyhow!("failed to set WebSocket URL scheme"))?;
        Ok(url)
    }

    fn endpoint(&self, path: &str) -> Result<Url> {
        self.base_url
            .join(path)
            .with_context(|| format!("failed to build backend URL for {path}"))
    }

    async fn request_json<T>(&self, request: reqwest::RequestBuilder) -> Result<T>
    where
        T: for<'de> Deserialize<'de>,
    {
        let response = request.send().await.context("backend request failed")?;
        let status = response.status();
        if !status.is_success() {
            let detail = response.text().await.unwrap_or_default();
            bail!("backend returned HTTP {status}: {}", error_detail(&detail));
        }
        response
            .json()
            .await
            .context("invalid backend JSON response")
    }

    async fn get_json<T>(&self, path: &str) -> Result<T>
    where
        T: for<'de> Deserialize<'de>,
    {
        self.request_json(self.http.get(self.endpoint(path)?)).await
    }

    async fn post_json<T, B>(&self, path: &str, body: &B) -> Result<T>
    where
        T: for<'de> Deserialize<'de>,
        B: Serialize + ?Sized,
    {
        self.request_json(self.http.post(self.endpoint(path)?).json(body))
            .await
    }

    async fn delete_json<T>(&self, path: &str) -> Result<T>
    where
        T: for<'de> Deserialize<'de>,
    {
        self.request_json(self.http.delete(self.endpoint(path)?))
            .await
    }
}

fn error_detail(body: &str) -> String {
    serde_json::from_str::<Value>(body)
        .ok()
        .and_then(|value| {
            value
                .get("detail")
                .and_then(Value::as_str)
                .map(str::to_owned)
        })
        .filter(|detail| !detail.is_empty())
        .unwrap_or_else(|| body.trim().to_owned())
}

pub fn normalize_backend_url(raw_url: &str) -> Result<Url> {
    let trimmed = raw_url.trim();
    let value = if trimmed.is_empty() {
        crate::args::DEFAULT_BACKEND_URL.to_owned()
    } else if trimmed.contains("://") {
        trimmed.to_owned()
    } else {
        format!("http://{trimmed}")
    };
    let mut url = Url::parse(&value).context("invalid backend URL")?;
    if !matches!(url.scheme(), "http" | "https") {
        bail!("backend URL must use http or https");
    }
    if url.host_str().is_none() {
        bail!("backend URL must include a host");
    }
    if !url.path().ends_with('/') {
        let path = format!("{}/", url.path().trim_end_matches('/'));
        url.set_path(&path);
    }
    Ok(url)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_host_and_port() {
        let url = normalize_backend_url("localhost:8420/").unwrap();
        assert_eq!(url.as_str(), "http://localhost:8420/");
    }

    #[test]
    fn preserves_backend_path_prefix() {
        let client = MyHarnessClient::new("https://example.test/jarvis").unwrap();
        assert_eq!(
            client.session_ws_url("ses_1").unwrap().as_str(),
            "wss://example.test/jarvis/api/sessions/ses_1/events"
        );
    }

    #[test]
    fn rejects_non_http_scheme() {
        assert!(normalize_backend_url("file:///tmp/socket").is_err());
    }

    #[test]
    fn extracts_fastapi_error_detail() {
        assert_eq!(
            error_detail(r#"{"detail":"Project is outside allowed paths"}"#),
            "Project is outside allowed paths"
        );
        assert_eq!(error_detail("plain failure"), "plain failure");
    }

    #[test]
    fn health_accepts_redundant_codex_capability_fields() {
        let health: Health = serde_json::from_value(serde_json::json!({
            "codex_enabled": true,
            "codex_app_server_enabled": true
        }))
        .unwrap();
        assert!(health.codex_app_server_enabled);
    }

    #[test]
    fn message_response_supports_all_backend_statuses() {
        for status in ["started", "queued", "command", "blocked"] {
            let response: MessageResponse = serde_json::from_value(serde_json::json!({
                "status": status,
                "session_id": "ses_1",
                "detail": if status == "blocked" { "not ready" } else { "" }
            }))
            .unwrap();
            assert_eq!(response.status, status);
            assert_eq!(response.session_id, "ses_1");
        }
    }

    #[test]
    fn delete_result_defaults_removed_sessions_for_single_session_delete() {
        let result: DeleteResult =
            serde_json::from_value(serde_json::json!({"status": "deleted"})).unwrap();
        assert_eq!(result.status, "deleted");
        assert!(result.removed_sessions.is_empty());
    }
}

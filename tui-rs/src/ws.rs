use futures_util::{SinkExt, StreamExt};
use tokio::sync::mpsc::UnboundedSender;
use tokio_tungstenite::{connect_async, tungstenite::Message};
use url::Url;

use crate::events::{decode_session_loaded, EventEnvelope};

#[derive(Debug)]
pub enum StreamUpdate {
    Connected {
        generation: u64,
    },
    Loaded {
        generation: u64,
        loaded: crate::events::SessionLoaded,
    },
    Event {
        generation: u64,
        event: EventEnvelope,
    },
    Disconnected {
        generation: u64,
        detail: String,
    },
}

pub async fn stream_session(url: Url, generation: u64, updates: UnboundedSender<StreamUpdate>) {
    let result = stream_session_inner(url, generation, &updates).await;
    if let Err(error) = result {
        let _ = updates.send(StreamUpdate::Disconnected {
            generation,
            detail: error.to_string(),
        });
    }
}

async fn stream_session_inner(
    url: Url,
    generation: u64,
    updates: &UnboundedSender<StreamUpdate>,
) -> anyhow::Result<()> {
    let (mut socket, _) = connect_async(url.as_str()).await?;
    let _ = updates.send(StreamUpdate::Connected { generation });

    while let Some(message) = socket.next().await {
        match message? {
            Message::Text(text) => {
                let event: EventEnvelope = serde_json::from_str(text.as_ref())?;
                if let Some(loaded) = decode_session_loaded(&event) {
                    let _ = updates.send(StreamUpdate::Loaded { generation, loaded });
                } else {
                    let _ = updates.send(StreamUpdate::Event { generation, event });
                }
            }
            Message::Binary(bytes) => {
                let event: EventEnvelope = serde_json::from_slice(&bytes)?;
                if let Some(loaded) = decode_session_loaded(&event) {
                    let _ = updates.send(StreamUpdate::Loaded { generation, loaded });
                } else {
                    let _ = updates.send(StreamUpdate::Event { generation, event });
                }
            }
            Message::Ping(payload) => socket.send(Message::Pong(payload)).await?,
            Message::Close(frame) => {
                let detail = frame
                    .map(|frame| frame.reason.to_string())
                    .filter(|reason| !reason.is_empty())
                    .unwrap_or_else(|| "server closed the connection".to_owned());
                let _ = updates.send(StreamUpdate::Disconnected { generation, detail });
                return Ok(());
            }
            Message::Pong(_) | Message::Frame(_) => {}
        }
    }

    let _ = updates.send(StreamUpdate::Disconnected {
        generation,
        detail: "event stream ended".to_owned(),
    });
    Ok(())
}

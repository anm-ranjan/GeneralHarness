use clap::Parser;

pub const DEFAULT_BACKEND_URL: &str = "http://127.0.0.1:8420";

#[derive(Debug, Parser)]
#[command(name = "myharness-tui", version, about)]
pub struct Args {
    /// MyHarness backend HTTP URL.
    #[arg(long, env = "MYHARNESS_BACKEND_URL", default_value = DEFAULT_BACKEND_URL)]
    pub backend_url: String,

    /// Session to select when the TUI starts.
    #[arg(long)]
    pub session: Option<String>,
}

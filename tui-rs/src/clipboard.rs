//! Clipboard image capture for pasted screenshots/images.
//!
//! Terminal bracketed paste only ever delivers text, so an image pasted with
//! Ctrl+V/Cmd+V cannot arrive through `Event::Paste`. Instead, on every paste
//! we check the OS clipboard directly (macOS/Windows/Linux-X11 via `arboard`)
//! for image data before falling back to the pasted text. This mirrors how
//! Claude Code's own CLI captures pasted images. Wayland clipboard image
//! support is intentionally not wired up (`arboard`'s `wayland-data-control`
//! feature is left disabled), so clipboard image paste has no effect there
//! and text paste continues to work normally.

use base64::{engine::general_purpose::STANDARD, Engine as _};

use crate::app::PendingImage;

/// Attempts to read an image from the OS clipboard and encode it as a PNG
/// data URL ready for `MyHarnessClient::send_message`. Returns `None` when
/// the clipboard has no image (including when the platform's clipboard
/// backend is unavailable, e.g. a bare Wayland session).
pub fn read_clipboard_image() -> Option<PendingImage> {
    let mut clipboard = arboard::Clipboard::new().ok()?;
    let image = clipboard.get_image().ok()?;
    encode_png_data_url(image.width as u32, image.height as u32, &image.bytes)
}

fn encode_png_data_url(width: u32, height: u32, rgba: &[u8]) -> Option<PendingImage> {
    if width == 0 || height == 0 {
        return None;
    }
    let mut png_bytes = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut png_bytes, width, height);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        let mut writer = encoder.write_header().ok()?;
        writer.write_image_data(rgba).ok()?;
    }
    let size_bytes = png_bytes.len();
    let data_url = format!("data:image/png;base64,{}", STANDARD.encode(&png_bytes));
    Some(PendingImage {
        name: "pasted-image.png".to_owned(),
        mime: "image/png".to_owned(),
        data_url,
        size_bytes,
    })
}

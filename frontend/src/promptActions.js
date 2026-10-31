// Helpers for Copy / Resend / Edit on a prompt: the stored attachments are
// fetched back from the server and turned into the same { data, mime, name }
// payloads the composer builds, so a restored prompt sends exactly like a new one.

function bytesToBase64(bytes) {
  let binary = ''
  const chunk = 0x8000
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk))
  }
  return btoa(binary)
}

export async function attachmentPayloads(attachments = [], fetchImpl = fetch) {
  const payloads = []
  for (const attachment of attachments || []) {
    if (!attachment?.url) continue
    const res = await fetchImpl(attachment.url)
    if (!res.ok) throw new Error(`Could not load ${attachment.name || 'an attachment'} again (${res.status}).`)
    const blob = await res.blob()
    const mime = attachment.mime || blob.type || 'application/octet-stream'
    const bytes = new Uint8Array(await blob.arrayBuffer())
    payloads.push({
      data: `data:${mime};base64,${bytesToBase64(bytes)}`,
      mime,
      name: attachment.name || attachment.filename || 'attachment',
    })
  }
  return payloads
}

// What a prompt message carries as attachments (older events stored images).
export function promptAttachments(item) {
  return (item?.attachments?.length ? item.attachments : item?.images) || []
}

// Providers whose context the server rolls back for a failed turn, so a deleted
// prompt cannot still be remembered. Codex and Claude keep their own thread, where
// a failed turn may live on; they only get Resend. Mirrors the check in the
// DELETE /api/sessions/{id}/last-prompt route.
const REMOVABLE_PROVIDERS = new Set(['native'])

export function canRemovePrompt(provider) {
  return REMOVABLE_PROVIDERS.has(provider)
}

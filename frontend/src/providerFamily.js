// Which vendor logo a provider/model pair shows. Codex is OpenAI and Claude is
// Anthropic; Native follows its model id and has no logo for other vendors.

export function modelFamily(model = '') {
  if (/anthropic|claude|sonnet|haiku|opus|fable/i.test(model)) return 'anthropic'
  if (/openai|gpt|\bo[134]\b|luna|terra|\bsol\b|codex/i.test(model)) return 'openai'
  return ''
}

export function providerFamily(provider, model = '') {
  if (provider === 'claude-agent') return 'anthropic'
  if (provider === 'codex-app-server' || provider === 'codex-cli') return 'openai'
  return modelFamily(model)
}

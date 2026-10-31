// Which vendor logo a provider/model pair shows. Codex is OpenAI and Claude is
// Anthropic; Native follows its model id: the OpenRouter author before "/"
// (openai/gpt-6.1-terra -> openai), or a known model name when there is none.

// OpenRouter authors with a bundled logo, and aliases onto them.
const VENDORS = new Set([
  'openai', 'anthropic', 'google', 'deepseek', 'qwen', 'moonshotai', 'z-ai', 'mistralai',
  'meta', 'x-ai', 'minimax', 'nvidia', 'cohere', 'microsoft', 'amazon', 'tencent',
  'bytedance', 'perplexity', 'stepfun', 'ai21', 'inception', 'openrouter', 'xiaomi', 'nousresearch',
])
const ALIASES = { 'meta-llama': 'meta', 'bytedance-seed': 'bytedance', 'stepfun-ai': 'stepfun' }

// Bare model names (no author prefix), e.g. Codex and Claude model ids.
const NAME_PATTERNS = [
  [/anthropic|claude|sonnet|haiku|opus|fable/i, 'anthropic'],
  [/openai|gpt|\bo[134]\b|luna|terra|\bsol\b|codex/i, 'openai'],
  [/gemini|gemma/i, 'google'],
  [/deepseek/i, 'deepseek'],
  [/qwen|qwq/i, 'qwen'],
  [/kimi|moonshot/i, 'moonshotai'],
  [/\bglm\b|glm-/i, 'z-ai'],
  [/grok/i, 'x-ai'],
  [/minimax/i, 'minimax'],
]

export function modelFamily(model = '') {
  const text = String(model || '').trim()
  const slash = text.indexOf('/')
  if (slash > 0) {
    const author = text.slice(0, slash).replace(/^~/, '').toLowerCase()
    const vendor = ALIASES[author] || author
    if (VENDORS.has(vendor)) return vendor
    // An unknown author still names Claude/GPT models served elsewhere.
    const name = text.slice(slash + 1)
    if (/claude/i.test(name)) return 'anthropic'
    if (/\bgpt/i.test(name)) return 'openai'
    return ''
  }
  for (const [pattern, vendor] of NAME_PATTERNS) {
    if (pattern.test(text)) return vendor
  }
  return ''
}

export function providerFamily(provider, model = '') {
  if (provider === 'claude-agent') return 'anthropic'
  if (provider === 'codex-app-server' || provider === 'codex-cli') return 'openai'
  return modelFamily(model)
}

/** Initials for a vendor without a logo: "aion-labs/x" -> "AL". */
export function vendorInitials(model = '') {
  const author = String(model || '').split('/')[0].replace(/^~/, '')
  const words = author.split(/[-_.\s]+/).filter(Boolean)
  if (!words.length) return ''
  const letters = words.length > 1 ? words[0][0] + words[1][0] : words[0].slice(0, 2)
  return letters.toUpperCase()
}

import anthropicLogo from '../assets/providers/anthropic.jpeg'
import openaiLogo from '../assets/providers/openai.jpeg'
import { providerFamily } from '../providerFamily'

const LOGOS = { anthropic: anthropicLogo, openai: openaiLogo }

// Vendor logo for a provider, or for Native the vendor of its model. Native
// with a model from another vendor keeps the generic agent glyph.
export default function ProviderLogo({ provider, model = '', className = 'h-4 w-4' }) {
  const src = LOGOS[providerFamily(provider, model)]
  if (src) {
    return <img src={src} className={`${className} shrink-0 rounded-sm object-contain`} alt="" aria-hidden="true" />
  }
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="m12 2.8 8 4.6v9.2l-8 4.6-8-4.6V7.4l8-4.6Z" stroke="currentColor" strokeWidth="1.8" />
      <path d="M8.5 9.5h7v5h-7z" fill="currentColor" opacity=".7" />
    </svg>
  )
}

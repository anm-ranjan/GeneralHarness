import { providerFamily, vendorInitials } from '../providerFamily'

// Bundled vendor logos, keyed by file name: "deepseek.png" for one image,
// "x-ai-light.png" / "x-ai-dark.png" where the glyph must change with the theme.
const FILES = import.meta.glob('../assets/providers/*.{png,jpeg}', { eager: true, import: 'default' })
const LOGOS = {}
for (const [path, src] of Object.entries(FILES)) {
  const name = path.split('/').pop().replace(/\.(png|jpeg)$/, '')
  const themed = name.match(/^(.*)-(light|dark)$/)
  if (themed) LOGOS[themed[1]] = { ...LOGOS[themed[1]], [themed[2]]: src }
  else LOGOS[name] = { ...LOGOS[name], any: src }
}

// Vendor logo for a provider, or for Native the vendor of its model. A vendor
// without a bundled logo shows its initials; no model shows the agent glyph.
export default function ProviderLogo({ provider, model = '', className = 'h-4 w-4' }) {
  const logo = LOGOS[providerFamily(provider, model)]
  const imgClass = `${className} shrink-0 rounded-sm object-contain`
  if (logo?.any) return <img src={logo.any} className={imgClass} alt="" aria-hidden="true" />
  if (logo?.light || logo?.dark) {
    return (
      <>
        <img src={logo.light || logo.dark} className={`${imgClass} logo-light`} alt="" aria-hidden="true" />
        <img src={logo.dark || logo.light} className={`${imgClass} logo-dark`} alt="" aria-hidden="true" />
      </>
    )
  }
  const initials = provider === 'native' || !provider ? vendorInitials(model) : ''
  if (initials && model.includes('/')) {
    return (
      <span
        className={`${className} shrink-0 inline-flex items-center justify-center rounded-sm bg-surface-hover text-[8px] font-bold leading-none text-muted`}
        aria-hidden="true"
      >
        {initials}
      </span>
    )
  }
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="m12 2.8 8 4.6v9.2l-8 4.6-8-4.6V7.4l8-4.6Z" stroke="currentColor" strokeWidth="1.8" />
      <path d="M8.5 9.5h7v5h-7z" fill="currentColor" opacity=".7" />
    </svg>
  )
}

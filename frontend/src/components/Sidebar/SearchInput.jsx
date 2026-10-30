export default function SearchInput({ value, onChange }) {
  return (
    <input
      type="text"
      placeholder="Filter this list"
      aria-label="Filter threads by title or label"
      value={value}
      onChange={e => onChange(e.target.value)}
      className="w-full bg-transparent border-b border-line px-0.5 py-1.5 text-[13px] text-text-default placeholder:text-faint outline-none focus:border-accent transition-colors"
    />
  )
}

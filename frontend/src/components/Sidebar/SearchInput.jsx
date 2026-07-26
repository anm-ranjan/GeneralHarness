export default function SearchInput({ value, onChange }) {
  return (
    <input
      type="text"
      placeholder="Search sessions"
      value={value}
      onChange={e => onChange(e.target.value)}
      className="w-full bg-surface border border-line rounded-md px-3 py-2 text-[13px] text-text-default placeholder:text-faint outline-none focus:border-line-hover transition-colors"
    />
  )
}

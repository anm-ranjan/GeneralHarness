import logo from '../assets/logo.png'

// The app icon in place of the app name. The name stays as alt text, so
// screen readers and the heading outline still read it.
export default function BrandMark({ name, className = 'h-7 w-7' }) {
  return <img src={logo} alt={name} title={name} className={`${className} shrink-0 select-none`} draggable="false" />
}

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import 'katex/dist/katex.min.css'
import '@fontsource/atkinson-hyperlegible/latin-400.css'
import '@fontsource/atkinson-hyperlegible/latin-400-italic.css'
import '@fontsource/atkinson-hyperlegible/latin-700.css'
import '@fontsource-variable/bricolage-grotesque/wght.css'
import '@fontsource-variable/jetbrains-mono/wght.css'
import './index.css'
import { bootstrapTheme } from './theme'

// Applied before the first render so the saved theme never flashes the default.
bootstrapTheme()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>
)

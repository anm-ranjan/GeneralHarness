import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'
import { bootstrapTheme } from './theme'

// Applied before the first render so the saved theme never flashes the default.
bootstrapTheme()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>
)

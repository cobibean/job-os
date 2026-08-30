import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './app/App'
import './styles.css'
import '@jobos/docx-editor-core/editor.css'

const root = document.getElementById('root')
if (!root) throw new Error('Renderer root is missing')

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>
)

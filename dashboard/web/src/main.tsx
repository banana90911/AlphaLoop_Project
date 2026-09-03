/**
 * description:        진입점
 * author:             siheon jung
 * created date:       2026/09/04
 * remarks:
 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// Lancelot — A Governed Autonomous System
// Copyright (c) 2026 Myles Russell Hamilton
// Licensed under AGPL-3.0. See LICENSE for details.
// Patent Pending: US Provisional Application #63/982,183

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './styles/index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter basename="/war-room">
      <App />
    </BrowserRouter>
  </StrictMode>,
)

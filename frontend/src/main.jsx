// React entry point: mounts the top-level <App /> component into the
// #root div defined in index.html. StrictMode enables extra dev-only
// checks/warnings (it has no effect in production builds).
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
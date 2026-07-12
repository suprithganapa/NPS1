import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import ServerPage from './pages/ServerPage.jsx'
import ClientPage from './pages/ClientPage.jsx'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/server" replace />} />
        <Route path="/server" element={<ServerPage />} />
        <Route path="/client" element={<ClientPage />} />
      </Routes>
    </BrowserRouter>
  )
}

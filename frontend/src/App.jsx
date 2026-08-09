import { Navigate, Route, Routes } from 'react-router-dom'
import Login from './routes/Login'
import AdminPortal from './routes/AdminPortal'
import FacultyDashboard from './routes/FacultyDashboard'
import StudentDashboard from './routes/StudentDashboard'
import NotFound from './routes/NotFound'

function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/login" replace />} />
      <Route path="/login" element={<Login />} />
      <Route path="/admin" element={<AdminPortal />} />
      <Route path="/faculty" element={<FacultyDashboard />} />
      <Route path="/student" element={<StudentDashboard />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  )
}

export default App

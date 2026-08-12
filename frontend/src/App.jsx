import { Navigate, Route, Routes } from 'react-router-dom'
import Login from './routes/Login'
import AdminPortal from './routes/AdminPortal'
import AcademicHierarchy from './routes/admin/AcademicHierarchy'
import FacultyDashboard from './routes/FacultyDashboard'
import StudentDashboard from './routes/StudentDashboard'
import NotFound from './routes/NotFound'
import ProtectedRoute from './components/ProtectedRoute'

function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/login" replace />} />
      <Route path="/login" element={<Login />} />
      <Route
        path="/admin"
        element={
          <ProtectedRoute role="admin">
            <AdminPortal />
          </ProtectedRoute>
        }
      />
      <Route
        path="/admin/academics"
        element={
          <ProtectedRoute role="admin">
            <AcademicHierarchy />
          </ProtectedRoute>
        }
      />
      <Route
        path="/faculty"
        element={
          <ProtectedRoute role="faculty">
            <FacultyDashboard />
          </ProtectedRoute>
        }
      />
      <Route
        path="/student"
        element={
          <ProtectedRoute role="student">
            <StudentDashboard />
          </ProtectedRoute>
        }
      />
      <Route path="*" element={<NotFound />} />
    </Routes>
  )
}

export default App

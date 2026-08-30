import { Navigate, Route, Routes } from 'react-router-dom'
import Login from './routes/Login'
import AdminPortal from './routes/AdminPortal'
import AcademicHierarchy from './routes/admin/AcademicHierarchy'
import UserManagement from './routes/admin/UserManagement'
import FaceEnrollment from './routes/admin/FaceEnrollment'
import FacultyDashboard from './routes/FacultyDashboard'
import AttendanceCapture from './routes/faculty/AttendanceCapture'
import AttendanceHistory from './routes/faculty/AttendanceHistory'
import StudentDashboard from './routes/StudentDashboard'
import AttendanceOverview from './routes/student/AttendanceOverview'
import Account from './routes/Account'
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
        path="/admin/users"
        element={
          <ProtectedRoute role="admin">
            <UserManagement />
          </ProtectedRoute>
        }
      />
      <Route
        path="/admin/face-enrollment"
        element={
          <ProtectedRoute role="admin">
            <FaceEnrollment />
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
        path="/faculty/attendance"
        element={
          <ProtectedRoute role="faculty">
            <AttendanceCapture />
          </ProtectedRoute>
        }
      />
      <Route
        path="/faculty/attendance/history"
        element={
          <ProtectedRoute role="faculty">
            <AttendanceHistory />
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
      <Route
        path="/student/attendance"
        element={
          <ProtectedRoute role="student">
            <AttendanceOverview />
          </ProtectedRoute>
        }
      />
      {/* No `role` prop, which ProtectedRoute already reads as "any
          signed-in user": all three roles have equal standing over their
          own account. */}
      <Route
        path="/account"
        element={
          <ProtectedRoute>
            <Account />
          </ProtectedRoute>
        }
      />
      <Route path="*" element={<NotFound />} />
    </Routes>
  )
}

export default App

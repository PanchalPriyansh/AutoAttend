import { useAuth } from '../context/AuthContext'

function StudentDashboard() {
  const { user, logout } = useAuth()

  return (
    <div>
      <h1>Student Dashboard</h1>
      <p>Welcome, {user?.name}</p>
      <button onClick={logout}>Logout</button>
    </div>
  )
}

export default StudentDashboard

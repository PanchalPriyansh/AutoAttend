import { useAuth } from '../context/AuthContext'

function FacultyDashboard() {
  const { user, logout } = useAuth()

  return (
    <div>
      <h1>Faculty Dashboard</h1>
      <p>Welcome, {user?.name}</p>
      <button onClick={logout}>Logout</button>
    </div>
  )
}

export default FacultyDashboard

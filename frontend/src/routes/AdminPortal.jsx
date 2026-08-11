import { useAuth } from '../context/AuthContext'

function AdminPortal() {
  const { user, logout } = useAuth()

  return (
    <div>
      <h1>Admin Portal</h1>
      <p>Welcome, {user?.name}</p>
      <button onClick={logout}>Logout</button>
    </div>
  )
}

export default AdminPortal

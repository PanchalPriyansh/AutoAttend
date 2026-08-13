import { Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

function AdminPortal() {
  const { user, logout } = useAuth()

  return (
    <div>
      <h1>Admin Portal</h1>
      <p>Welcome, {user?.name}</p>
      <nav>
        <Link to="/admin/academics">Manage academic hierarchy</Link>
        {' · '}
        <Link to="/admin/users">Manage users</Link>
        {' · '}
        <Link to="/admin/face-enrollment">Face enrollment</Link>
      </nav>
      <button onClick={logout}>Logout</button>
    </div>
  )
}

export default AdminPortal

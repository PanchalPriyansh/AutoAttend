import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

function Login() {
  const { user, loading, login } = useAuth()
  const navigate = useNavigate()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  if (!loading && user) {
    return <Navigate to={`/${user.role}`} replace />
  }

  async function handleSubmit(event) {
    event.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      const profile = await login(email, password)
      navigate(`/${profile.role}`, { replace: true })
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="auth-screen">
      <main className="auth-card card">
        <p className="auth-brand">AutoAttend</p>
        <h1 className="auth-title">Login</h1>
        <form className="auth-form" onSubmit={handleSubmit}>
          <div className="form-field form-field--lg">
            <label htmlFor="email">Email</label>
            <input
              id="email"
              name="email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="username"
              spellCheck={false}
              required
            />
          </div>
          <div className="form-field form-field--lg">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              name="password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
          </div>
          {error && (
            <p className="callout callout--error" role="alert">
              <span className="callout-mark" aria-hidden="true">
                !
              </span>
              {error}
            </p>
          )}
          <button
            type="submit"
            className="btn btn--primary btn--lg auth-submit"
            disabled={submitting}
          >
            {submitting ? 'Logging in…' : 'Log in'}
          </button>
        </form>
        <p className="auth-note">
          Accounts are created by your college administrator. AutoAttend has no
          public sign-up — if you cannot sign in, contact them.
        </p>
      </main>
    </div>
  )
}

export default Login

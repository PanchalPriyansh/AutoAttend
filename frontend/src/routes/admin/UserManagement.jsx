import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { institutes as institutesApi } from '../../api/academic'
import {
  createUser,
  listUsers,
  setUserPassword,
  setUserStatus,
  updateUser,
} from '../../api/users'
import ConfirmDialog from '../../components/admin/ConfirmDialog'
import UserForm from '../../components/admin/UserForm'
import { useAuth } from '../../context/AuthContext'

const ROLE_FILTERS = ['', 'admin', 'faculty', 'student']

function UserManagement() {
  const { user: currentUser } = useAuth()

  const [users, setUsers] = useState([])
  const [institutes, setInstitutes] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [reloadToken, setReloadToken] = useState(0)

  // `filters` is what has actually been applied; `search` is the uncommitted
  // text box. Searching on submit rather than per keystroke keeps one request
  // per intent -- the endpoint has no rate limiting behind it.
  const [filters, setFilters] = useState({ role: '', q: '' })
  const [search, setSearch] = useState('')

  const [creating, setCreating] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [resettingId, setResettingId] = useState(null)
  const [newPassword, setNewPassword] = useState('')
  const [pendingDeactivation, setPendingDeactivation] = useState(null)
  const [pending, setPending] = useState(false)
  const [actionError, setActionError] = useState('')
  const [notice, setNotice] = useState('')

  const refresh = useCallback(() => setReloadToken((token) => token + 1), [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')

    listUsers(filters)
      .then((rows) => {
        if (!cancelled) setUsers(rows)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err.message)
        setUsers([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [filters, reloadToken])

  useEffect(() => {
    let cancelled = false
    // Institutes populate the form's dropdown. A failure here is not fatal to
    // the directory, so it only empties the optional picker.
    institutesApi
      .list()
      .then((rows) => {
        if (!cancelled) setInstitutes(rows)
      })
      .catch(() => {
        if (!cancelled) setInstitutes([])
      })

    return () => {
      cancelled = true
    }
  }, [])

  /** Runs a mutation, surfacing the backend's message inline on failure. */
  const runAction = useCallback(async (action) => {
    setActionError('')
    setNotice('')
    setPending(true)
    try {
      await action()
      return true
    } catch (err) {
      setActionError(err.message)
      return false
    } finally {
      setPending(false)
    }
  }, [])

  async function handleCreate(payload) {
    await createUser(payload)
    setCreating(false)
    setNotice('User created.')
    refresh()
  }

  async function handleUpdate(payload) {
    await updateUser(editingId, payload)
    setEditingId(null)
    setNotice('User updated.')
    refresh()
  }

  async function handleResetPassword(event) {
    event.preventDefault()
    const id = resettingId
    const ok = await runAction(() => setUserPassword(id, newPassword))
    if (ok) {
      // Cleared immediately and never echoed back into the field.
      setNewPassword('')
      setResettingId(null)
      setNotice('Password updated.')
    }
  }

  async function handleToggleStatus(target, isActive) {
    const ok = await runAction(() => setUserStatus(target.id, isActive))
    if (ok) {
      setNotice(isActive ? 'Account reactivated.' : 'Account deactivated.')
      refresh()
    }
  }

  function startEditing(target) {
    setActionError('')
    setNotice('')
    setResettingId(null)
    setCreating(false)
    setEditingId(target.id)
  }

  function startResetting(target) {
    setActionError('')
    setNotice('')
    setEditingId(null)
    setNewPassword('')
    setResettingId(target.id)
  }

  function applySearch(event) {
    event.preventDefault()
    setFilters((current) => ({ ...current, q: search }))
  }

  return (
    <div>
      <h1>Users</h1>
      <p>
        <Link to="/admin">← Back to Admin Portal</Link>
      </p>
      <p className="hierarchy-hint">
        Accounts are created here — there is no public sign-up. Accounts are deactivated rather
        than deleted, so a user’s enrollment and attendance history stays intact.
      </p>

      <section className="hierarchy-level" aria-labelledby="users-heading">
        <header className="hierarchy-header">
          <h2 id="users-heading">Directory</h2>
          <button
            type="button"
            onClick={() => {
              setEditingId(null)
              setResettingId(null)
              setCreating((open) => !open)
            }}
            disabled={pending}
          >
            {creating ? 'Cancel' : '+ Add user'}
          </button>
        </header>

        <form className="hierarchy-form" onSubmit={applySearch}>
          <span className="field">
            <label htmlFor="filter-role">Role</label>
            <select
              id="filter-role"
              value={filters.role}
              onChange={(event) =>
                setFilters((current) => ({ ...current, role: event.target.value }))
              }
            >
              {ROLE_FILTERS.map((role) => (
                <option key={role || 'all'} value={role}>
                  {role || 'All roles'}
                </option>
              ))}
            </select>
          </span>
          <span className="field">
            <label htmlFor="filter-q">Search name or email</label>
            <input
              id="filter-q"
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </span>
          <button type="submit" disabled={loading}>
            Search
          </button>
        </form>

        {creating && (
          <UserForm
            mode="create"
            institutes={institutes}
            onSubmit={handleCreate}
            onCancel={() => setCreating(false)}
          />
        )}

        {error && (
          <p role="alert" className="hierarchy-error">
            {error}
          </p>
        )}
        {actionError && (
          <p role="alert" className="hierarchy-error">
            {actionError}
          </p>
        )}
        {notice && <p className="hierarchy-hint">{notice}</p>}

        {loading && <p className="hierarchy-hint">Loading…</p>}

        {!loading && !error && users.length === 0 && (
          <p className="hierarchy-hint">No users match these filters.</p>
        )}

        <ul className="hierarchy-items">
          {users.map((row) => {
            const isSelf = row.id === currentUser?.id

            if (editingId === row.id) {
              return (
                <li key={row.id}>
                  <UserForm
                    mode="edit"
                    user={row}
                    institutes={institutes}
                    onSubmit={handleUpdate}
                    onCancel={() => setEditingId(null)}
                  />
                </li>
              )
            }

            return (
              <li key={row.id} className={row.is_active ? undefined : 'is-inactive'}>
                <span className="user-identity">
                  <span className="user-name">{row.name}</span>
                  <span className="hierarchy-hint">
                    {row.email} · {row.role}
                    {!row.is_active && ' · Deactivated'}
                  </span>
                </span>

                <button type="button" onClick={() => startEditing(row)} disabled={pending}>
                  Edit
                </button>
                <button type="button" onClick={() => startResetting(row)} disabled={pending}>
                  Reset password
                </button>
                {row.is_active ? (
                  <button
                    type="button"
                    className="danger"
                    onClick={() => {
                      setActionError('')
                      setNotice('')
                      setPendingDeactivation(row)
                    }}
                    // The backend rejects this too (409); disabling it here
                    // just avoids offering an action that cannot succeed.
                    disabled={pending || isSelf}
                    title={isSelf ? 'You cannot deactivate your own account' : undefined}
                  >
                    Deactivate
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => handleToggleStatus(row, true)}
                    disabled={pending}
                  >
                    Reactivate
                  </button>
                )}

                {resettingId === row.id && (
                  <form className="hierarchy-form" onSubmit={handleResetPassword}>
                    <span className="field">
                      <label htmlFor={`reset-${row.id}`}>New password</label>
                      <input
                        id={`reset-${row.id}`}
                        type="password"
                        value={newPassword}
                        onChange={(event) => setNewPassword(event.target.value)}
                        autoComplete="new-password"
                        required
                      />
                    </span>
                    <button type="submit" disabled={pending}>
                      {pending ? 'Saving…' : 'Set password'}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setNewPassword('')
                        setResettingId(null)
                      }}
                      disabled={pending}
                    >
                      Cancel
                    </button>
                  </form>
                )}
              </li>
            )
          })}
        </ul>

        <ConfirmDialog
          open={pendingDeactivation !== null}
          title="Deactivate account?"
          confirmLabel="Deactivate"
          message={
            pendingDeactivation
              ? `${pendingDeactivation.name} will no longer be able to log in. Their enrollments and history are kept, and the account can be reactivated later.`
              : ''
          }
          pending={pending}
          onConfirm={async () => {
            const target = pendingDeactivation
            await handleToggleStatus(target, false)
            // Closed either way: on failure the backend's reason is shown in
            // the list, where it would otherwise sit hidden behind the dialog.
            setPendingDeactivation(null)
          }}
          onCancel={() => setPendingDeactivation(null)}
        />
      </section>
    </div>
  )
}

export default UserManagement

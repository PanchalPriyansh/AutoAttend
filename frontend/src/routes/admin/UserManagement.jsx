import { useCallback, useEffect, useState } from 'react'
import { institutes as institutesApi } from '../../api/academic'
import {
  createUser,
  listUsers,
  setUserPassword,
  setUserStatus,
  updateUser,
} from '../../api/users'
import PasswordInput from '../../components/PasswordInput'
import AppShell from '../../components/layout/AppShell'
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
    <AppShell title="Users">
      <p className="au-intro">
        Accounts are created here — there is no public sign-up. Accounts are deactivated rather
        than deleted, so a user’s enrollment and attendance history stays intact.
      </p>

      <section className="au-panel" aria-labelledby="users-heading">
        <header className="au-head">
          <h2 id="users-heading" className="au-eyebrow">
            Directory
          </h2>
          <button
            type="button"
            className="btn btn--secondary au-add"
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

        <form className="au-filters" onSubmit={applySearch}>
          <span className="form-field au-field au-field--role">
            <label htmlFor="filter-role">Role</label>
            <select
              id="filter-role"
              name="role"
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
          <span className="form-field au-field au-field--search">
            <label htmlFor="filter-q">Search name or email</label>
            <input
              id="filter-q"
              name="q"
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
          </span>
          <button type="submit" className="btn btn--secondary au-submit" disabled={loading}>
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
          <p role="alert" className="callout callout--error au-alert">
            <span className="callout-mark" aria-hidden="true">
              !
            </span>
            {error}
          </p>
        )}
        {actionError && (
          <p role="alert" className="callout callout--error au-alert">
            <span className="callout-mark" aria-hidden="true">
              !
            </span>
            {actionError}
          </p>
        )}
        {/* Always rendered, so the region exists before its content
            changes -- a live region inserted at the same moment as its
            text is announced unreliably. The two errors above stay
            outside it: role="alert" already announces them, and more
            assertively than this should. */}
        <div aria-live="polite">
          {notice && (
            <p className="callout callout--success au-alert">
              <span className="callout-mark" aria-hidden="true">
                ✓
              </span>
              {notice}
            </p>
          )}

          {loading && <p className="au-loading">Loading…</p>}

          {!loading && !error && users.length === 0 && (
            <p className="au-empty">No users match these filters.</p>
          )}
        </div>

        <ul className="au-items">
          {users.map((row) => {
            const isSelf = row.id === currentUser?.id

            if (editingId === row.id) {
              return (
                <li key={row.id} className="au-editing">
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

            // Two data-driven marks, both read from state that already
            // existed: a deactivated account, and the row whose password
            // form is open. No new state, no changed handler.
            const rowClass = [
              'au-row',
              row.is_active ? '' : 'au-row--inactive',
              resettingId === row.id ? 'au-row--open' : '',
            ]
              .filter(Boolean)
              .join(' ')

            return (
              <li key={row.id} className={rowClass}>
                <span className="au-identity">
                  <span className="au-name">{row.name}</span>
                  <span className="au-meta">
                    <span className="au-email">{row.email}</span>
                    <span className="au-role">{row.role}</span>
                    {!row.is_active && <span className="pill pill--neutral au-flag">Deactivated</span>}
                  </span>
                </span>

                <button
                  type="button"
                  className="btn btn--secondary au-action"
                  onClick={() => startEditing(row)}
                  disabled={pending}
                >
                  Edit
                </button>
                <button
                  type="button"
                  className="btn btn--secondary au-action"
                  onClick={() => startResetting(row)}
                  disabled={pending}
                >
                  Reset password
                </button>
                {row.is_active ? (
                  <button
                    type="button"
                    className="btn btn--danger au-action"
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
                    className="btn btn--secondary au-action"
                    onClick={() => handleToggleStatus(row, true)}
                    disabled={pending}
                  >
                    Reactivate
                  </button>
                )}

                {resettingId === row.id && (
                  <form className="au-reset" onSubmit={handleResetPassword}>
                    <span className="form-field au-field au-field--password">
                      <label htmlFor={`reset-${row.id}`}>New password</label>
                      <PasswordInput
                        id={`reset-${row.id}`}
                        name="new_password"
                        value={newPassword}
                        onChange={(event) => setNewPassword(event.target.value)}
                        autoComplete="new-password"
                        required
                      />
                    </span>
                    <button type="submit" className="btn btn--primary au-submit" disabled={pending}>
                      {pending ? 'Saving…' : 'Set password'}
                    </button>
                    <button
                      type="button"
                      className="btn btn--secondary au-submit"
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
    </AppShell>
  )
}

export default UserManagement

import { useState } from 'react'
import ConfirmDialog from './ConfirmDialog'

function emptyValues(fields) {
  return Object.fromEntries(fields.map((field) => [field.name, '']))
}

function valuesFromItem(fields, item) {
  return Object.fromEntries(
    fields.map((field) => {
      const value = item[field.name] ?? ''
      // Date inputs need a bare yyyy-mm-dd; the API returns full ISO
      // timestamps with an offset.
      return [field.name, field.type === 'date' ? String(value).slice(0, 10) : value]
    }),
  )
}

function itemLabel(item) {
  return item.code ? `${item.name} (${item.code})` : item.name
}

/**
 * One level of the academic hierarchy: its items, an inline create form,
 * and rename/delete actions. All five levels differ only in their field
 * descriptors, so this component is driven by `fields` rather than
 * written out once per level.
 *
 * Errors from the API are rendered verbatim -- the backend already
 * returns admin-readable messages for conflicts and blocked deletes.
 */
function HierarchyLevel({
  title,
  noun,
  fields,
  items,
  loading,
  error,
  disabled,
  disabledHint,
  selectedId,
  onSelect,
  onCreate,
  onUpdate,
  onDelete,
}) {
  const [creating, setCreating] = useState(false)
  const [createValues, setCreateValues] = useState(() => emptyValues(fields))
  const [editingId, setEditingId] = useState(null)
  const [editValues, setEditValues] = useState({})
  const [pendingItem, setPendingItem] = useState(null)
  const [pending, setPending] = useState(false)
  const [actionError, setActionError] = useState('')

  function resetCreateForm() {
    setCreateValues(emptyValues(fields))
    setCreating(false)
  }

  async function runAction(action) {
    setActionError('')
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
  }

  async function handleCreate(event) {
    event.preventDefault()
    if (await runAction(() => onCreate(createValues))) resetCreateForm()
  }

  async function handleUpdate(event) {
    event.preventDefault()
    if (await runAction(() => onUpdate(editingId, editValues))) setEditingId(null)
  }

  async function handleDelete() {
    const item = pendingItem
    await runAction(() => onDelete(item.id))
    // Closed either way: on failure the backend's reason (e.g. "Cannot
    // delete: 3 departments belong to this institute") is shown in the
    // level itself, where it would otherwise sit hidden behind the dialog.
    setPendingItem(null)
  }

  function startEditing(item) {
    setActionError('')
    setEditingId(item.id)
    setEditValues(valuesFromItem(fields, item))
  }

  function renderFields(values, setValues, idPrefix) {
    return fields.map((field) => (
      <span key={field.name} className="form-field ah-field">
        <label htmlFor={`${idPrefix}-${field.name}`}>{field.label}</label>
        <input
          id={`${idPrefix}-${field.name}`}
          type={field.type === 'date' ? 'date' : 'text'}
          value={values[field.name] ?? ''}
          onChange={(event) => setValues({ ...values, [field.name]: event.target.value })}
          required
        />
      </span>
    ))
  }

  return (
    <section
      className={`ah-level${disabled ? ' ah-level--locked' : ''}`}
      aria-labelledby={`${noun}-heading`}
    >
      <header className="ah-head">
        <h2 id={`${noun}-heading`} className="ah-eyebrow">
          {title}
        </h2>
        {!disabled && (
          <button
            type="button"
            className="btn btn--secondary ah-add"
            onClick={() => setCreating((open) => !open)}
            disabled={pending}
          >
            {creating ? 'Cancel' : `+ Add ${noun}`}
          </button>
        )}
      </header>

      {disabled ? (
        <p className="ah-locked">{disabledHint}</p>
      ) : (
        <>
          {creating && (
            <form className="ah-form" onSubmit={handleCreate}>
              {renderFields(createValues, setCreateValues, `new-${noun}`)}
              <button type="submit" className="btn btn--primary ah-submit" disabled={pending}>
                {pending ? 'Saving…' : 'Save'}
              </button>
            </form>
          )}

          {error && (
            <p role="alert" className="callout callout--error ah-alert">
              <span className="callout-mark" aria-hidden="true">
                !
              </span>
              {error}
            </p>
          )}
          {actionError && (
            <p role="alert" className="callout callout--error ah-alert">
              <span className="callout-mark" aria-hidden="true">
                !
              </span>
              {actionError}
            </p>
          )}

          {loading && <p className="ah-loading">Loading…</p>}

          {!loading && !error && items.length === 0 && (
            <p className="ah-empty">No {title.toLowerCase()} yet.</p>
          )}

          <ul className="ah-items">
            {items.map((item) => (
              <li
                key={item.id}
                className={`ah-item${item.id === selectedId ? ' ah-item--selected' : ''}`}
              >
                {editingId === item.id ? (
                  <form className="ah-form ah-form--edit" onSubmit={handleUpdate}>
                    {renderFields(editValues, setEditValues, `edit-${item.id}`)}
                    <button type="submit" className="btn btn--primary ah-submit" disabled={pending}>
                      {pending ? 'Saving…' : 'Save'}
                    </button>
                    <button
                      type="button"
                      className="btn btn--secondary ah-submit"
                      onClick={() => setEditingId(null)}
                      disabled={pending}
                    >
                      Cancel
                    </button>
                  </form>
                ) : (
                  <>
                    <button
                      type="button"
                      className="ah-pick"
                      aria-pressed={item.id === selectedId}
                      onClick={() => onSelect(item.id)}
                    >
                      {itemLabel(item)}
                    </button>
                    <button
                      type="button"
                      className="btn btn--secondary ah-action"
                      onClick={() => startEditing(item)}
                      disabled={pending}
                    >
                      Rename
                    </button>
                    <button
                      type="button"
                      className="btn btn--danger ah-action"
                      onClick={() => {
                        setActionError('')
                        setPendingItem(item)
                      }}
                      disabled={pending}
                    >
                      Delete
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        </>
      )}

      <ConfirmDialog
        open={pendingItem !== null}
        title={`Delete ${noun}?`}
        message={
          pendingItem
            ? `"${itemLabel(pendingItem)}" will be permanently removed. This cannot be undone.`
            : ''
        }
        pending={pending}
        onConfirm={handleDelete}
        onCancel={() => setPendingItem(null)}
      />
    </section>
  )
}

export default HierarchyLevel

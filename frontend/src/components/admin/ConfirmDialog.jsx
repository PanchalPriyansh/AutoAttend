import { useEffect, useRef } from 'react'

/**
 * Confirmation prompt shown before a destructive action, so deleting part
 * of the academic hierarchy is never a single accidental click.
 */
function ConfirmDialog({ open, title, message, confirmLabel = 'Delete', pending, onConfirm, onCancel }) {
  const cancelRef = useRef(null)

  useEffect(() => {
    if (open) cancelRef.current?.focus()
  }, [open])

  useEffect(() => {
    if (!open) return undefined

    function handleKeyDown(event) {
      if (event.key === 'Escape' && !pending) onCancel()
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [open, pending, onCancel])

  if (!open) return null

  return (
    <div className="dialog-backdrop">
      <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title">
        <h2 id="confirm-title">{title}</h2>
        <p>{message}</p>
        <div className="dialog-actions">
          <button type="button" ref={cancelRef} onClick={onCancel} disabled={pending}>
            Cancel
          </button>
          <button type="button" className="danger" onClick={onConfirm} disabled={pending}>
            {pending ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

export default ConfirmDialog

/**
 * Splitting a bulk-import selection into requests that fit.
 *
 * Pure — no DOM, no fetch, no React. It sits in utils/ beside download.js
 * and lecture.js for the same reason those do: it is neither HTTP nor
 * markup, and it is the part of the import worth reasoning about on its
 * own.
 *
 * Batching is transport convenience and nothing more. The server enforces
 * its own file-count cap and its own per-image ceiling and re-derives
 * every one of them from the request it actually receives; nothing here
 * is a check, and a client that packed badly gets a 400 or a 413 rather
 * than a silently wrong import. What batching buys is that each request
 * stays short enough to report progress between, cheap enough to retry,
 * and small enough not to be refused whole.
 */

// Mirrors MAX_IMPORT_FILES in backend/recognition/validators.py.
export const MAX_IMPORT_FILES = 10

// Deliberately under the backend's MAX_REQUEST_BYTES (26 MB), not equal
// to it: a multipart body carries boundary framing and per-part headers
// on top of the bytes themselves, so a batch packed exactly to the
// ceiling arrives above it and is refused as a 413. 20 MB leaves room
// for that framing and for the ceiling to stay where it is — the guard
// on every other route is not this feature's to relax.
export const MAX_BATCH_BYTES = 20 * 1024 * 1024

/**
 * Split `files` into batches respecting both bounds, preserving order.
 *
 * A single file larger than `maxBytes` gets a batch of its own rather
 * than being dropped or silently skipped: the server owns the size rule,
 * and it is the one that can say "this photo is too large" in the words
 * the rest of the page shows errors in. Hiding it here would leave the
 * admin with a file that never appears in any report.
 */
export function packBatches(files, { maxFiles = MAX_IMPORT_FILES, maxBytes = MAX_BATCH_BYTES } = {}) {
  const batches = []
  let current = []
  let currentBytes = 0

  for (const file of files) {
    const size = file.size ?? 0
    const wouldExceed = current.length > 0 && (current.length >= maxFiles || currentBytes + size > maxBytes)

    if (wouldExceed) {
      batches.push(current)
      current = []
      currentBytes = 0
    }

    current.push(file)
    currentBytes += size
  }

  if (current.length > 0) batches.push(current)

  return batches
}

/** Total bytes of a selection, for the "12 photos · 8.4 MB" line. */
export function totalBytes(files) {
  return files.reduce((sum, file) => sum + (file.size ?? 0), 0)
}

/**
 * A size a person reads, not a number of bytes. One decimal place below
 * 10 MB and none above it, so the figure stays the same width as it
 * grows and the line does not reflow while files are being added.
 */
export function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`

  const kb = bytes / 1024
  if (kb < 1024) return `${Math.round(kb)} KB`

  const mb = kb / 1024
  return mb < 10 ? `${mb.toFixed(1)} MB` : `${Math.round(mb)} MB`
}

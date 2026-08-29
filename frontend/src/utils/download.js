/**
 * Handing a fetched file to the browser to save.
 *
 * DOM rather than HTTP, which is why it lives here and not in api/. The
 * request itself is an ordinary authenticated fetch — see
 * `downloadAttendanceExport` in api/attendance.js — and this only deals
 * with what to do once the bytes have arrived.
 *
 * A plain `<a href>` pointing at the endpoint would be simpler and is
 * wrong: it sends no X-CSRF-TOKEN, cannot transparently refresh an
 * expired access token, and turns a 403 into a browser error page
 * instead of a message on the screen the user is looking at.
 */

/**
 * Save `blob` to the user's downloads as `filename`.
 *
 * The object URL is revoked immediately after the click. The browser has
 * already taken its own reference by then, so the download completes;
 * leaving it un-revoked would pin the whole file in memory for the life
 * of the page, which for a term of attendance is not nothing.
 */
export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')

  link.href = url
  link.download = filename
  // Firefox will not act on a click for a link that is not in the
  // document; Chrome and Safari do not care.
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)

  URL.revokeObjectURL(url)
}

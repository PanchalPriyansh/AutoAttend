import { useRef, useState } from 'react'
import { importClassFaceEncodings } from '../../api/faces'
import { formatBytes, packBatches, totalBytes } from '../../utils/fileBatches'

/**
 * Enrol a whole class from a folder of photos named after the students.
 *
 * The panel sends the files and nothing else. It never tells the server
 * which student a photo is of — the server resolves that from the class
 * roster, and this component only *displays* the answer. That split is
 * the point of the feature: a mis-parse would attribute one person's
 * face, and their attendance for as long as the encoding survives, to
 * another.
 *
 * A selection is sent as several sequential requests (see
 * utils/fileBatches.js), one at a time so progress can be reported
 * between them and so one failure costs one batch rather than the whole
 * import. When a batch does fail the results already collected stay on
 * screen and the panel says how many files were never attempted — an
 * import that loses its own report is worse than one that stops early.
 *
 * Partial success is the normal outcome here, not an error: a folder of
 * sixty photos will contain a blurry one, a group shot and a typo, and
 * every file is reported on its own.
 *
 * Styled by styles/admin-face-enrollment.css (.fe-import*), which is this
 * page's own file — the panel is rendered by one page, so it composes
 * that page's row shape rather than introducing a layout of its own.
 */

// The order the report reads in: what worked, then what needs a decision,
// then what the server could not place. Grouping beats submitted order
// once there are sixty rows -- the admin acts on the failures.
const GROUPS = [
  { status: 'registered', title: 'Registered', tone: 'success' },
  { status: 'rejected', title: 'Not registered', tone: 'warning' },
  { status: 'ambiguous', title: 'Ambiguous file names', tone: 'warning' },
  { status: 'no_match', title: 'No matching student', tone: 'neutral' },
]

function ResultRow({ result }) {
  const student = result.student

  return (
    <li className="fe-row fe-import-result">
      <span className="fe-identity">
        <span className="fe-name">{student ? student.name : result.filename}</span>
        {/* When no student was resolved the file name IS the heading, so
            repeating it in the metadata says the same thing twice. It
            earns its place only beside a student: there it is what the
            admin renames to fix a failure, and how two photos of one
            person are told apart. */}
        {student && (
          <span className="fe-meta">
            {/* Whitespace between them for the same reason the group
                heading carries it: the flex gap separates these on
                screen but leaves the row reading as
                "…@charusat.edu.in24DCS109.jpg" to anything consuming
                the text. */}
            <span className="fe-email">{student.email}</span>{' '}
            <span className="fe-import-file">{result.filename}</span>
          </span>
        )}
        {result.message && <span className="fe-import-message">{result.message}</span>}
      </span>
    </li>
  )
}

function BulkFaceImport({ classId, onImported, disabled }) {
  const [files, setFiles] = useState([])
  const [running, setRunning] = useState(false)
  const [completed, setCompleted] = useState(0)
  const [results, setResults] = useState([])
  const [error, setError] = useState('')
  const [notAttempted, setNotAttempted] = useState(0)
  const inputRef = useRef(null)

  const busy = running || disabled

  function handleSelect(event) {
    setFiles(Array.from(event.target.files || []))
    setResults([])
    setError('')
    setNotAttempted(0)
    setCompleted(0)
  }

  function reset() {
    setFiles([])
    setResults([])
    setError('')
    setNotAttempted(0)
    setCompleted(0)
    // The input keeps its own value, so clearing state alone would leave
    // the control showing files this panel has forgotten -- and picking
    // the same folder again would fire no change event.
    if (inputRef.current) inputRef.current.value = ''
  }

  async function runImport() {
    setRunning(true)
    setError('')
    setNotAttempted(0)
    setResults([])
    setCompleted(0)

    const batches = packBatches(files)
    const collected = []
    let done = 0

    try {
      for (const batch of batches) {
        const { results: batchResults } = await importClassFaceEncodings(classId, batch)
        collected.push(...batchResults)
        done += batch.length
        // Committed to state per batch rather than at the end, so a
        // failure three batches in still leaves the first two on screen.
        setResults([...collected])
        setCompleted(done)
      }
    } catch (err) {
      setError(err.message)
      setNotAttempted(files.length - done)
    } finally {
      setRunning(false)
      // Refreshed even after a failure: earlier batches may have
      // registered samples, so the roster's counts are stale either way.
      if (done > 0) onImported?.()
    }
  }

  const selectedBytes = totalBytes(files)
  const grouped = GROUPS.map((group) => ({
    ...group,
    rows: results.filter((result) => result.status === group.status),
  })).filter((group) => group.rows.length > 0)

  return (
    <section className="fe-import" aria-labelledby="bulk-import-heading">
      <h3 id="bulk-import-heading" className="fe-subtitle">
        Import many photos
      </h3>

      <p className="fe-guide">
        Name each photo with the student&apos;s ID —{' '}
        <code className="fe-import-example">24DCS001.jpg</code>. Photos are matched against
        this class&apos;s students only, and nothing is stored but the numeric encoding.
        Photos are never saved.
      </p>

      <div className="fe-import-controls">
        <span className="form-field fe-field fe-file">
          <label htmlFor="bulk-face-images">Choose photos</label>
          <input
            id="bulk-face-images"
            name="bulk_face_images"
            ref={inputRef}
            type="file"
            multiple
            accept="image/jpeg,image/png,image/webp"
            onChange={handleSelect}
            disabled={busy}
          />
        </span>

        <button
          type="button"
          className="btn btn--primary fe-btn"
          onClick={runImport}
          disabled={busy || files.length === 0}
        >
          {running ? 'Importing…' : 'Import photos'}
        </button>

        {files.length > 0 && !running && (
          <button type="button" className="btn btn--secondary fe-btn" onClick={reset}>
            Clear
          </button>
        )}
      </div>

      {error && (
        <p role="alert" className="callout callout--error fe-alert">
          <span className="callout-mark" aria-hidden="true">
            !
          </span>
          {error}
          {notAttempted > 0 && ` ${notAttempted} of ${files.length} photos were not attempted.`}
        </p>
      )}

      {/* Always rendered so the region exists before its content changes:
          a live region inserted at the same moment as its text is
          announced unreliably. Same reasoning as the roster panel's. */}
      <div aria-live="polite">
        {files.length > 0 && !running && results.length === 0 && (
          <p className="fe-import-status">
            {files.length} photo{files.length === 1 ? '' : 's'} selected ·{' '}
            {formatBytes(selectedBytes)}
          </p>
        )}

        {running && (
          <p className="fe-import-status">
            Importing… {completed} of {files.length} photos done.
          </p>
        )}

        {!running && results.length > 0 && (
          <p className="fe-import-status">
            {results.filter((result) => result.status === 'registered').length} of{' '}
            {results.length} photos registered.
          </p>
        )}
      </div>

      {grouped.map((group) => (
        <div className="fe-import-group" key={group.status}>
          <h4 className="fe-import-group-title">
            <span className={`pill pill--${group.tone} fe-import-count`}>
              {group.rows.length}
            </span>{' '}
            {/* A real space, not just the flex gap: the gap separates the
                count from the word on screen but leaves the heading's
                text content as "12Registered" for anything reading it. */}
            {group.title}
          </h4>
          <ul className="fe-items">
            {group.rows.map((result, index) => (
              <ResultRow key={`${group.status}-${result.filename}-${index}`} result={result} />
            ))}
          </ul>
        </div>
      ))}
    </section>
  )
}

export default BulkFaceImport

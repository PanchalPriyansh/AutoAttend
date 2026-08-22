import useCamera from '../../hooks/useCamera'

/**
 * Two ways to produce one enrolment image: pick a file, or take a photo with
 * the webcam. Both hand the result to `onCapture(image, source)` — a File for
 * an upload, a Blob for a capture — and the parent decides what to do with it.
 *
 * This component never uploads anything itself and never keeps the image: the
 * Blob is passed straight out and the canvas it came from is discarded.
 *
 * The camera is optional in the strong sense — a browser without
 * `mediaDevices` (any page not served over HTTPS or localhost) and a denied
 * permission both leave the file input fully working, because an admin
 * enrolling from a saved photo should not be blocked by a webcam they were
 * never going to use. The stream lifecycle itself lives in useCamera, shared
 * with the classroom capture control.
 */
function FaceCapture({ onCapture, disabled }) {
  const { videoRef, active, error, start, stop, capturePhoto } = useCamera()

  async function takePhoto() {
    const blob = await capturePhoto()
    if (blob) onCapture(blob, 'camera')
  }

  function handleFile(event) {
    const file = event.target.files?.[0]
    // Cleared so picking the same file twice still fires a change event.
    event.target.value = ''
    if (file) onCapture(file, 'upload')
  }

  return (
    <div className="face-capture">
      {error && (
        <p role="alert" className="hierarchy-error">
          {error}
        </p>
      )}

      <p className="hierarchy-hint">
        Face the camera directly, head and shoulders in frame, in even front-facing
        light. Avoid sunglasses, glare on glasses, and a busy or reflective background.
        The photo is not stored — only the numeric encoding derived from it.
      </p>

      <div className="hierarchy-form">
        <span className="field">
          <label htmlFor="face-image">Upload a photo</label>
          <input
            id="face-image"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            onChange={handleFile}
            disabled={disabled}
          />
        </span>

        {!active && (
          <button type="button" onClick={start} disabled={disabled}>
            Use camera
          </button>
        )}
      </div>

      {active && (
        <div className="face-capture-live">
          <video ref={videoRef} autoPlay playsInline muted />
          <div className="hierarchy-form">
            <button type="button" onClick={takePhoto} disabled={disabled}>
              Take photo
            </button>
            <button type="button" onClick={stop}>
              Stop camera
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default FaceCapture

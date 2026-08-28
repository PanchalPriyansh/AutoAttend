import useCamera from '../../hooks/useCamera'

const IMAGE_TYPES = 'image/jpeg,image/png,image/webp'
const VIDEO_TYPES = 'video/mp4,video/webm,video/quicktime'

/**
 * Three ways to produce one classroom capture: pick a photo, pick a short
 * video, or take a photo with the webcam. All hand the result to
 * `onCapture(file, kind)` where `kind` is 'image' or 'video', and the parent
 * decides what to do with it.
 *
 * Deliberately separate from the admin FaceCapture control rather than a
 * shared component with flags: that one accepts a still of exactly one face
 * and says so, this one accepts a room full of people and a video too. The
 * stream lifecycle they genuinely do share lives in useCamera.
 *
 * Nothing here uploads or keeps anything — the capture is handed straight out
 * and the canvas it came from is discarded.
 *
 * Styled by styles/faculty-attendance.css. It used to render .face-capture,
 * .face-capture-live and .hierarchy-* -- all shared with the admin face
 * enrolment screen, which has not been designed -- so those were renamed onto
 * this page's own .fa-* hooks rather than restyled underneath admin.
 */
function ClassroomCapture({ onCapture, disabled }) {
  const { videoRef, active, error, start, stop, capturePhoto } = useCamera()

  async function takePhoto() {
    const blob = await capturePhoto()
    if (blob) onCapture(blob, 'image')
  }

  function handleFile(kind) {
    return (event) => {
      const file = event.target.files?.[0]
      // Cleared so picking the same file twice still fires a change event.
      event.target.value = ''
      if (file) onCapture(file, kind)
    }
  }

  return (
    <div className="fa-capture">
      {error && (
        <p role="alert" className="callout callout--error fa-alert">
          <span className="callout-mark" aria-hidden="true">
            !
          </span>
          {error}
        </p>
      )}

      <div className="fa-form fa-capture-form">
        <span className="form-field fa-field fa-file">
          <label htmlFor="classroom-photo">Upload a photo</label>
          <input
            id="classroom-photo"
            type="file"
            accept={IMAGE_TYPES}
            onChange={handleFile('image')}
            disabled={disabled}
          />
        </span>

        <span className="form-field fa-field fa-file">
          <label htmlFor="classroom-video">Upload a short video</label>
          <input
            id="classroom-video"
            type="file"
            accept={VIDEO_TYPES}
            onChange={handleFile('video')}
            disabled={disabled}
          />
        </span>

        {!active && (
          <button type="button" className="btn btn--secondary fa-btn" onClick={start} disabled={disabled}>
            Use camera
          </button>
        )}
      </div>

      {active && (
        <div className="fa-live">
          <video ref={videoRef} autoPlay playsInline muted aria-label="Live camera preview" />
          <div className="fa-form fa-live-actions">
            <button type="button" className="btn btn--primary fa-btn" onClick={takePhoto} disabled={disabled}>
              Take photo
            </button>
            <button type="button" className="btn btn--secondary fa-btn" onClick={stop}>
              Stop camera
            </button>
          </div>
        </div>
      )}

      <p className="fa-note">
        Capture the whole room, well lit, with faces turned towards the camera. A few
        seconds of video gives each student several chances to be seen. The photo or
        video is analysed and discarded — it is never stored.
      </p>
    </div>
  )
}

export default ClassroomCapture

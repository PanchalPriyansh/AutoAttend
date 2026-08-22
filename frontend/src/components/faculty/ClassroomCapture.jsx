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
    <div className="face-capture">
      {error && (
        <p role="alert" className="hierarchy-error">
          {error}
        </p>
      )}

      <div className="hierarchy-form">
        <span className="field">
          <label htmlFor="classroom-photo">Upload a photo</label>
          <input
            id="classroom-photo"
            type="file"
            accept={IMAGE_TYPES}
            onChange={handleFile('image')}
            disabled={disabled}
          />
        </span>

        <span className="field">
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

      <p className="hierarchy-hint">
        Capture the whole room, well lit, with faces turned towards the camera. A few
        seconds of video gives each student several chances to be seen. The photo or
        video is analysed and discarded — it is never stored.
      </p>
    </div>
  )
}

export default ClassroomCapture

import { useCallback, useEffect, useRef, useState } from 'react'

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
 * never going to use.
 */
function FaceCapture({ onCapture, disabled }) {
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const [cameraOn, setCameraOn] = useState(false)
  const [cameraError, setCameraError] = useState('')

  const stopCamera = useCallback(() => {
    const stream = streamRef.current
    if (stream) {
      // Every track, not just the first: stopping only the video track can
      // leave the camera indicator lit, which reads to the person in front
      // of it as still being recorded.
      stream.getTracks().forEach((track) => track.stop())
      streamRef.current = null
    }
    setCameraOn(false)
  }, [])

  // Unmounting while the camera runs (navigating away, selecting another
  // student) must release the device too.
  useEffect(() => stopCamera, [stopCamera])

  // Assigned in an effect rather than straight after getUserMedia resolves:
  // the <video> element only exists once cameraOn has re-rendered, so the
  // ref would still be null at that point.
  useEffect(() => {
    if (cameraOn && videoRef.current && streamRef.current) {
      videoRef.current.srcObject = streamRef.current
    }
  }, [cameraOn])

  async function startCamera() {
    setCameraError('')

    if (!navigator.mediaDevices?.getUserMedia) {
      setCameraError('This browser cannot open a camera here. Upload a photo instead.')
      return
    }

    try {
      streamRef.current = await navigator.mediaDevices.getUserMedia({ video: true })
      setCameraOn(true)
    } catch {
      // The specific DOMException name is not shown: "denied", "no camera",
      // and "device busy" all leave the admin with the same next step.
      setCameraError('Could not open the camera. Check permissions, or upload a photo instead.')
    }
  }

  function takePhoto() {
    const video = videoRef.current
    if (!video) return

    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height)

    canvas.toBlob(
      (blob) => {
        // The camera is released as soon as the frame is taken rather than
        // being left running while the upload is in flight.
        stopCamera()
        if (blob) onCapture(blob, 'camera')
      },
      'image/jpeg',
      0.92,
    )
  }

  function handleFile(event) {
    const file = event.target.files?.[0]
    // Cleared so picking the same file twice still fires a change event.
    event.target.value = ''
    if (file) onCapture(file, 'upload')
  }

  return (
    <div className="face-capture">
      {cameraError && (
        <p role="alert" className="hierarchy-error">
          {cameraError}
        </p>
      )}

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

        {!cameraOn && (
          <button type="button" onClick={startCamera} disabled={disabled}>
            Use camera
          </button>
        )}
      </div>

      {cameraOn && (
        <div className="face-capture-live">
          <video ref={videoRef} autoPlay playsInline muted />
          <div className="hierarchy-form">
            <button type="button" onClick={takePhoto} disabled={disabled}>
              Take photo
            </button>
            <button type="button" onClick={stopCamera}>
              Stop camera
            </button>
          </div>
          <p className="hierarchy-hint">
            One face only, well lit and facing the camera. The photo is not stored — only
            the numeric encoding derived from it.
          </p>
        </div>
      )}
    </div>
  )
}

export default FaceCapture

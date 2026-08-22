import { useCallback, useEffect, useRef, useState } from 'react'

const JPEG_QUALITY = 0.92

/**
 * The webcam lifecycle shared by every capture control: open a stream,
 * hand back a still, and — the part that matters — always release the
 * device afterwards.
 *
 * Extracted from FaceCapture when attendance capture needed the same
 * behaviour. Releasing a camera correctly is easy to get subtly wrong in a
 * way nobody notices until the indicator light stays on next to a room full
 * of students, so there is one copy of it rather than one per screen.
 *
 * Returns `{ videoRef, active, error, start, stop, capturePhoto }`. Attach
 * `videoRef` to a `<video autoPlay playsInline muted>` that is rendered only
 * while `active`.
 */
export default function useCamera() {
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const [active, setActive] = useState(false)
  const [error, setError] = useState('')

  const stop = useCallback(() => {
    const stream = streamRef.current
    if (stream) {
      // Every track, not just the first: stopping only the video track can
      // leave the camera indicator lit, which reads to the people in front
      // of it as still being recorded.
      stream.getTracks().forEach((track) => track.stop())
      streamRef.current = null
    }
    setActive(false)
  }, [])

  // Unmounting while the camera runs (navigating away, switching class or
  // student) must release the device too.
  useEffect(() => stop, [stop])

  // Assigned in an effect rather than straight after getUserMedia resolves:
  // the <video> element only exists once `active` has re-rendered, so the
  // ref would still be null at that point.
  useEffect(() => {
    if (active && videoRef.current && streamRef.current) {
      videoRef.current.srcObject = streamRef.current
    }
  }, [active])

  const start = useCallback(async () => {
    setError('')

    if (!navigator.mediaDevices?.getUserMedia) {
      setError('This browser cannot open a camera here. Upload a file instead.')
      return
    }

    try {
      streamRef.current = await navigator.mediaDevices.getUserMedia({ video: true })
      setActive(true)
    } catch {
      // The specific DOMException name is not shown: "denied", "no camera",
      // and "device busy" all leave the user with the same next step.
      setError('Could not open the camera. Check permissions, or upload a file instead.')
    }
  }, [])

  /**
   * Grab the current frame as a JPEG Blob, releasing the camera as soon as
   * the frame is taken rather than leaving it running while an upload is in
   * flight. Resolves to null if there is no live video element.
   */
  const capturePhoto = useCallback(
    () =>
      new Promise((resolve) => {
        const video = videoRef.current
        if (!video) {
          resolve(null)
          return
        }

        const canvas = document.createElement('canvas')
        canvas.width = video.videoWidth
        canvas.height = video.videoHeight
        canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height)

        canvas.toBlob(
          (blob) => {
            stop()
            resolve(blob)
          },
          'image/jpeg',
          JPEG_QUALITY,
        )
      }),
    [stop],
  )

  return { videoRef, active, error, start, stop, capturePhoto }
}

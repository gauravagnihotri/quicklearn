import { useEffect, useRef, useState } from 'react'
import { usePlayer } from '../../lib/store'
import { AudioPlayer } from './AudioPlayer'

/**
 * Local patch: plays the original upload for video sources, wired to the global player store
 * exactly like `AudioPlayer`, so note anchors, transcript clicks and chat citations seek it.
 *
 * Uses the browser's own controls (seek bar, speed, fullscreen). If the browser cannot decode
 * the file — an unusual container or codec — it falls back to the normalised audio.
 */
export function VideoPlayer({ src, audioSrc }: { src: string; audioSrc: string }) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)

  const { seekRequest, setTime, setDuration, setPlaying } = usePlayer()

  useEffect(() => {
    setReady(false)
    setFailed(false)
    return () => setPlaying(false)
  }, [src, setPlaying])

  // Seek requests arrive from anywhere in the app; the nonce means seeking to the current
  // position still fires.
  useEffect(() => {
    const video = videoRef.current
    if (!seekRequest || !video || !ready) return
    video.currentTime = Math.max(0, Math.min(video.duration || Infinity, seekRequest.ms / 1000))
    void video.play()
  }, [seekRequest, ready])

  if (failed) return <AudioPlayer src={audioSrc} />

  return (
    <video
      ref={videoRef}
      src={src}
      controls
      preload="metadata"
      className="aspect-video w-full rounded-lg bg-black"
      onLoadedMetadata={(event) => {
        setReady(true)
        setDuration(event.currentTarget.duration * 1000)
      }}
      onTimeUpdate={(event) => setTime(event.currentTarget.currentTime * 1000)}
      onPlay={() => setPlaying(true)}
      onPause={() => setPlaying(false)}
      onEnded={() => setPlaying(false)}
      onError={() => setFailed(true)}
    />
  )
}

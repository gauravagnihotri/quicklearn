import { Gauge, Pause, Play, RotateCcw, RotateCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import WaveSurfer from 'wavesurfer.js'
import { usePlayer } from '../../lib/store'
import { formatDuration } from '../../lib/utils'
import { Button, Spinner, Tooltip } from '../ui'

const SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2]

/**
 * Waveform audio player, wired to the global player store.
 *
 * The store connection is what makes click-to-source work: an anchor clicked inside the note
 * editor calls `seekTo`, and this component reacts — the two never talk to each other directly.
 */
export function AudioPlayer({ src, compact = false }: { src: string; compact?: boolean }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const waveRef = useRef<WaveSurfer | null>(null)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [speed, setSpeed] = useState(1)

  const { currentMs, durationMs, playing, seekRequest, setTime, setDuration, setPlaying } =
    usePlayer()

  useEffect(() => {
    if (!containerRef.current) return
    setReady(false)
    setError(null)

    const styles = getComputedStyle(document.documentElement)
    const wave = WaveSurfer.create({
      container: containerRef.current,
      height: compact ? 36 : 56,
      waveColor: styles.getPropertyValue('--border').trim() || '#d4d4d8',
      progressColor: styles.getPropertyValue('--accent').trim() || '#3b82f6',
      cursorColor: styles.getPropertyValue('--accent').trim() || '#3b82f6',
      cursorWidth: 2,
      barWidth: 2,
      barGap: 1,
      barRadius: 2,
      normalize: true,
      // Decoding a 90-minute WAV client-side is slow and memory-hungry. The backend serves
      // byte ranges, and wavesurfer streams through the media element rather than buffering
      // the whole file to build a waveform.
      backend: 'MediaElement',
      url: src,
    })
    waveRef.current = wave

    wave.on('ready', () => {
      setReady(true)
      setDuration(wave.getDuration() * 1000)
    })
    wave.on('timeupdate', (seconds) => setTime(seconds * 1000))
    wave.on('play', () => setPlaying(true))
    wave.on('pause', () => setPlaying(false))
    wave.on('finish', () => setPlaying(false))
    wave.on('error', (exception) => {
      setError(
        exception instanceof Error
          ? `Could not load the audio (${exception.message})`
          : 'Could not load the audio for this source.',
      )
      setReady(false)
    })

    return () => {
      wave.destroy()
      waveRef.current = null
      setPlaying(false)
    }
  }, [src, compact, setDuration, setTime, setPlaying])

  // Seek requests arrive from anywhere in the app (note anchors, transcript clicks, chat
  // citations). The nonce means seeking to the current position still fires.
  useEffect(() => {
    if (!seekRequest || !waveRef.current || !ready) return
    const duration = waveRef.current.getDuration()
    if (duration > 0) {
      waveRef.current.seekTo(Math.max(0, Math.min(1, seekRequest.ms / 1000 / duration)))
      void waveRef.current.play()
    }
  }, [seekRequest, ready])

  const skip = (seconds: number) => {
    const wave = waveRef.current
    if (!wave) return
    wave.setTime(Math.max(0, Math.min(wave.getDuration(), wave.getCurrentTime() + seconds)))
  }

  const cycleSpeed = () => {
    const next = SPEEDS[(SPEEDS.indexOf(speed) + 1) % SPEEDS.length] ?? 1
    setSpeed(next)
    waveRef.current?.setPlaybackRate(next, true)
  }

  if (error) {
    return (
      <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
        {error}
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="relative">
        <div ref={containerRef} className="w-full" />
        {!ready && (
          <div className="absolute inset-0 flex items-center justify-center gap-2 text-xs text-[var(--text-muted)]">
            <Spinner className="h-3.5 w-3.5" /> Loading audio…
          </div>
        )}
      </div>

      <div className="flex items-center gap-1.5">
        <Tooltip label="Back 10s">
          <Button variant="ghost" size="icon-sm" onClick={() => skip(-10)} disabled={!ready}>
            <RotateCcw className="h-3.5 w-3.5" />
          </Button>
        </Tooltip>
        <Button
          variant="primary"
          size="icon"
          onClick={() => waveRef.current?.playPause()}
          disabled={!ready}
          aria-label={playing ? 'Pause' : 'Play'}
        >
          {playing ? <Pause className="h-4 w-4" /> : <Play className="ml-0.5 h-4 w-4" />}
        </Button>
        <Tooltip label="Forward 10s">
          <Button variant="ghost" size="icon-sm" onClick={() => skip(10)} disabled={!ready}>
            <RotateCw className="h-3.5 w-3.5" />
          </Button>
        </Tooltip>

        <span className="ml-1 tabular-nums text-xs text-[var(--text-muted)]">
          {formatDuration(currentMs / 1000)} / {formatDuration(durationMs / 1000)}
        </span>

        <div className="flex-1" />

        <Tooltip label="Playback speed">
          <Button variant="ghost" size="sm" onClick={cycleSpeed} disabled={!ready}>
            <Gauge className="h-3.5 w-3.5" />
            {speed}×
          </Button>
        </Tooltip>
      </div>
    </div>
  )
}

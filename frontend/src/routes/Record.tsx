import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { AlertTriangle, Mic, Pause, Play, Square, Star, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { api, ApiError } from '../lib/api'
import { formatBytes, formatDuration, formatRelative } from '../lib/utils'
import {
  Badge, Button, Card, CardBody, Field, Input, Select, SelectContent, SelectItem,
  SelectTrigger, SelectValue,
} from '../components/ui'

/** Chunk interval. Five seconds bounds how much a crash can cost (PRD §7.1). */
const CHUNK_MS = 5000

type Phase = 'idle' | 'recording' | 'paused' | 'stopping'

export function RecordPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [phase, setPhase] = useState<Phase>('idle')
  const [elapsed, setElapsed] = useState(0)
  const [uploaded, setUploaded] = useState(0)
  const [markers, setMarkers] = useState<number[]>([])
  const [title, setTitle] = useState('')
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([])
  const [deviceId, setDeviceId] = useState('')
  const [warning, setWarning] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const tokenRef = useRef<string | null>(null)
  const sourceIdRef = useRef<number | null>(null)
  const startedAt = useRef(0)
  const pausedFor = useRef(0)
  const pauseStart = useRef(0)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const rafRef = useRef<number>(0)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)

  const { data: unfinished } = useQuery({
    queryKey: ['unfinished'],
    queryFn: () => api.unfinishedRecordings(),
  })

  // Device labels are only exposed after permission is granted, so the list is refreshed
  // once recording has started at least once.
  const loadDevices = useCallback(async () => {
    try {
      const all = await navigator.mediaDevices.enumerateDevices()
      setDevices(all.filter((device) => device.kind === 'audioinput'))
    } catch {
      /* permission not yet granted */
    }
  }, [])

  useEffect(() => {
    void loadDevices()
  }, [loadDevices])

  useEffect(() => {
    if (phase !== 'recording') return
    const timer = setInterval(() => {
      setElapsed(Date.now() - startedAt.current - pausedFor.current)
    }, 200)
    return () => clearInterval(timer)
  }, [phase])

  // Warn before closing the tab mid-recording. The partial file survives server-side either
  // way, but losing the in-flight chunk for nothing would be careless.
  useEffect(() => {
    if (phase !== 'recording' && phase !== 'paused') return
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [phase])

  const drawWave = useCallback(() => {
    const canvas = canvasRef.current
    const analyser = analyserRef.current
    if (!canvas || !analyser) return
    const context = canvas.getContext('2d')
    if (!context) return

    const buffer = new Uint8Array(analyser.frequencyBinCount)
    const styles = getComputedStyle(document.documentElement)
    const accent = styles.getPropertyValue('--accent').trim() || '#3b82f6'

    const render = () => {
      rafRef.current = requestAnimationFrame(render)
      analyser.getByteTimeDomainData(buffer)

      const { width, height } = canvas
      context.clearRect(0, 0, width, height)
      context.lineWidth = 2
      context.strokeStyle = accent
      context.beginPath()

      const slice = width / buffer.length
      let x = 0
      for (let i = 0; i < buffer.length; i += 1) {
        const value = ((buffer[i] ?? 128) / 128.0) * (height / 2)
        if (i === 0) context.moveTo(x, value)
        else context.lineTo(x, value)
        x += slice
      }
      context.stroke()
    }
    render()
  }, [])

  const start = async () => {
    setError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: deviceId
          ? { deviceId: { exact: deviceId }, echoCancellation: true, noiseSuppression: true }
          : { echoCancellation: true, noiseSuppression: true },
      })
      streamRef.current = stream
      void loadDevices()

      const session = await api.startRecording(title.trim())
      tokenRef.current = session.upload_token
      sourceIdRef.current = session.source_id
      setWarning(session.warning)

      // Live waveform via the Web Audio API.
      const audioContext = new AudioContext()
      audioContextRef.current = audioContext
      const analyser = audioContext.createAnalyser()
      analyser.fftSize = 2048
      audioContext.createMediaStreamSource(stream).connect(analyser)
      analyserRef.current = analyser
      drawWave()

      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/webm'
      const recorder = new MediaRecorder(stream, { mimeType })
      recorder.ondataavailable = async (event) => {
        if (event.data.size === 0 || !tokenRef.current) return
        try {
          // Chunks go straight to disk as they are produced — crash insurance, not streaming
          // transcription (PRD §7.1).
          await api.sendRecordingChunk(tokenRef.current, event.data)
          setUploaded((total) => total + event.data.size)
        } catch {
          toast.error('A chunk failed to upload. Recording continues.')
        }
      }
      recorder.start(CHUNK_MS)
      recorderRef.current = recorder

      startedAt.current = Date.now()
      pausedFor.current = 0
      setElapsed(0)
      setUploaded(0)
      setMarkers([])
      setPhase('recording')
    } catch (exception) {
      const message =
        exception instanceof ApiError
          ? exception.message
          : exception instanceof DOMException && exception.name === 'NotAllowedError'
            ? 'Microphone access was denied. Allow it in your browser settings and try again.'
            : 'Could not start recording.'
      setError(message)
      cleanup()
    }
  }

  const cleanup = () => {
    cancelAnimationFrame(rafRef.current)
    recorderRef.current?.stream.getTracks().forEach((track) => track.stop())
    streamRef.current?.getTracks().forEach((track) => track.stop())
    void audioContextRef.current?.close().catch(() => {})
    recorderRef.current = null
    streamRef.current = null
    analyserRef.current = null
    audioContextRef.current = null
  }

  const togglePause = () => {
    const recorder = recorderRef.current
    if (!recorder) return
    if (phase === 'recording') {
      recorder.pause()
      pauseStart.current = Date.now()
      setPhase('paused')
    } else if (phase === 'paused') {
      recorder.resume()
      pausedFor.current += Date.now() - pauseStart.current
      setPhase('recording')
    }
  }

  const mark = async () => {
    const sourceId = sourceIdRef.current
    if (!sourceId) return
    const position = elapsed
    setMarkers((current) => [...current, position])
    try {
      await api.addMarker(sourceId, Math.round(position))
      toast.success(`Marked at ${formatDuration(position / 1000)}`)
    } catch {
      toast.error('Could not save that marker.')
    }
  }

  const stop = async () => {
    const recorder = recorderRef.current
    const token = tokenRef.current
    if (!recorder || !token) return
    setPhase('stopping')

    // Flush the final partial chunk before finalising, or the last few seconds are lost.
    await new Promise<void>((resolve) => {
      recorder.onstop = () => resolve()
      recorder.stop()
    })
    await new Promise((resolve) => setTimeout(resolve, 350))
    cleanup()

    try {
      const result = await api.stopRecording(token)
      queryClient.invalidateQueries()
      toast.success('Recording saved — processing has started.')
      navigate({ to: '/library/$sourceId', params: { sourceId: String(result.source_id) } })
    } catch (exception) {
      setError(exception instanceof ApiError ? exception.message : 'Could not save the recording.')
      setPhase('idle')
    }
  }

  useEffect(() => cleanup, [])

  // 'stopping' counts as active so the controls stay put while the file is finalised, rather
  // than snapping back to "Start recording" for the second it takes to flush the last chunk.
  const active = phase !== 'idle'

  return (
    <div className="mx-auto max-w-2xl p-4 sm:p-6">
      <h1 className="mb-1 text-2xl font-semibold tracking-tight">Record a lecture</h1>
      <p className="mb-5 text-sm text-[var(--text-muted)]">
        Audio is saved to your machine every {CHUNK_MS / 1000} seconds while you record, so a
        browser crash cannot lose the session.
      </p>

      {error && (
        <Card className="mb-4 border-red-500/40 bg-red-500/5">
          <CardBody className="flex items-start gap-2.5 py-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
            <p className="text-sm">{error}</p>
          </CardBody>
        </Card>
      )}

      {warning && (
        <Card className="mb-4 border-amber-500/40 bg-amber-500/5">
          <CardBody className="flex items-start gap-2.5 py-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
            <p className="text-sm">{warning}</p>
          </CardBody>
        </Card>
      )}

      <Card>
        <CardBody className="space-y-4">
          {!active && (
            <>
              <Field label="Title" hint="Optional — quicklearn names it from the content otherwise.">
                <Input
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="Lecture 7 — Thermodynamics"
                />
              </Field>

              {devices.length > 0 && (
                <Field label="Microphone">
                  <Select value={deviceId} onValueChange={setDeviceId}>
                    <SelectTrigger>
                      <SelectValue placeholder="System default" />
                    </SelectTrigger>
                    <SelectContent>
                      {devices.map((device, index) => (
                        <SelectItem key={device.deviceId} value={device.deviceId}>
                          {device.label || `Microphone ${index + 1}`}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
              )}
            </>
          )}

          <div className="rounded-xl bg-[var(--bg-sunken)] p-4">
            <canvas
              ref={canvasRef}
              width={640}
              height={80}
              className="h-20 w-full"
              style={{ opacity: active ? 1 : 0.25 }}
            />
            <div className="mt-2 flex items-center justify-center gap-3">
              <span className="font-mono text-3xl tabular-nums">
                {formatDuration(elapsed / 1000)}
              </span>
              {phase === 'recording' && (
                <span className="flex items-center gap-1.5 text-xs text-red-500">
                  <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" /> REC
                </span>
              )}
              {phase === 'paused' && <Badge tone="warning">paused</Badge>}
            </div>
            {active && (
              <p className="mt-1 text-center text-[11px] text-[var(--text-muted)]">
                {formatBytes(uploaded)} saved · {markers.length} marker
                {markers.length === 1 ? '' : 's'}
              </p>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-center gap-2">
            {!active ? (
              <Button variant="primary" size="lg" onClick={start}>
                <Mic className="h-4 w-4" /> Start recording
              </Button>
            ) : (
              <>
                <Button size="lg" onClick={togglePause} disabled={phase === 'stopping'}>
                  {phase === 'paused' ? (
                    <>
                      <Play className="h-4 w-4" /> Resume
                    </>
                  ) : (
                    <>
                      <Pause className="h-4 w-4" /> Pause
                    </>
                  )}
                </Button>
                <Button
                  variant="primary"
                  size="lg"
                  onClick={mark}
                  disabled={phase === 'stopping'}
                  title="Mark this moment as important"
                >
                  <Star className="h-4 w-4" /> Mark
                </Button>
                <Button
                  variant="danger"
                  size="lg"
                  onClick={stop}
                  loading={phase === 'stopping'}
                >
                  <Square className="h-4 w-4" /> Stop and process
                </Button>
              </>
            )}
          </div>

          {active && (
            <p className="text-center text-[11px] text-[var(--text-muted)]">
              Tap <strong>Mark</strong> when something important is said — marked moments are
              treated as high-importance when the notes are written.
            </p>
          )}
        </CardBody>
      </Card>

      {(unfinished?.recordings.length ?? 0) > 0 && !active && (
        <Card className="mt-4">
          <div className="border-b border-[var(--border)] px-4 py-3">
            <h2 className="text-sm font-semibold">Unfinished recordings</h2>
            <p className="text-xs text-[var(--text-muted)]">
              These were interrupted. The audio is still on disk.
            </p>
          </div>
          <ul className="divide-y divide-[var(--border)]">
            {unfinished?.recordings.map((recording) => (
              <li key={recording.id} className="flex items-center gap-3 px-4 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{recording.title}</p>
                  <p className="text-xs text-[var(--text-muted)]">
                    {formatBytes(recording.size_bytes)} · {formatRelative(recording.created_at)}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="primary"
                  disabled={!recording.has_data}
                  onClick={async () => {
                    try {
                      await api.processUnfinished(recording.id)
                      queryClient.invalidateQueries()
                      toast.success('Processing started.')
                      navigate({
                        to: '/library/$sourceId',
                        params: { sourceId: String(recording.id) },
                      })
                    } catch (exception) {
                      toast.error(
                        exception instanceof ApiError ? exception.message : 'Could not process.',
                      )
                    }
                  }}
                >
                  Process anyway
                </Button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="text-red-500"
                  onClick={async () => {
                    await api.discardUnfinished(recording.id)
                    queryClient.invalidateQueries()
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <p className="mt-4 text-center text-[11px] text-[var(--text-muted)]">
        Consent laws vary, and your institution may have its own policy. Recording is your call.
      </p>
    </div>
  )
}

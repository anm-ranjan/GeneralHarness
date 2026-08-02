import { useEffect, useLayoutEffect, useState, useRef } from 'react'
import { useApp } from '../../context/AppContext'
import useApi from '../../hooks/useApi'
import { isSlashCommand, SUGGESTED_SLASH_COMMANDS } from '../../constants'
import ComposerToolbar from './ComposerToolbar'
import { api } from '../../api'
import { hostStorageKey } from '../../fleet'
import { audioBlobToWav } from '../../audioWav'

export default function Composer() {
  const { state, dispatch } = useApp()
  const { sendMessage, sendSilentCommand } = useApi()
  const [input, setInput] = useState('')
  const [attachments, setAttachments] = useState([])
  const [attachmentError, setAttachmentError] = useState('')
  const [audioError, setAudioError] = useState('')
  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const [recordingElapsed, setRecordingElapsed] = useState(0)
  const inputRef = useRef(null)
  const fileRef = useRef(null)
  const mediaRecorderRef = useRef(null)
  const audioChunksRef = useRef([])
  const audioStreamRef = useRef(null)
  const recordingStartedAtRef = useRef(0)
  const audioContextRef = useRef(null)
  const waveformRafRef = useRef(0)
  const waveformCanvasRef = useRef(null)
  const discardRecordingRef = useRef(false)

  useLayoutEffect(() => {
    resizeInput()
  }, [input])

  useEffect(() => {
    if (!state.currentSessionId) return
    setInput(localStorage.getItem(draftKey(state.activeHostId, state.currentSessionId)) || '')
    setAttachments([])
    setAudioError('')
  }, [state.currentSessionId])

  useEffect(() => {
    if (!state.currentSessionId) return
    localStorage.setItem(draftKey(state.activeHostId, state.currentSessionId), input)
  }, [state.currentSessionId, input])

  useEffect(() => {
    if (!recording) return
    const timer = window.setInterval(() => {
      const started = recordingStartedAtRef.current || Date.now()
      setRecordingElapsed(Math.max(0, Math.floor((Date.now() - started) / 1000)))
    }, 500)
    return () => window.clearInterval(timer)
  }, [recording])

  useEffect(() => {
    return () => {
      stopAudioTracks()
    }
  }, [])

  if (!state.currentSessionId) return null

  const audioAvailable = state.audioEnabled && typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== 'undefined'

  function resizeInput() {
    const el = inputRef.current
    if (!el) return
    const max = Math.round(window.innerHeight * 0.32)
    const min = 52
    el.style.height = 'auto'
    el.style.height = `${Math.min(Math.max(el.scrollHeight, min), max)}px`
  }

  function handleSend() {
    const text = input.trim()
    if (!text && attachments.length === 0) return
    if (state.isRunning && isSlashCommand(text)) {
      dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: 'Slash commands can only run when the agent is idle.' } })
      return
    }
    if (state.currentProvider === 'codex-cli') {
      dispatch({
        type: 'OPEN_CONFIRM',
        payload: {
          title: 'Migrate provider?',
          message: 'This thread uses the legacy Codex CLI provider. Your current message will stay in the composer; send it again after migration completes.',
          confirmLabel: 'Migrate',
          onConfirm: () => sendSilentCommand(state.currentSessionId, '/model codex'),
        },
      })
      return
    }
    const currentUnavailable =
      (state.currentProvider === 'native' && !state.nativeEnabled)
      || (state.currentProvider === 'codex-app-server' && !state.codexAppServerEnabled)
      || (state.currentProvider === 'claude-agent' && !state.claudeAgentEnabled)
    if (currentUnavailable) {
      const fallback = state.nativeEnabled
        ? { command: 'native', label: 'Native' }
        : state.codexAppServerEnabled
          ? { command: 'codex', label: 'Codex' }
          : state.claudeAgentEnabled
            ? { command: 'claude', label: 'Claude' }
            : null
      if (!fallback) {
        dispatch({ type: 'APPEND_STAGE_ITEM', payload: { type: 'error', text: 'No authenticated provider is available. Re-run setup or configure a provider.' } })
        return
      }
      dispatch({
        type: 'OPEN_CONFIRM',
        payload: {
          title: `Switch to ${fallback.label}?`,
          message: 'The current provider is unavailable. Your message will stay in the composer; send it again after migration completes.',
          confirmLabel: 'Switch',
          onConfirm: () => sendSilentCommand(state.currentSessionId, `/model ${fallback.command}`),
        },
      })
      return
    }
    setInput('')
    setAttachmentError('')
    localStorage.removeItem(draftKey(state.activeHostId, state.currentSessionId))
    setAttachments([])
    sendMessage(state.currentSessionId, text, attachments)
    requestAnimationFrame(resizeInput)
  }

  async function fileToPayload(file) {
    if (file.size > 10 * 1024 * 1024) {
      throw new Error(`${file.name || 'Attachment'} is larger than 10 MB.`)
    }
    const data = file.type.startsWith('image/') ? await resizeImage(file) : await blobToDataUrl(file)
    return {
      data,
      mime: data.slice(5, data.indexOf(';')) || file.type || 'application/octet-stream',
      name: file.name || 'attachment',
    }
  }

  async function addFiles(fileList) {
    setAttachmentError('')
    const files = [...fileList]
    if (!files.length) return
    const remaining = Math.max(0, 4 - attachments.length)
    if (files.length > remaining) setAttachmentError('Maximum 4 attachments per message.')
    try {
      const payloads = (await Promise.all(files.slice(0, remaining).map(fileToPayload))).filter(Boolean)
      setAttachments(current => [...current, ...payloads])
    } catch (err) {
      setAttachmentError(err.message || 'Failed to read attachment.')
    }
  }

  function handlePaste(e) {
    const files = [...(e.clipboardData?.files || [])]
    if (files.length > 0) {
      e.preventDefault()
      addFiles(files)
    }
  }

  function handleDrop(e) {
    e.preventDefault()
    addFiles(e.dataTransfer.files || [])
  }

  function removeAttachment(index) {
    setAttachments(current => current.filter((_, i) => i !== index))
  }

  function insertTranscription(text) {
    const transcript = (text || '').trim()
    if (!transcript) return
    setInput(current => {
      if (!current.trim()) return transcript
      return `${current.trimEnd()}\n\n${transcript}`
    })
    requestAnimationFrame(() => {
      inputRef.current?.focus()
      resizeInput()
    })
  }

  function stopAudioTracks() {
    audioStreamRef.current?.getTracks?.().forEach(track => track.stop())
    audioStreamRef.current = null
    cancelAnimationFrame(waveformRafRef.current)
    waveformRafRef.current = 0
    audioContextRef.current?.close?.().catch(() => {})
    audioContextRef.current = null
  }

  function startWaveform(stream) {
    const AudioContextImpl = window.AudioContext || window.webkitAudioContext
    if (!AudioContextImpl) return
    const audioContext = new AudioContextImpl()
    audioContextRef.current = audioContext
    const analyser = audioContext.createAnalyser()
    analyser.fftSize = 256
    audioContext.createMediaStreamSource(stream).connect(analyser)
    const data = new Uint8Array(analyser.frequencyBinCount)

    const draw = () => {
      const canvas = waveformCanvasRef.current
      if (canvas && audioContextRef.current) {
        const ctx = canvas.getContext('2d')
        analyser.getByteFrequencyData(data)
        const { width, height } = canvas
        ctx.clearRect(0, 0, width, height)
        const bars = 28
        const step = Math.floor(data.length / bars) || 1
        const barWidth = width / bars
        ctx.fillStyle = getComputedStyle(document.documentElement)
          .getPropertyValue('--color-danger').trim() || '#ef4444'
        for (let i = 0; i < bars; i++) {
          const value = data[i * step] / 255
          const barHeight = Math.max(2, value * height)
          ctx.globalAlpha = 0.45 + value * 0.55
          ctx.fillRect(i * barWidth + 1, (height - barHeight) / 2, barWidth - 2, barHeight)
        }
        ctx.globalAlpha = 1
      }
      waveformRafRef.current = requestAnimationFrame(draw)
    }
    waveformRafRef.current = requestAnimationFrame(draw)
  }

  async function startRecording() {
    if (!audioAvailable || recording || transcribing) return
    setAudioError('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      audioStreamRef.current = stream
      audioChunksRef.current = []
      const mimeType = preferredAudioMimeType()
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      mediaRecorderRef.current = recorder
      recorder.ondataavailable = event => {
        if (event.data?.size) audioChunksRef.current.push(event.data)
      }
      recorder.onerror = event => {
        setAudioError(event.error?.message || 'Audio recording failed.')
        setRecording(false)
        stopAudioTracks()
      }
      recorder.onstop = () => {
        const chunks = audioChunksRef.current
        const discarded = discardRecordingRef.current
        audioChunksRef.current = []
        discardRecordingRef.current = false
        stopAudioTracks()
        setRecording(false)
        setRecordingElapsed(0)
        if (discarded) return
        if (!chunks.length) {
          setAudioError('No audio was recorded.')
          return
        }
        const type = recorder.mimeType || mimeType || 'audio/webm'
        transcribeAudioBlob(new Blob(chunks, { type }))
      }
      recordingStartedAtRef.current = Date.now()
      discardRecordingRef.current = false
      setRecordingElapsed(0)
      setRecording(true)
      startWaveform(stream)
      recorder.start(1000)
    } catch (err) {
      setAudioError(err.message || 'Microphone access failed.')
      setRecording(false)
      stopAudioTracks()
    }
  }

  function stopRecording() {
    const recorder = mediaRecorderRef.current
    if (recorder && recorder.state !== 'inactive') {
      recorder.stop()
    }
  }

  function discardRecording() {
    discardRecordingRef.current = true
    stopRecording()
  }

  async function transcribeAudioBlob(blob) {
    setTranscribing(true)
    setAudioError('')
    try {
      const uploadBlob = state.audioProcessor === 'api' ? await audioBlobToWav(blob) : blob
      const mime = uploadBlob.type || 'audio/webm'
      const extension = audioExtension(mime)
      const data = await blobToDataUrl(uploadBlob)
      const result = await api('POST', '/api/audio/transcribe', {
        session_id: state.currentSessionId,
        data,
        mime,
        name: `composer-recording.${extension}`,
      })
      insertTranscription(result?.text || '')
    } catch (err) {
      setAudioError(err.detail || err.message || 'Transcription failed.')
    } finally {
      setTranscribing(false)
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && e.isComposing) return
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      handleSend()
      return
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="border-t border-line px-7 pt-4 pb-3">
      <div className="max-w-[min(1100px,calc(100vw-2rem))] mx-auto">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <div className="flex-1 min-w-0">
            <textarea
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              onDrop={handleDrop}
              onDragOver={e => e.preventDefault()}
              placeholder="Type a message, paste, or drop files..."
              rows={1}
              className="w-full min-h-[52px] max-h-[32vh] resize-y bg-surface border border-line rounded-lg px-4 py-3 text-[14px] leading-5 text-text-default placeholder:text-faint outline-none focus:border-line-hover transition-colors disabled:opacity-50"
            />
            {input.startsWith('/') && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {SUGGESTED_SLASH_COMMANDS.map(cmd => (
                  <button
                    key={cmd}
                    type="button"
                    onClick={() => setInput(cmd)}
                    className="px-2 py-0.5 rounded border border-line text-[11px] text-muted hover:text-accent hover:border-line-hover"
                  >
                    {cmd}
                  </button>
                ))}
              </div>
            )}
          </div>
          <input
            ref={fileRef}
            type="file"
            multiple
            className="hidden"
            onChange={e => {
              addFiles(e.target.files || [])
              e.target.value = ''
            }}
          />
          <div className="flex gap-2 self-end">
            {state.audioEnabled && (
              <button
                type="button"
                onClick={recording ? stopRecording : startRecording}
                disabled={!audioAvailable || transcribing}
                className={`h-9 w-10 border rounded-lg transition disabled:opacity-40 flex items-center justify-center ${
                  recording
                    ? 'border-danger text-danger bg-danger-soft animate-rec-ring'
                    : 'border-line text-muted hover:text-accent hover:border-line-hover'
                }`}
                title={recording ? 'Finish recording and transcribe' : (audioAvailable ? 'Record voice prompt' : 'Audio recording is unavailable in this browser')}
                aria-label={recording ? 'Finish recording and transcribe' : 'Record voice prompt'}
              >
                {recording ? (
                  <span className="h-3 w-3 rounded-sm bg-current" aria-hidden="true" />
                ) : (
                  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                    <path d="M12 19v3" />
                  </svg>
                )}
              </button>
            )}
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              disabled={attachments.length >= 4}
              className="px-3 py-2 text-[13px] font-medium text-muted border border-line rounded-lg hover:text-accent hover:border-line-hover transition disabled:opacity-40"
              title="Attach files"
            >
              Attach
            </button>
            <button
              onClick={handleSend}
              disabled={!input.trim() && attachments.length === 0}
              className="h-9 w-10 text-[18px] font-semibold text-bg bg-accent rounded-lg hover:brightness-110 transition disabled:opacity-40"
              title={state.isRunning ? 'Queue message' : 'Send message'}
              aria-label={state.isRunning ? 'Queue message' : 'Send message'}
            >
              ↑
            </button>
          </div>
        </div>
        {attachmentError && <div className="mt-2 text-[12px] text-danger">{attachmentError}</div>}
        {audioError && <div className="mt-2 text-[12px] text-danger">{audioError}</div>}
        {recording && !audioError && (
          <div className="mt-2 flex items-center gap-3 rounded-lg border border-danger/30 bg-danger-soft px-3 py-1.5 text-[12px] text-muted animate-scale-in">
            <span className="h-2 w-2 shrink-0 rounded-full bg-danger animate-pulse-fast" aria-hidden="true" />
            <canvas ref={waveformCanvasRef} width={168} height={26} className="h-[26px] w-[168px] shrink-0" aria-hidden="true" />
            <span className="font-mono tabular-nums text-text-default">{formatDuration(recordingElapsed)}</span>
            <span className="flex-1" />
            <button
              type="button"
              onClick={discardRecording}
              className="shrink-0 rounded border border-line px-2 py-0.5 text-[11px] text-muted hover:text-danger hover:border-danger/50 transition-colors"
              title="Discard recording"
            >
              ✕ Discard
            </button>
            <button
              type="button"
              onClick={stopRecording}
              className="shrink-0 rounded border border-line px-2 py-0.5 text-[11px] text-accent hover:brightness-110 transition"
              title="Finish and transcribe"
            >
              ■ Use
            </button>
          </div>
        )}
        {transcribing && !audioError && (
          <div className="mt-2 flex items-center gap-2 text-[12px] animate-scale-in">
            <span className="spinner" aria-hidden="true" />
            <span className="text-shimmer">Transcribing audio…</span>
          </div>
        )}
        <div className="mt-3 border-t border-line/50 pt-2">
          {attachments.length > 0 && (
            <div className="mb-2 flex gap-2 overflow-x-auto pb-1">
              {attachments.map((attachment, i) => (
                <div key={`${attachment.name}_${i}`} className="relative shrink-0 w-28 rounded-md border border-line bg-surface p-1">
                  {attachment.mime?.startsWith('image/') ? (
                    <img src={attachment.data} alt={attachment.name} className="h-16 w-full object-cover rounded" />
                  ) : (
                    <div className="flex h-16 w-full items-center justify-center rounded border border-line/70 bg-black/15 px-2 text-center text-[11px] font-medium text-muted">
                      {fileExtension(attachment.name)}
                    </div>
                  )}
                  <div className="mt-1 truncate text-[10px] text-faint" title={attachment.name}>{attachment.name}</div>
                  <button
                    type="button"
                    onClick={() => removeAttachment(i)}
                    className="absolute -top-1.5 -right-1.5 h-5 w-5 rounded-full bg-danger text-white text-[12px] leading-5"
                    aria-label={`Remove ${attachment.name}`}
                  >
                    x
                  </button>
                </div>
              ))}
            </div>
          )}
          <ComposerToolbar />
        </div>
      </div>
    </div>
  )
}

function preferredAudioMimeType() {
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/mp4',
  ]
  return candidates.find(type => MediaRecorder.isTypeSupported?.(type)) || ''
}

function audioExtension(mime) {
  if (mime === 'audio/wav' || mime === 'audio/x-wav') return 'wav'
  if (mime === 'audio/mp4' || mime === 'audio/x-m4a') return 'm4a'
  if (mime === 'audio/mpeg' || mime === 'audio/mp3') return 'mp3'
  return 'webm'
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('Failed to read audio'))
    reader.onload = () => resolve(reader.result)
    reader.readAsDataURL(blob)
  })
}

function formatDuration(seconds) {
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  return `${mins}:${String(secs).padStart(2, '0')}`
}

function fileExtension(name = '') {
  const ext = String(name).split('.').pop()
  return ext && ext !== name ? ext.slice(0, 8).toUpperCase() : 'FILE'
}

// Session ids are only unique within one machine, so drafts are namespaced by
// host: without it, two hosts' sessions could share a draft.
function draftKey(hostId, sessionId) {
  return hostStorageKey(hostId, `draft:${sessionId}`)
}

function resizeImage(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('Failed to read image'))
    reader.onload = () => {
      const img = new Image()
      img.onerror = () => reject(new Error('Failed to load image'))
      img.onload = () => {
        const maxSide = 2048
        const scale = Math.min(1, maxSide / Math.max(img.width, img.height))
        const canvas = document.createElement('canvas')
        canvas.width = Math.max(1, Math.round(img.width * scale))
        canvas.height = Math.max(1, Math.round(img.height * scale))
        const ctx = canvas.getContext('2d')
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
        resolve(canvas.toDataURL('image/jpeg', 0.85))
      }
      img.src = reader.result
    }
    reader.readAsDataURL(file)
  })
}

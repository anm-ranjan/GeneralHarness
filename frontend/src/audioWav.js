const DEFAULT_SAMPLE_RATE = 16_000

export async function audioBlobToWav(blob, targetSampleRate = DEFAULT_SAMPLE_RATE) {
  const AudioContextImpl = window.AudioContext || window.webkitAudioContext
  if (!AudioContextImpl) throw new Error('This browser cannot decode the recorded audio.')

  const audioContext = new AudioContextImpl()
  try {
    const encoded = await blob.arrayBuffer()
    const decoded = await audioContext.decodeAudioData(encoded.slice(0))
    return new Blob([audioBufferToWavBytes(decoded, targetSampleRate)], { type: 'audio/wav' })
  } finally {
    await audioContext.close().catch(() => {})
  }
}

export function audioBufferToWavBytes(audioBuffer, targetSampleRate = DEFAULT_SAMPLE_RATE) {
  if (!Number.isFinite(targetSampleRate) || targetSampleRate <= 0) {
    throw new Error('WAV sample rate must be a positive number.')
  }
  if (!audioBuffer?.numberOfChannels || !audioBuffer.length || !audioBuffer.sampleRate) {
    throw new Error('The recorded audio is empty.')
  }

  const mono = new Float32Array(audioBuffer.length)
  for (let channel = 0; channel < audioBuffer.numberOfChannels; channel++) {
    const samples = audioBuffer.getChannelData(channel)
    for (let i = 0; i < mono.length; i++) mono[i] += samples[i] / audioBuffer.numberOfChannels
  }

  const pcm = resampleLinear(mono, audioBuffer.sampleRate, targetSampleRate)
  const bytes = new Uint8Array(44 + pcm.length * 2)
  const view = new DataView(bytes.buffer)
  writeAscii(view, 0, 'RIFF')
  view.setUint32(4, bytes.length - 8, true)
  writeAscii(view, 8, 'WAVE')
  writeAscii(view, 12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, targetSampleRate, true)
  view.setUint32(28, targetSampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeAscii(view, 36, 'data')
  view.setUint32(40, pcm.length * 2, true)

  for (let i = 0; i < pcm.length; i++) {
    const sample = Math.max(-1, Math.min(1, pcm[i]))
    view.setInt16(44 + i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true)
  }
  return bytes
}

function resampleLinear(samples, sourceRate, targetRate) {
  if (sourceRate === targetRate) return samples
  const outputLength = Math.max(1, Math.round(samples.length * targetRate / sourceRate))
  const output = new Float32Array(outputLength)
  const ratio = sourceRate / targetRate
  for (let i = 0; i < outputLength; i++) {
    const position = Math.min(i * ratio, samples.length - 1)
    const left = Math.floor(position)
    const right = Math.min(left + 1, samples.length - 1)
    const fraction = position - left
    output[i] = samples[left] + (samples[right] - samples[left]) * fraction
  }
  return output
}

function writeAscii(view, offset, value) {
  for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i))
}

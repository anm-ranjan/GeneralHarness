import assert from 'node:assert/strict'
import test from 'node:test'

import { audioBufferToWavBytes } from './audioWav.js'

function ascii(bytes, start, length) {
  return String.fromCharCode(...bytes.slice(start, start + length))
}

test('audioBufferToWavBytes writes 16 kHz mono PCM WAV data', () => {
  const channels = [
    new Float32Array([1, 0.5, -0.5, -1]),
    new Float32Array([1, -0.5, 0.5, -1]),
  ]
  const bytes = audioBufferToWavBytes({
    numberOfChannels: channels.length,
    length: channels[0].length,
    sampleRate: 16_000,
    getChannelData: channel => channels[channel],
  })
  const view = new DataView(bytes.buffer)

  assert.equal(ascii(bytes, 0, 4), 'RIFF')
  assert.equal(ascii(bytes, 8, 4), 'WAVE')
  assert.equal(ascii(bytes, 12, 4), 'fmt ')
  assert.equal(ascii(bytes, 36, 4), 'data')
  assert.equal(view.getUint16(20, true), 1)
  assert.equal(view.getUint16(22, true), 1)
  assert.equal(view.getUint32(24, true), 16_000)
  assert.equal(view.getUint16(34, true), 16)
  assert.equal(view.getUint32(40, true), 8)
  assert.equal(view.getInt16(44, true), 0x7fff)
  assert.equal(view.getInt16(46, true), 0)
  assert.equal(view.getInt16(48, true), 0)
  assert.equal(view.getInt16(50, true), -0x8000)
})

test('audioBufferToWavBytes downsamples to the requested rate', () => {
  const samples = new Float32Array(48_000)
  const bytes = audioBufferToWavBytes({
    numberOfChannels: 1,
    length: samples.length,
    sampleRate: 48_000,
    getChannelData: () => samples,
  }, 16_000)
  const view = new DataView(bytes.buffer)

  assert.equal(view.getUint32(24, true), 16_000)
  assert.equal(view.getUint32(40, true), 16_000 * 2)
  assert.equal(bytes.length, 44 + 16_000 * 2)
})

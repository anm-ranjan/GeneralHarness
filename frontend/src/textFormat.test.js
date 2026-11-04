import assert from 'node:assert/strict'
import test from 'node:test'

import { continueList, hasFormatting, messageBody, parseFormatted, stripMarkers, toggleMarker } from './textFormat.js'

test('markers render as styles and are stripped for the model', () => {
  const text = 'Check **all 16** folders, *not* ++some++ of ~~them~~.'
  assert.equal(stripMarkers(text), 'Check all 16 folders, not some of them.')
  assert.deepEqual(parseFormatted('a **b** c'), [{ text: 'a ' }, { style: 'bold', children: [{ text: 'b' }] }, { text: ' c' }])
  assert.deepEqual(parseFormatted('***both***'), [{ style: 'bold', children: [{ style: 'italic', children: [{ text: 'both' }] }] }])
  assert.equal(stripMarkers('**bold with *italic* inside**'), 'bold with italic inside')
  assert.equal(stripMarkers('***both*** and **b**'), 'both and b')
})

test('code-like text is never treated as formatting', () => {
  for (const literal of [
    'def f(*args, **kwargs): pass',
    '2*3*4 and a * b * c',
    'glob *.py and *.js',
    'C++ and C++ again',
    'edit __init__.py and __main__',
    'x ** 2 ** 3',
    'a **b\nc** d',
    'path D:\\data\\*\\out',
    '\\*escaped*',
  ]) {
    assert.equal(stripMarkers(literal), literal, literal)
    assert.equal(hasFormatting(literal), false, literal)
  }
  assert.equal(stripMarkers('use `**kwargs**` here'), 'use `**kwargs**` here')
  assert.equal(stripMarkers('```\n**not bold**\n```'), '```\n**not bold**\n```')
  assert.equal(stripMarkers('**bold** and `**code**`'), 'bold and `**code**`')
})

test('toggling wraps the selection, keeps spaces outside, and unwraps', () => {
  assert.deepEqual(toggleMarker('make this bold', 5, 9, '**'), { value: 'make **this** bold', start: 7, end: 11 })
  assert.deepEqual(toggleMarker('make **this** bold', 7, 11, '**'), { value: 'make this bold', start: 5, end: 9 })
  assert.deepEqual(toggleMarker('make **this** bold', 5, 13, '**'), { value: 'make this bold', start: 5, end: 9 })
  assert.deepEqual(toggleMarker('a word b', 1, 7, '~~'), { value: 'a ~~word~~ b', start: 4, end: 8 })
  assert.deepEqual(toggleMarker('ab', 1, 1, '++'), { value: 'a++++b', start: 3, end: 3 })
  // Italic inside bold adds a marker instead of eating half of the ** pair.
  assert.equal(toggleMarker('**word**', 2, 6, '*').value, '***word***')
})

function shiftEnter(value) {
  return continueList(value, value.length)
}

test('lists continue with the next marker in the same style', () => {
  assert.equal(shiftEnter('(a) first').value, '(a) first\n(b) ')
  assert.equal(shiftEnter('a) first').value, 'a) first\nb) ')
  assert.equal(shiftEnter('(i) one').value, '(i) one\n(ii) ')
  assert.equal(shiftEnter('(iii) three').value, '(iii) three\n(iv) ')
  assert.equal(shiftEnter('iv) four').value, 'iv) four\nv) ')
  assert.equal(shiftEnter('1. step').value, '1. step\n2. ')
  assert.equal(shiftEnter('- point').value, '- point\n- ')
  assert.equal(shiftEnter('   (A) upper').value, '   (A) upper\n   (B) ')
  assert.equal(shiftEnter('(I) roman').value, '(I) roman\n(II) ')
})

test('i, v and x are letters or numerals depending on the point before', () => {
  assert.equal(shiftEnter('(h) eight\n(i) nine').value, '(h) eight\n(i) nine\n(j) ')
  assert.equal(shiftEnter('(iv) four\n(v) five').value, '(iv) four\n(v) five\n(vi) ')
  assert.equal(shiftEnter('(u) u\n(v) v').value, '(u) u\n(v) v\n(w) ')
  assert.equal(shiftEnter('(c) c').value, '(c) c\n(d) ')
})

test('an empty point ends the list; a colon line starts an indented one', () => {
  assert.deepEqual(shiftEnter('(a) first\n(b) '), { value: '(a) first\n', cursor: 10 })
  assert.deepEqual(shiftEnter('Do the following:'), { value: 'Do the following:\n   ', cursor: 21 })
  assert.equal(shiftEnter('plain text'), null)
  // Splitting a point in the middle carries the rest onto the new point.
  assert.deepEqual(continueList('(a) firstsecond', 9), { value: '(a) first\n(b) second', cursor: 14 })
})

test('sent messages carry plain text for the model and markers for display', () => {
  assert.deepEqual(messageBody('see **this**', [], 'queue'),
    { text: 'see this', display_text: 'see **this**', attachments: [], mode: 'queue' })
  assert.deepEqual(messageBody('plain words', [], 'steer'), { text: 'plain words', attachments: [], mode: 'steer' })
  assert.deepEqual(messageBody('/compact **x**'), { text: '/compact **x**', attachments: [], mode: 'queue' })
})

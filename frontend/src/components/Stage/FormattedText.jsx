import { memo, useMemo } from 'react'
import { parseFormatted } from '../../textFormat'

const TAGS = { bold: 'strong', italic: 'em', underline: 'u', strike: 's' }

function renderNodes(nodes) {
  return nodes.map((node, i) => {
    if (node.children) {
      const Tag = TAGS[node.style] || 'span'
      return <Tag key={i} className={node.style === 'bold' ? 'font-bold' : undefined}>{renderNodes(node.children)}</Tag>
    }
    // Code spans keep their backticks and look exactly as typed.
    return node.code ?? node.text
  })
}

// The user's message with **bold**, *italic*, ++underline++ and ~~strike~~
// shown as formatting. Built as React elements, never as HTML.
function FormattedText({ text }) {
  const nodes = useMemo(() => parseFormatted(text), [text])
  return <>{renderNodes(nodes)}</>
}

export default memo(FormattedText)

// A small markdown renderer for the agent's final report: headings (#), bold (**x**), quotes (>),
// bullet and numbered lists, and blank lines between paragraphs. That's all the reports use, so
// no library. Text goes through React as plain strings, so nothing in a report can inject HTML.
import type { ReactNode } from 'react'

// **bold** inside a line.
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') && part.length > 4
      ? <strong key={i} className="font-semibold">{part.slice(2, -2)}</strong>
      : part,
  )
}

type Block = { kind: 'h' | 'p' | 'quote' | 'ul' | 'ol'; lines: string[] }

// Group lines into blocks: a heading is one line; quotes and list items stick together;
// anything else joins the current paragraph until a blank line.
function blocks(md: string): Block[] {
  const out: Block[] = []
  for (const raw of md.split('\n')) {
    const line = raw.trim()
    const last = out[out.length - 1]
    if (!line) { out.push({ kind: 'p', lines: [] }); continue }
    if (/^#{1,6}\s/.test(line)) { out.push({ kind: 'h', lines: [line.replace(/^#+\s*/, '')] }); continue }
    const kind: Block['kind'] =
      line.startsWith('>') ? 'quote' : /^[-*]\s/.test(line) ? 'ul' : /^\d+[.)]\s/.test(line) ? 'ol' : 'p'
    const text = kind === 'quote' ? line.replace(/^>\s?/, '') : kind === 'ul' ? line.replace(/^[-*]\s/, '') : kind === 'ol' ? line.replace(/^\d+[.)]\s/, '') : line
    if (last && last.kind === kind && (kind !== 'p' || last.lines.length)) last.lines.push(text)
    else out.push({ kind, lines: [text] })
  }
  return out.filter((b) => b.lines.length)
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className="flex flex-col gap-2 text-sm leading-relaxed">
      {blocks(text).map((b, i) => {
        if (b.kind === 'h') return <p key={i} className="mt-1 font-semibold">{inline(b.lines[0])}</p>
        if (b.kind === 'quote') return <blockquote key={i} className="border-l-2 pl-3 text-muted-foreground">{inline(b.lines.join(' '))}</blockquote>
        if (b.kind === 'ul') return <ul key={i} className="list-disc pl-5">{b.lines.map((l, j) => <li key={j}>{inline(l)}</li>)}</ul>
        if (b.kind === 'ol') return <ol key={i} className="list-decimal pl-5">{b.lines.map((l, j) => <li key={j}>{inline(l)}</li>)}</ol>
        // lines inside one paragraph keep their line breaks (e.g. "**Task:** …" then "**Method:** …")
        return <p key={i}>{b.lines.map((l, j) => <span key={j}>{j > 0 && <br />}{inline(l)}</span>)}</p>
      })}
    </div>
  )
}

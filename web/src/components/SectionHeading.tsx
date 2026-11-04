// A screen's title block: a small numbered label, a big headline and one line of context.
// Shared by Compare ("02") and Results ("03"); the intro will be "01".
export function SectionHeading({ eyebrow, title, text }: { eyebrow: string; title: string; text: string }) {
  return (
    <div>
      <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">{eyebrow}</p>
      <h2 className="mt-1 text-3xl font-bold tracking-tight">{title}</h2>
      <p className="mt-1 text-sm text-muted-foreground">{text}</p>
    </div>
  )
}

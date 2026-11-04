// The intro screen ("01"): what MOAT is, in one picture.

import castle from '@/assets/castle.png'
import guard from '@/assets/guard.png'
import messenger from '@/assets/messenger.png'
import { SectionHeading } from '@/components/SectionHeading'

// Keep pixel art crisp when it's scaled up (no blur between pixels).
const PIXEL = { imageRendering: 'pixelated' } as const

function Messenger({ frame = 0, faceRight = false }: { frame?: 0 | 1; faceRight?: boolean }) {
  return (
    <div
      aria-hidden
      className="aspect-square h-full"
      style={{
        ...PIXEL,
        backgroundImage: `url(${messenger})`,
        backgroundSize: '200% 100%',
        backgroundPosition: frame === 0 ? '0% 0' : '100% 0',
        transform: faceRight ? undefined : 'scaleX(-1)',
      }}
    />
  )
}

const OUTCOMES = [
  { label: 'Pass', text: 'Harmless search? Let it through.', swatch: 'bg-emerald-200 border-emerald-700' },
  { label: 'Rewrite', text: 'Too revealing? Remove the clue.', swatch: 'bg-amber-200 border-amber-700' },
  { label: 'Block', text: 'Gives a secret away? Stop the search.', swatch: 'bg-red-200 border-red-700' },
]

export function IntroPage({ onWatch }: { onWatch: () => void }) {
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-col gap-6 p-8">
      <SectionHeading
        eyebrow="01 / The checkpoint for AI search"
        title="Every search is a tiny leak."
        text="Your AI research agent reads company documents and searches the web. One query looks innocent; many together can give the company's secrets away. MOAT checks every search before it leaves."
      />

      {/* The scene. Positions are in % of the box, so it scales with the screen width. */}
      <div
        role="img"
        aria-label="A castle with a moat. Messengers carry search queries out across the drawbridge, where a guard checks each one."
        className="relative aspect-[16/7] w-full overflow-hidden rounded-xl border-2 border-foreground/80 bg-sky-100 shadow-[6px_6px_0_0] shadow-foreground/80"
      >
        {/* ground, moat, bridge */}
        <div className="absolute inset-x-0 bottom-[30%] h-[8%] bg-emerald-300/70" />
        <div className="absolute inset-x-0 bottom-0 h-[30%] bg-sky-600/80" />
        <div className="absolute bottom-[30%] left-[30%] h-[5%] w-[42%] bg-amber-800" />

        {/* castle (right), guard at the gate (middle), messengers heading out to the web (left) */}
        <img src={castle} alt="" style={PIXEL} className="absolute bottom-[28%] right-[4%] h-[62%]" />
        <img src={guard} alt="" style={PIXEL} className="absolute bottom-[34%] left-[48%] h-[28%]" />
        {/* Same height as the guard's box, so all characters share one scale. */}
        <div className="absolute bottom-[34%] left-[36%] h-[28%]"><Messenger frame={0} /></div>
        <div className="absolute bottom-[38%] left-[10%] h-[28%]"><Messenger frame={1} /></div>

        <p className="absolute left-3 top-3 rounded border border-foreground/60 bg-card px-2 py-1 font-mono text-xs uppercase tracking-widest">
          The web ←
        </p>
        <p className="absolute right-3 top-3 rounded border border-foreground/60 bg-card px-2 py-1 font-mono text-xs uppercase tracking-widest">
          Company secrets
        </p>
      </div>

      <ul className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {OUTCOMES.map((o) => (
          <li key={o.label} className="flex items-start gap-3 rounded-lg border bg-card p-4">
            <span className={`mt-0.5 size-6 shrink-0 rounded border-2 ${o.swatch}`} aria-hidden />
            <span>
              <span className="block font-mono text-sm font-semibold uppercase tracking-wide">{o.label}</span>
              <span className="text-sm text-muted-foreground">{o.text}</span>
            </span>
          </li>
        ))}
      </ul>

      <div className="flex flex-wrap items-center justify-between gap-4">
        <p className="text-sm">
          <span className="font-semibold">Protect the secret. Keep the research.</span>{' '}
          <span className="text-muted-foreground">See the same agent run, with and without MOAT.</span>
        </p>
        <button
          type="button"
          onClick={onWatch}
          className="rounded-md border-2 border-foreground bg-amber-300 px-5 py-2 font-mono text-sm font-semibold uppercase tracking-wide shadow-[4px_4px_0_0] shadow-foreground hover:bg-amber-200"
        >
          Watch it work →
        </button>
      </div>
    </main>
  )
}

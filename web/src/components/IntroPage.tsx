// The intro screen ("01"): what MOAT is, in one picture.

import { useEffect, useRef } from 'react'
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

// The three trips in the loop: one per outcome, 3 s apart, then a 1.5 s pause (moat-* keyframes in index.css).
const TRIPS = [
  { outcome: 'pass', delay: 0, path: 'moat-out', bubble: '✓ pass', color: 'bg-emerald-200 border-emerald-700 text-emerald-900' },
  { outcome: 'rewrite', delay: 3, path: 'moat-out', bubble: '✎ rewritten', color: 'bg-amber-200 border-amber-700 text-amber-900' },
  { outcome: 'block', delay: 6, path: 'moat-back', bubble: '✕ blocked', color: 'bg-red-200 border-red-700 text-red-900' },
] as const

// One messenger's trip: the outer box moves along the bridge, the inner one flips to face the
// castle on a block, the sprite itself flips between its two walk frames.
// The rewrite moment: a little note above the messenger where the secret part gets crossed out
// and replaced, a tiny version of the word diff on the Compare screen. Runs on the same timing
// as its messenger's trip, so it shows exactly while they wait at the guard.
function RewriteNote({ timing }: { timing: string }) {
  return (
    <span
      className="absolute top-[105%] left-1/2 whitespace-nowrap rounded-md border-2 border-foreground/80 bg-[#fffdf8] px-1 py-0.5 font-mono text-[9px] sm:-top-24 sm:px-2 sm:py-1 sm:text-xs shadow-[2px_2px_0_0] shadow-foreground/70"
      style={{ animation: `moat-note ${timing}`, opacity: 0 }}
    >
      <span className="hidden sm:inline">energy density </span>
      <span className="relative text-red-700">
        <span className="hidden sm:inline">above </span>360 Wh/kg
        {/* the strike line grows from left to right */}
        <span className="absolute left-0 top-1/2 h-0.5 -translate-y-1/2 bg-red-700" style={{ animation: `moat-strike ${timing}` }} />
      </span>{' '}
      <span className="font-semibold text-emerald-700" style={{ animation: `moat-fix ${timing}`, opacity: 0 }}>characteristics</span>
    </span>
  )
}

function Trip({ outcome, delay, path, bubble, color }: (typeof TRIPS)[number]) {
  const timing = `10.5s linear ${delay}s infinite both`   // same length as the whole loop
  return (
    <div className="absolute bottom-[34%] h-[28%] aspect-square" style={{ animation: `${path} ${timing}`, opacity: 0 }}>
      {outcome === 'rewrite' && <RewriteNote timing={timing} />}
      <span
        className={`absolute -top-6 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md border-2 px-1 py-0.5 font-mono text-[10px] font-bold sm:-top-10 sm:px-2 sm:py-1 sm:text-sm shadow-[2px_2px_0_0] shadow-foreground/70 ${color}`}
        style={{ animation: `moat-bubble ${timing}`, opacity: 0 }}
      >
        {bubble}
      </span>
      <div className="h-full" style={{ animation: path === 'moat-back' ? `moat-turn ${timing}` : undefined, transform: 'scaleX(-1)' }}>
        <div
          aria-hidden
          className="aspect-square h-full"
          style={{ ...PIXEL, backgroundImage: `url(${messenger})`, backgroundSize: '200% 100%',
                   animation: 'moat-walk 0.4s steps(1) infinite' }}
        />
      </div>
    </div>
  )
}

// The spy: whoever is watching the public web. Drawn as a tiny pixel grid (one letter = one
// pixel, "." = empty) so it matches the pixel art. Faces right, toward the castle.
const SPY_PIXELS = [
  '......HHHH......',
  '.....HHHHHH.....',
  '....HRRRRRRH....',
  '...HHHHHHHHHH...',
  '.....SSSSSS.....',
  '.....SGGSGGS....',
  '.....SSSSSS.....',
  '....CCCSSCCC....',
  '...CCCCCCCCCC...',
  '...CCCCCCCCCCCCS',
  '...CCCCCCCCCC...',
  '...BBBBBBBBBB...',
  '...CCCCDCCCCC...',
  '...CCCCDCCCCC...',
  '....CCCDCCCC....',
  '.....LL..LL.....',
  '.....LL..LL.....',
  '....KKK..KKK....',
]
const SPY_COLORS: Record<string, string> = {
  H: '#3a3f4b', R: '#8b2e2e', S: '#f2c9a0', G: '#1c2735', C: '#b08850',
  D: '#8a6a3c', B: '#5b4325', L: '#2d3340', K: '#1c2735',
}

function Spy() {
  return (
    <svg viewBox="0 0 24 18" className="h-full" shapeRendering="crispEdges" aria-hidden>
      {SPY_PIXELS.flatMap((row, y) =>
        [...row].map((c, x) => (c === '.' ? null : <rect key={`${x}-${y}`} x={x} y={y} width={1} height={1} fill={SPY_COLORS[c]} />)),
      )}
      {/* the magnifying glass turns around his hand (15.5, 9.5) to read passing notes */}
      <g style={{ transformBox: 'view-box', transformOrigin: '15.5px 9.5px', animation: 'moat-spy-look 10.5s linear infinite' }}
         className="motion-reduce:[animation:none]">
        <line x1={15.5} y1={9.5} x2={18.5} y2={8} stroke="#5b4325" strokeWidth={1} />
        <circle cx={20.5} cy={7} r={2.5} fill="#cfe8f7" fillOpacity={0.7} stroke="#1c2735" strokeWidth={0.8} />
      </g>
    </svg>
  )
}

// The signpost on the web side: where every query that gets out ends up.
function Signpost() {
  const board = 'relative whitespace-nowrap bg-amber-700 py-0.5 pl-3 pr-1.5 font-mono text-[8px] font-bold uppercase tracking-wider text-amber-50 sm:text-[11px]'
  const arrow = { clipPath: 'polygon(8px 0, 100% 0, 100% 100%, 8px 100%, 0 50%)' }   // points left, out to the web
  return (
    <div className="absolute bottom-[34%] left-[17%] h-[44%]" aria-hidden>
      <div className="absolute bottom-0 left-1/2 h-full w-[5px] -translate-x-1/2 bg-amber-900" />
      <div className="relative flex flex-col items-center gap-1 pt-1">
        <span className={board} style={arrow}>Search engine</span>
        <span className={board} style={arrow}>Public internet</span>
      </div>
    </div>
  )
}

const OUTCOMES = [
  { label: 'Pass', text: 'Harmless search? Let it through.', swatch: 'bg-emerald-200 border-emerald-700' },
  { label: 'Rewrite', text: 'Too revealing? Remove the clue.', swatch: 'bg-amber-200 border-amber-700' },
  { label: 'Block', text: 'Gives a secret away? Stop the search.', swatch: 'bg-red-200 border-red-700' },
]

export function IntroPage({ onWatch }: { onWatch: () => void }) {
  // The guard's lean and the messengers' trips are separate CSS animations. The browser can start
  // them a moment apart (e.g. while an image loads), so restart them all together once the scene
  // is on screen: then the guard always leans exactly while a messenger waits.
  const scene = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const restart = () => scene.current?.getAnimations({ subtree: true }).forEach((a) => { a.currentTime = 0 })
    const imgs = [...(scene.current?.querySelectorAll('img') ?? [])]
    Promise.all(imgs.map((i) => (i.complete ? null : new Promise((r) => (i.onload = r))))).then(restart)
  }, [])

  return (
    <main className="mx-auto flex w-full max-w-5xl flex-col gap-6 p-8">
      <SectionHeading
        eyebrow="01 / The checkpoint for AI search"
        title="Every search is a tiny leak."
        text="Your AI research agent reads company documents and searches the web. One query looks innocent; many together can give the company's secrets away. MOAT checks every search before it leaves."
      />

      {/* Wrapper for the scene (ref), so every animation inside restarts together and stays in sync. */}
      <div ref={scene} className="flex flex-col gap-3">
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

        {/* the web side (left): a signpost, and a spy reading whatever gets out */}
        <Signpost />
        <div className="absolute bottom-[34%] left-[2%] h-[28%]">
          <Spy />
          <span className="absolute -top-6 left-1/2 rounded-md border-2 border-foreground/80 bg-card px-1.5 font-mono text-sm font-bold motion-reduce:hidden sm:-top-8 sm:text-base"
                style={{ animation: 'moat-spy-huh 10.5s linear infinite', opacity: 0 }} aria-hidden>?</span>
        </div>

        {/* castle (right), guard at the gate (middle), messengers heading out to the web (left) */}
        <img src={castle} alt="" style={PIXEL} className="absolute bottom-[28%] right-[4%] h-[62%]" />
        {/* Leans from his feet (origin-bottom) while reading each note; still for reduced motion. */}
        {/* Two movements, one on each layer so they don't override each other:
            the wrapper turns him around on a block (moat-stop), the image leans while he reads
            every note (moat-check). Both run on the same 10.5 s loop as the messengers. Both stay still for reduced motion. */}
        <div className="absolute bottom-[34%] left-[48%] h-[28%] motion-safe:animate-[moat-stop_10.5s_linear_infinite]">
          <img src={guard} alt="" style={PIXEL}
               className="h-full origin-bottom motion-safe:animate-[moat-check_10.5s_linear_infinite]" />
        </div>
        {/* Still picture for people who turn off animations (motion-reduce), the loop for everyone else. */}
        <div className="motion-safe:hidden">
          <div className="absolute bottom-[34%] left-[36%] h-[28%]"><Messenger frame={0} /></div>
          <div className="absolute bottom-[38%] left-[10%] h-[28%]"><Messenger frame={1} /></div>
        </div>
        <div className="motion-reduce:hidden">
          {TRIPS.map((t) => <Trip key={t.outcome} {...t} />)}
        </div>

        <p className="absolute right-3 top-3 rounded border border-foreground/60 bg-card px-2 py-1 font-mono text-[10px] uppercase tracking-widest sm:text-xs">
          Your company<span className="hidden sm:inline"> · private docs</span>
        </p>
      </div>

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

// The intro screen ("01"): what MOAT is, in one picture.

import { useEffect, useRef, type ReactNode } from 'react'
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

// The loop runs on one 13 s clock (moat-* keyframes in index.css): scene 1 MOAT off, scene 2 MOAT on.
const LOOP = '13s linear infinite'

// A note above a messenger: the search query they carry. On phones there's no room above, so it
// sits below them (over the moat) and drops the words that don't change.
const NOTE = 'absolute top-[105%] left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md border-2 border-foreground/80 bg-[#fffdf8] px-1 py-0.5 font-mono text-[9px] shadow-[2px_2px_0_0] shadow-foreground/70 sm:-top-24 sm:px-2 sm:py-1 sm:text-xs'
const Extra = ({ children }: { children: string }) => <span className="hidden sm:inline">{children}</span>

// The real Tier 2 rewrite from #61, shortened: "pouch cell 272 Wh/kg patents" -> "pouch cell high energy density patents".
function OffNote() {
  return (
    <span className={NOTE} style={{ animation: `moat-off-note ${LOOP}`, opacity: 0 }}>
      <Extra>pouch cell </Extra><span className="font-semibold text-red-700">272 Wh/kg</span><Extra> patents</Extra>
    </span>
  )
}

function OnNote() {
  return (
    <span className={NOTE} style={{ animation: `moat-on-note ${LOOP}`, opacity: 0 }}>
      <Extra>pouch cell </Extra>
      <span className="relative text-red-700">
        272 Wh/kg
        {/* the strike line grows from left to right */}
        <span className="absolute left-0 top-1/2 h-0.5 -translate-y-1/2 bg-red-700" style={{ animation: `moat-strike ${LOOP}` }} />
      </span>{' '}
      <span className="font-semibold text-emerald-700" style={{ animation: `moat-fix ${LOOP}`, opacity: 0 }}>high energy density</span>
      <Extra> patents</Extra>
    </span>
  )
}

// One walking messenger: the outer box moves along the bridge (path), the sprite flips between
// its two walk frames. Faces left, out toward the web.
function Walker({ path, children }: { path: string; children: ReactNode }) {
  return (
    <div className="absolute bottom-[34%] h-[28%] aspect-square" style={{ animation: `${path} ${LOOP}`, opacity: 0 }}>
      {children}
      <div className="h-full" style={{ transform: 'scaleX(-1)' }}>
        <div aria-hidden className="aspect-square h-full"
             style={{ ...PIXEL, backgroundImage: `url(${messenger})`, backgroundSize: '200% 100%',
                      animation: 'moat-walk 0.4s steps(1) infinite' }} />
      </div>
    </div>
  )
}

// Small pop-up label (the guard's verdict, the spy's reaction).
const BUBBLE = 'absolute whitespace-nowrap rounded-md border-2 px-1 py-0.5 font-mono text-[10px] font-bold shadow-[2px_2px_0_0] shadow-foreground/70 sm:px-2 sm:py-1 sm:text-sm'

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
      <g style={{ transformBox: 'view-box', transformOrigin: '15.5px 9.5px', animation: `moat-spy-look ${LOOP}` }}
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
    <div className="absolute bottom-[34%] left-[23%] h-[44%]" aria-hidden>
      <div className="absolute bottom-0 left-1/2 h-full w-[5px] -translate-x-1/2 bg-amber-900" />
      <div className="relative flex flex-col items-center gap-1 pt-1">
        <span className={board} style={arrow}>Search engine</span>
        <span className={board} style={arrow}>Public internet</span>
      </div>
    </div>
  )
}

// A pixel cloud: a small block sitting on a wider one. Drifts across the sky; a negative delay
// starts each one part-way along, so the sky isn't empty when the page opens.
const CLOUDS = [
  { top: '10%', width: '11%', duration: 200, delay: -60 },
  { top: '22%', width: '8%', duration: 260, delay: -180 },
  { top: '6%', width: '9%', duration: 230, delay: -15 },
]

function Cloud({ top, width, duration, delay }: (typeof CLOUDS)[number]) {
  return (
    <div className="absolute aspect-[3/1] motion-reduce:!animate-none" aria-hidden
         style={{ top, width, left: '20%', animation: `moat-drift ${duration}s linear ${delay}s infinite` }}>
      <div className="absolute bottom-1/2 left-[25%] h-1/2 w-[40%] bg-white" />
      <div className="absolute bottom-0 h-1/2 w-full bg-white" />
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
        aria-label="Two scenes. Without MOAT, a search with a secret number walks out of the castle and a spy reads the number. With MOAT, a guard rewrites the same search so the spy learns nothing."
        className="relative aspect-[16/7] w-full overflow-hidden rounded-xl border-2 border-foreground/80 bg-sky-100 shadow-[6px_6px_0_0] shadow-foreground/80"
      >
        {/* ground, moat, bridge */}
        <div className="absolute inset-x-0 bottom-[30%] h-[8%] bg-emerald-300/70" />
        <div className="absolute inset-x-0 bottom-0 h-[30%] bg-sky-600/80" />
        {CLOUDS.map((c, i) => <Cloud key={i} {...c} />)}
        <div className="absolute bottom-[30%] left-[30%] h-[5%] w-[41%] bg-amber-800" />

        {/* the web side (left): a signpost, and a spy reading whatever gets out */}
        <Signpost />
        <div className="absolute bottom-[34%] left-[2%] h-[28%]">
          <Spy />
          {/* scene 1: he got the number. scene 2: he got nothing useful. */}
          <span className={`${BUBBLE} z-10 -top-5 left-[10%] border-red-700 bg-red-200 text-red-900 motion-reduce:hidden sm:-top-9`}
                style={{ animation: `moat-spy-got ${LOOP}`, opacity: 0 }} aria-hidden><span className="hidden lg:inline">the secret: </span>272 Wh/kg!</span>
          <span className={`${BUBBLE} z-10 -top-5 left-[30%] border-foreground/80 bg-card motion-reduce:hidden sm:-top-9`}
                style={{ animation: `moat-spy-huh ${LOOP}`, opacity: 0 }} aria-hidden>?<Extra> nothing useful</Extra></span>
        </div>

        {/* castle (right), guard at the gate (middle), messengers heading out to the web (left) */}
        <img src={castle} alt="" style={PIXEL} className="absolute bottom-[28%] right-[1.5%] h-[62%]" />
        {/* Two movements, one on each layer so they don't override each other: the wrapper fades
            him out in scene 1 (MOAT off), the image leans from his feet while he reads the note.
            Both stay still (and he stays visible) for reduced motion. */}
        <div className="absolute bottom-[34%] left-[48%] h-[28%] motion-safe:animate-[moat-guard_13s_linear_infinite]">
          <img src={guard} alt="" style={PIXEL}
               className="h-full origin-bottom motion-safe:animate-[moat-check_13s_linear_infinite]" />
        </div>
        {/* Still picture for people who turn off animations (motion-reduce), the loop for everyone else. */}
        <div className="motion-safe:hidden">
          <div className="absolute bottom-[34%] left-[36%] h-[28%]"><Messenger frame={0} /></div>
          <div className="absolute bottom-[38%] left-[10%] h-[28%]"><Messenger frame={1} /></div>
        </div>
        <div className="motion-reduce:hidden">
          <Walker path="moat-off-walk"><OffNote /></Walker>
          <Walker path="moat-on-walk">
            <OnNote />
            <span className={`${BUBBLE} -top-6 left-1/2 -translate-x-1/2 border-amber-700 bg-amber-200 text-amber-900 sm:-top-10`}
                  style={{ animation: `moat-bubble ${LOOP}`, opacity: 0 }}>✎ rewritten</span>
          </Walker>
        </div>

        <div className="absolute left-3 top-3 font-mono text-[10px] font-bold uppercase tracking-widest motion-reduce:hidden sm:text-xs">
          <span className="absolute whitespace-nowrap rounded border-2 border-red-700 bg-red-200 px-2 py-1 text-red-900" style={{ animation: `moat-tag-off ${LOOP}` }}>MOAT off<span style={{ animation: `moat-tag-leaked ${LOOP}`, maxWidth: 0, opacity: 0 }} className="hidden overflow-hidden whitespace-nowrap align-bottom sm:inline-block"> · secret leaked</span></span>
          <span className="absolute whitespace-nowrap rounded border-2 border-emerald-700 bg-emerald-200 px-2 py-1 text-emerald-900" style={{ animation: `moat-tag-on ${LOOP}`, opacity: 0 }}>MOAT on<span style={{ animation: `moat-tag-kept ${LOOP}`, maxWidth: 0, opacity: 0 }} className="hidden overflow-hidden whitespace-nowrap align-bottom sm:inline-block"> · secret kept</span></span>
        </div>
        <p className="absolute right-3 top-3 rounded border border-foreground/60 bg-card px-2 py-1 font-mono text-[10px] uppercase tracking-widest sm:text-xs">
          Your company<span className="hidden sm:inline"> · private docs</span>
        </p>
      </div>

      </div>

      {/* The animation shows one of MOAT's moves (a rewrite); these name all three, in the same
          words the Compare screen uses on every hop. */}
      {/* Extra space above (mt-6) and little below (gap-3), so the heading groups with the cards, not the scene. */}
      <section className="mt-6 flex flex-col gap-3">
      <h3 className="font-mono text-xs font-semibold uppercase tracking-widest text-muted-foreground">What MOAT can do with each search</h3>
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
      </section>

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

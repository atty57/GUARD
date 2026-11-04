// "What an outsider can infer": one bar per secret, on the scale of what anyone would assume anyway.
import type { LaneView, PriorInfo } from '@/lib/runState'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

type Range = [number, number]

// One secret's policy limits, from GET /api/policy (computed by guard/policy.py).
export type SecretLimit = {
  unit: string
  prior: Range
  resolution: number
  min_width: number
  bits_budget: number
  threshold: number | null
}

// Why a guess breaks the policy. Mirrors `violates()` in guard/policy.py: too narrow,
// too many bits gained, or it reveals which side of the legal threshold the value is on.
// Empty list = within policy.
function policyBreaches(guess: Range, limit: SecretLimit): string[] {
  const out: string[] = []
  const width = guess[1] - guess[0]
  const priorWidth = limit.prior[1] - limit.prior[0]
  if (width < limit.min_width) {
    out.push(`Narrower than the policy allows (must stay at least ${limit.min_width} ${limit.unit} wide)`)
  }
  // Rounded to 3 decimals like interval_metrics() in guard/policy.py, so a guess sitting
  // exactly at the limit gets the same verdict here as in the guard.
  const bits = Math.round(Math.max(Math.log2(priorWidth / Math.max(width, limit.resolution)), 0) * 1000) / 1000
  if (bits > limit.bits_budget) {
    out.push('Gives away more than the policy\'s information budget for this secret')
  }
  if (limit.threshold !== null) {
    const t = limit.threshold
    // A range ending exactly at the threshold ("below 265" = [lo, 265]) counts as below it,
    // matching reveals_threshold_side() in guard/policy.py (#47).
    const priorSettled = limit.prior[0] >= t || limit.prior[1] <= t
    if (!priorSettled && guess[0] >= t) out.push(`Reveals it is above the ${t} ${limit.unit} legal threshold`)
    else if (!priorSettled && guess[1] <= t) out.push(`Reveals it is below the ${t} ${limit.unit} legal threshold`)
  }
  return out
}

// rank: how bad it is, so a lane can show its worst secret (see laneVerdict).
const EXPOSED = { label: 'Exposed', rank: 2, bar: 'bg-red-500', text: 'text-red-700', badge: 'bg-red-500/15 text-red-700' }
const NARROWED = { label: 'Narrowed', rank: 1, bar: 'bg-amber-500', text: 'text-amber-700', badge: 'bg-amber-500/15 text-amber-700' }
const PROTECTED = { label: 'Protected', rank: 0, bar: 'bg-emerald-500', text: 'text-emerald-700', badge: 'bg-emerald-500/15 text-emerald-700' }

// Exposed = breaks this secret's policy. Narrowed = within policy but under half the prior range.
// Without policy limits for this secret, fall back to a fixed share of the prior range.
function level(guess: Range, prior: Range, limit?: SecretLimit) {
  const ratio = (guess[1] - guess[0]) / (prior[1] - prior[0])
  if (limit) {
    const breaches = policyBreaches(guess, limit)
    if (breaches.length) return { ...EXPOSED, breaches }
    return { ...(ratio < 0.5 ? NARROWED : PROTECTED), breaches }
  }
  if (ratio < 0.15) return { ...EXPOSED, breaches: [] }
  return { ...(ratio < 0.5 ? NARROWED : PROTECTED), breaches: [] }
}

const pretty = (dim: string) => dim.replace(/_/g, ' ')
// "372–372" reads oddly; a one-point range is just "372".
const span = (r: Range) => (r[0] === r[1] ? `${r[0]}` : `${r[0]}–${r[1]}`)

// The attacker's guess per secret: the latest observer estimate, else the latest committed interval.
function laneGuesses(lane: LaneView): Map<string, Range> {
  const guesses = new Map<string, Range>()
  for (const i of lane.hops.flatMap((h) => h.intervals)) if (i.committed) guesses.set(i.dim, i.after)
  for (const o of lane.observer) guesses.set(o.attr, [o.p10, o.p90])
  return guesses
}

// One word for a whole lane, shown next to its title: its worst secret ("Exposed" if any secret
// breaks the policy). null while nothing is known yet. Same rules as the meter below, so the
// badge and the meter can never disagree.
// Lives here (not in its own file) so it shares the meter's rules; only costs hot-reload speed.
// eslint-disable-next-line react-refresh/only-export-components
export function laneVerdict(lane: LaneView, priors: Record<string, PriorInfo>, limits: Record<string, SecretLimit>) {
  let worst: typeof EXPOSED | typeof NARROWED | typeof PROTECTED | null = null
  for (const [dim, guess] of laneGuesses(lane)) {
    const info = priors[dim]
    if (!info) continue
    const lv = level(guess, info.prior, limits[dim])
    if (!worst || lv.rank > worst.rank) worst = lv
  }
  return worst && { label: worst.label, badge: worst.badge }
}

export function LeakMeter({ lane, priors, limits }: {
  lane: LaneView
  priors: Record<string, PriorInfo>
  limits: Record<string, SecretLimit>
}) {
  const prevented = lane.hops.flatMap((h) => h.intervals).filter((i) => i.status === 'prevented')
  const guesses = laneGuesses(lane)

  return (
    <Card>
      <CardHeader>
        <CardTitle>What an outsider can infer</CardTitle>
        <CardDescription>From the search queries alone</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {guesses.size === 0 && <p className="text-sm text-muted-foreground">Nothing yet.</p>}

        {[...guesses].map(([dim, guess]) => {
          const info = priors[dim]
          if (!info) {
            return <p key={dim} className="text-sm">{pretty(dim)}: {span(guess)}</p>
          }
          const { prior, unit } = info
          const pct = (v: number) => Math.min(100, Math.max(0, ((v - prior[0]) / (prior[1] - prior[0])) * 100))
          const lv = level(guess, prior, limits[dim])
          const stopped = prevented.filter((p) => p.dim === dim)

          return (
            <div key={dim} className="flex flex-col gap-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium first-letter:uppercase">{pretty(dim)}</span>
                <Badge className={lv.badge}>{lv.label}</Badge>
              </div>

              {/* The hero number: the attacker's guess, big and colored by how bad it is. */}
              <p className={`text-4xl font-bold tracking-tight tabular-nums ${lv.text}`}>
                {span(guess)} <span className="text-base font-normal text-muted-foreground">{unit}</span>
              </p>

              <div className="relative h-4 rounded-full bg-muted" role="img"
                   aria-label={`Attacker's guess ${guess[0]} to ${guess[1]} ${unit}, out of ${prior[0]} to ${prior[1]}`}>
                {stopped.map((p, i) => (
                  <div key={i} title={`MOAT prevented ${span(p.after)} ${unit}`}
                       className="absolute inset-y-0 rounded-full border border-dashed border-red-400/80"
                       style={{ left: `${pct(p.after[0])}%`, width: `${pct(p.after[1]) - pct(p.after[0])}%` }} />
                ))}
                <div className={`absolute inset-y-0.5 rounded-full ${lv.bar} transition-all duration-500`}
                     style={{ left: `${pct(guess[0])}%`, width: `max(4px, ${pct(guess[1]) - pct(guess[0])}%)` }} />
              </div>

              <div className="flex justify-between font-mono text-xs text-muted-foreground tabular-nums">
                <span>{prior[0]}</span>
                <span>{prior[1]} {unit}</span>
              </div>

              {lv.breaches.map((b) => (
                <p key={b} className="text-xs text-red-700">{b}</p>
              ))}

              {/* Good news, so green: MOAT kept the guess from getting this narrow. */}
              {stopped.map((p, i) => (
                <p key={i} className="text-xs text-emerald-700">
                  MOAT prevented this from narrowing to {span(p.after)} {unit}
                </p>
              ))}
            </div>
          )
        })}
      </CardContent>
    </Card>
  )
}

// "What an outsider can infer": one bar per secret, on the scale of what anyone would assume anyway.
import type { LaneView, PriorInfo } from '@/lib/runState'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

type Range = [number, number]

// How much of the prior range is still possible. Small = the secret is pinned down.
function level(guess: Range, prior: Range) {
  const ratio = (guess[1] - guess[0]) / (prior[1] - prior[0])
  if (ratio < 0.15) return { label: 'Exposed', bar: 'bg-red-500', badge: 'bg-red-500/15 text-red-400' }
  if (ratio < 0.5) return { label: 'Narrowed', bar: 'bg-amber-500', badge: 'bg-amber-500/15 text-amber-400' }
  return { label: 'Protected', bar: 'bg-emerald-500', badge: 'bg-emerald-500/15 text-emerald-400' }
}

const pretty = (dim: string) => dim.replace(/_/g, ' ')

export function LeakMeter({ lane, priors }: { lane: LaneView; priors: Record<string, PriorInfo> }) {
  const intervals = lane.hops.flatMap((h) => h.intervals)
  const prevented = intervals.filter((i) => i.status === 'prevented')

  // The attacker's guess per secret: the latest observer estimate, else the latest committed interval.
  const guesses = new Map<string, Range>()
  for (const i of intervals) if (i.committed) guesses.set(i.dim, i.after)
  for (const o of lane.observer) guesses.set(o.attr, [o.p10, o.p90])

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
            return <p key={dim} className="text-sm">{pretty(dim)}: {guess[0]}–{guess[1]}</p>
          }
          const { prior, unit } = info
          const pct = (v: number) => Math.min(100, Math.max(0, ((v - prior[0]) / (prior[1] - prior[0])) * 100))
          const lv = level(guess, prior)
          const stopped = prevented.filter((p) => p.dim === dim)

          return (
            <div key={dim} className="flex flex-col gap-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium capitalize">{pretty(dim)}</span>
                <Badge className={lv.badge}>{lv.label}</Badge>
              </div>

              <div className="relative h-4 rounded-full bg-muted" role="img"
                   aria-label={`Attacker's guess ${guess[0]} to ${guess[1]} ${unit}, out of ${prior[0]} to ${prior[1]}`}>
                {stopped.map((p, i) => (
                  <div key={i} title={`MOAT prevented ${p.after[0]}–${p.after[1]} ${unit}`}
                       className="absolute inset-y-0 rounded-full border border-dashed border-red-400/80"
                       style={{ left: `${pct(p.after[0])}%`, width: `${pct(p.after[1]) - pct(p.after[0])}%` }} />
                ))}
                <div className={`absolute inset-y-0.5 rounded-full ${lv.bar} transition-all duration-500`}
                     style={{ left: `${pct(guess[0])}%`, width: `max(4px, ${pct(guess[1]) - pct(guess[0])}%)` }} />
              </div>

              <div className="flex justify-between font-mono text-xs text-muted-foreground tabular-nums">
                <span>{prior[0]}</span>
                <span className="text-foreground">guess: {guess[0]}–{guess[1]} {unit}</span>
                <span>{prior[1]}</span>
              </div>

              {stopped.map((p, i) => (
                <p key={i} className="text-xs text-red-400">
                  MOAT prevented this from narrowing to {p.after[0]}–{p.after[1]} {unit}
                </p>
              ))}
            </div>
          )
        })}
      </CardContent>
    </Card>
  )
}

// The headline chart: how much each condition leaks (x) against how good its report stays (y).
// Plus MOAT at five strictness levels (the tau sweep) as a line, with a slider to pick one.
// Drawn as plain SVG: no chart library, so every line here can be read and changed directly.
import { useState } from 'react'
import type { ConditionResult, Estimate } from '@/evalTypes'
import { CONDITIONS } from '@/evalTypes'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

// Which two numbers the chart plots. Change them here and the whole chart follows.
// (Waiting on Hyeseong to confirm these are the right headline metrics.)
const X = { label: 'Leak score', pick: (c: ConditionResult) => c.leakage.numeric_gain }
const Y = { label: 'Report quality', pick: (c: ConditionResult) => c.utility.rubric }

// Drawing area in SVG units. The SVG scales to the card's width via viewBox.
const W = 640, H = 340
const M = { left: 56, right: 20, top: 20, bottom: 48 }

const pct = (x: number) => `${Math.round(x * 100)}%`
const MOAT_COLOR = CONDITIONS.find((c) => c.id === 'moat')!.color

// A range [lo, hi] that covers every value with a little room, rounded out to steps of 0.05.
function niceRange(values: number[], floor?: number, ceil?: number): [number, number] {
  let lo = Math.floor((Math.min(...values) - 0.02) / 0.05) * 0.05
  let hi = Math.ceil((Math.max(...values) + 0.02) / 0.05) * 0.05
  if (floor !== undefined) lo = Math.max(lo, floor)
  if (ceil !== undefined) hi = Math.min(hi, ceil)
  return [lo, hi]
}

export function HeadlineChart({ conditions, sweep }: { conditions: ConditionResult[]; sweep: ConditionResult[] }) {
  // The slider starts on the strictness the four conditions were run at.
  const policyTau = conditions[0]?.tau
  const [picked, setPicked] = useState(Math.max(0, sweep.findIndex((p) => p.tau === policyTau)))

  // Axis ranges cover every point and every confidence whisker.
  const all = [...conditions, ...sweep]
  const xs = all.flatMap((c) => X.pick(c).ci95), ys = all.flatMap((c) => Y.pick(c).ci95)
  // Start at 0 ("nothing leaked"), or lower only if some value really is negative.
  const [x0, x1] = niceRange([0, ...xs], Math.min(0, ...xs) < 0 ? undefined : 0)
  const [y0, y1] = niceRange(ys, 0, 1)
  const sx = (v: number) => M.left + ((v - x0) / (x1 - x0)) * (W - M.left - M.right)
  const sy = (v: number) => H - M.bottom - ((v - y0) / (y1 - y0)) * (H - M.top - M.bottom)
  // Tick marks on round numbers only (0%, 10%, 20%…), whatever the edges of the range are.
  const ticks = (lo: number, hi: number) => {
    const step = hi - lo > 0.3 ? 0.1 : 0.05
    const out: number[] = []
    for (let t = Math.ceil(lo / step - 1e-9) * step; t <= hi + 1e-9; t += step) out.push(+t.toFixed(2))
    return out
  }

  const p = sweep[picked]

  return (
    <Card>
      <CardHeader>
        <CardTitle>Leak vs report quality</CardTitle>
        <CardDescription>
          Best is the top-left corner: little leaked, good report. Lines through each dot are the 95% confidence range.
          The green line is MOAT at five strictness levels (τ).
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img"
             aria-label={`Leak score against report quality for ${conditions.length} conditions and MOAT at ${sweep.length} strictness levels`}>
          {/* grid lines and axis labels */}
          {ticks(x0, x1).map((t) => (
            <g key={`x${t}`}>
              <line x1={sx(t)} x2={sx(t)} y1={M.top} y2={H - M.bottom} className="stroke-border" />
              <text x={sx(t)} y={H - M.bottom + 16} textAnchor="middle" className="fill-muted-foreground text-[11px]">{pct(t)}</text>
            </g>
          ))}
          {ticks(y0, y1).map((t) => (
            <g key={`y${t}`}>
              <line x1={M.left} x2={W - M.right} y1={sy(t)} y2={sy(t)} className="stroke-border" />
              <text x={M.left - 8} y={sy(t) + 4} textAnchor="end" className="fill-muted-foreground text-[11px]">{pct(t)}</text>
            </g>
          ))}
          <text x={(M.left + W - M.right) / 2} y={H - 8} textAnchor="middle" className="fill-muted-foreground text-xs">
            {X.label} → more leaked
          </text>
          <text transform={`translate(14 ${(M.top + H - M.bottom) / 2}) rotate(-90)`} textAnchor="middle"
                className="fill-muted-foreground text-xs">
            {Y.label} → better
          </text>

          {/* MOAT tau sweep: a line through the five points, the picked one ringed */}
          <polyline fill="none" stroke={MOAT_COLOR} strokeWidth={1.5} strokeDasharray="4 3" opacity={0.7}
                    points={sweep.map((c) => `${sx(X.pick(c).mean)},${sy(Y.pick(c).mean)}`).join(' ')} />
          {sweep.map((c, i) => (
            <g key={c.tau}>
              <circle cx={sx(X.pick(c).mean)} cy={sy(Y.pick(c).mean)} r={i === picked ? 8 : 3}
                      fill={i === picked ? 'none' : MOAT_COLOR} stroke={MOAT_COLOR} strokeWidth={i === picked ? 2 : 0} />
              {(i === 0 || i === sweep.length - 1 || i === picked) && (
                <text x={sx(X.pick(c).mean) + 10} y={sy(Y.pick(c).mean) + 4} className="fill-muted-foreground text-[10px]">
                  τ {c.tau}
                </text>
              )}
            </g>
          ))}

          {/* the four conditions: dot + confidence whiskers in both directions */}
          {CONDITIONS.map(({ id, label, color }) => {
            const c = conditions.find((x) => x.condition === id)
            if (!c) return null
            const ex: Estimate = X.pick(c), ey: Estimate = Y.pick(c)
            return (
              <g key={id}>
                <line x1={sx(ex.ci95[0])} x2={sx(ex.ci95[1])} y1={sy(ey.mean)} y2={sy(ey.mean)} stroke={color} strokeWidth={1.5} opacity={0.6} />
                <line x1={sx(ex.mean)} x2={sx(ex.mean)} y1={sy(ey.ci95[0])} y2={sy(ey.ci95[1])} stroke={color} strokeWidth={1.5} opacity={0.6} />
                <circle cx={sx(ex.mean)} cy={sy(ey.mean)} r={6} fill={color}>
                  <title>{`${label}: ${X.label} ${pct(ex.mean)}, ${Y.label} ${pct(ey.mean)}`}</title>
                </circle>
              </g>
            )
          })}
        </svg>

        {/* Legend instead of labels on the dots: MOAT and MOAT + prompt can land almost on top of each other. */}
        <ul className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
          {CONDITIONS.map(({ id, label, color }) => (
            <li key={id} className="flex items-center gap-2">
              <span className="inline-block size-2.5 rounded-full" style={{ background: color }} aria-hidden />
              {label}
            </li>
          ))}
          <li className="flex items-center gap-2 text-muted-foreground">
            <svg width="22" height="8" aria-hidden><line x1="0" x2="22" y1="4" y2="4" stroke={MOAT_COLOR} strokeWidth={1.5} strokeDasharray="4 3" /></svg>
            MOAT at other strictness levels
          </li>
        </ul>

        {/* strictness slider: picks one point of the MOAT line and shows its numbers */}
        {p && (
          <div className="flex flex-col gap-2 rounded-lg border p-4">
            <label htmlFor="tau" className="flex items-center justify-between text-sm font-medium">
              <span>MOAT strictness (τ = {p.tau}{p.tau === policyTau ? ', the policy default' : ''})</span>
            </label>
            <input id="tau" type="range" min={0} max={sweep.length - 1} step={1} value={picked}
                   onChange={(e) => setPicked(Number(e.target.value))} className="w-full accent-emerald-500"
                   aria-valuetext={`tau ${p.tau}`} />
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>looser (τ {sweep[0].tau})</span><span>stricter (τ {sweep[sweep.length - 1].tau})</span>
            </div>
            <dl className="grid grid-cols-2 gap-3 pt-2 text-sm sm:grid-cols-4">
              <div><dt className="text-muted-foreground">{X.label}</dt><dd className="font-medium tabular-nums">{pct(X.pick(p).mean)}</dd></div>
              <div><dt className="text-muted-foreground">{Y.label}</dt><dd className="font-medium tabular-nums">{pct(Y.pick(p).mean)}</dd></div>
              <div><dt className="text-muted-foreground">Drafts blocked</dt><dd className="font-medium tabular-nums">{pct(p.flow.block_rate)}</dd></div>
              <div><dt className="text-muted-foreground">Searches skipped</dt><dd className="font-medium tabular-nums">{pct(p.flow.skip_rate)}</dd></div>
            </dl>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

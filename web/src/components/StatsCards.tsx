// Two small cards: are the differences real (statistical tests), and how fast is the guard.
import type { Comparison, EvalResults } from '@/evalTypes'
import { CONDITIONS } from '@/evalTypes'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

// Readable names for the metric paths the comparisons use (e.g. "leakage.numeric_gain").
const METRIC: Record<string, string> = {
  'leakage.numeric_gain': 'Leak score',
  'leakage.width_ratio': 'Guess width',
  'leakage.bits': 'Bits leaked',
  'leakage.hit_within_2pct': 'Exact guesses',
  'leakage.categorical_gain': 'Name guesses',
  'leakage.intent_similarity': 'Task guessed',
  'utility.citation_recall': 'Patents cited',
  'utility.rubric': 'Report quality',
}
const name = (id: string) => CONDITIONS.find((c) => c.id === id)?.label ?? id
// p values this small all read the same; past 3 decimals is just noise for a reader.
const pValue = (p: number) => (p < 0.001 ? '< 0.001' : p.toFixed(3))
// The effect (a − b) in percentage points, with an explicit + or − sign.
const points = (x: number) => `${x > 0 ? '+' : x < 0 ? '−' : ''}${Math.abs(Math.round(x * 100))} pts`

function ComparisonRow({ c }: { c: Comparison }) {
  const significant = c.p_holm < 0.05
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 border-b py-2 text-sm last:border-0">
      <span>
        <span className="font-medium">{name(c.a)} vs {name(c.b)}</span>
        <span className="text-muted-foreground"> · {METRIC[c.metric] ?? c.metric}</span>
        <span className="block text-xs text-muted-foreground tabular-nums">
          difference {points(c.effect)} · p = {pValue(c.p_holm)} ({c.test})
        </span>
      </span>
      <Badge className={significant ? 'bg-emerald-500/15 text-emerald-700' : 'bg-secondary text-muted-foreground'}>
        {significant ? 'significant' : 'not significant'}
      </Badge>
    </li>
  )
}

const TIERS = [
  ['tier0', 'Tier 0 · exact checks'],
  ['tier1', 'Tier 1 · ledger'],
  ['tier2', 'Tier 2 · rewrite'],
  ['total', 'Total per query'],
] as const

export function StatsCards({ r }: { r: EvalResults }) {
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Are the differences real?</CardTitle>
          <CardDescription>
            Paired tests on the same tasks, corrected for running several tests (Holm). Below p = 0.05 means the
            difference is unlikely to be chance.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul>{r.comparisons.map((c) => <ComparisonRow key={`${c.metric}-${c.a}-${c.b}`} c={c} />)}</ul>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Guard speed</CardTitle>
          <CardDescription>
            Time the guard adds to each query, in milliseconds. p50 = a typical query, p95 = a slow one.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="py-2 font-medium">Step</th>
                <th className="py-2 text-right font-medium">p50</th>
                <th className="py-2 text-right font-medium">p95</th>
              </tr>
            </thead>
            <tbody>
              {/* Tiers missing from the file (e.g. no Tier 2 yet) are simply left out. */}
              {TIERS.filter(([id]) => r.latency_ms[id]).map(([id, label]) => (
                <tr key={id} className={`border-b last:border-0 ${id === 'total' ? 'font-medium' : ''}`}>
                  <td className="py-2">{label}</td>
                  <td className="py-2 text-right tabular-nums">{r.latency_ms[id]!.p50} ms</td>
                  <td className="py-2 text-right tabular-nums">{r.latency_ms[id]!.p95} ms</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-xs text-muted-foreground">
            Queries that needed a Tier 2 rewrite: {Math.round(r.tier2_escalation_rate * 100)}%
          </p>
        </CardContent>
      </Card>
    </div>
  )
}

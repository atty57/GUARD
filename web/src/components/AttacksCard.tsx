// How well the guard does against scripted attacks, one bar per kind of leak, plus how often it
// wrongly blocks harmless queries. Both matter: a guard that blocks everything would score 100%
// on attacks and be useless.
import type { AttackResult } from '@/evalTypes'
import { LEAK_LABEL } from '@/lib/labels'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

const pct = (x: number) => `${Math.round(x * 100)}%`

export function AttacksCard({ attacks, benign }: {
  attacks: AttackResult[]
  benign: { n: number; false_positive_rate: number }
}) {
  // Strongest first, so the weakest spots end up at the bottom where the eye lands last.
  const rows = [...attacks].sort((a, b) => b.recall - a.recall)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Scripted attacks</CardTitle>
        <CardDescription>
          Queries written on purpose to leak a secret in a specific way. Caught = blocked or rewritten before it was sent.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <ul className="flex flex-col gap-3">
          {rows.map((a) => (
            <li key={a.leak_type} className="grid grid-cols-[minmax(7rem,10rem)_1fr_auto] items-center gap-3 text-sm">
              <span className="first-letter:uppercase">{LEAK_LABEL[a.leak_type] ?? a.leak_type}</span>
              {/* The bar: a grey track with a green fill as wide as the catch rate. */}
              <div className="h-2.5 rounded-full bg-muted" role="img" aria-label={`${pct(a.recall)} caught`}>
                <div className="h-full rounded-full bg-emerald-500" style={{ width: pct(a.recall) }} />
              </div>
              <span className="tabular-nums">
                {pct(a.recall)}{' '}
                <span className="text-xs text-muted-foreground">({Math.round(a.recall * a.n)}/{a.n})</span>
              </span>
            </li>
          ))}
        </ul>

        <div className="flex flex-wrap items-baseline justify-between gap-2 rounded-lg border px-4 py-3 text-sm">
          <span>
            Harmless queries wrongly blocked
            <span className="block text-xs text-muted-foreground">e.g. public numbers like an export threshold · lower is better</span>
          </span>
          <span className="text-lg font-semibold tabular-nums">
            {pct(benign.false_positive_rate)}{' '}
            <span className="text-xs font-normal text-muted-foreground">
              ({Math.round(benign.false_positive_rate * benign.n)}/{benign.n})
            </span>
          </span>
        </div>
      </CardContent>
    </Card>
  )
}

// The four conditions side by side: how much leaked, how useful the report stayed, what the
// guard did. Every main number shows its mean and, smaller, its 95% confidence range.
import type { ConditionResult, Estimate } from '@/evalTypes'
import { CONDITIONS } from '@/evalTypes'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

// 0.31 -> "31%", -0.03 -> "-3%". Most numbers here are shares in [0, 1]; the leak score
// (numeric_gain) is a difference and can be negative, so the sign is kept.
const pct = (x: number) => `${Math.round(x * 100)}%`

function Cell({ e }: { e: Estimate }) {
  return (
    <td className="px-3 py-2 text-right tabular-nums">
      {pct(e.mean)}
      <span className="block text-xs text-muted-foreground">{pct(e.ci95[0])}–{pct(e.ci95[1])}</span>
    </td>
  )
}

// Column headers say which direction is good, so nobody has to guess.
const COLUMNS = [
  { label: 'Leak score', hint: 'lower is better' },
  { label: 'Exact guesses', hint: 'within ±2%, lower is better' },
  { label: 'Report quality', hint: 'higher is better' },
  { label: 'Patents cited', hint: 'higher is better' },
  { label: 'Drafts blocked', hint: 'by the guard' },
]

export function ConditionsTable({ conditions }: { conditions: ConditionResult[] }) {
  // Always the same row order (No guard → MOAT + prompt), whatever order the file uses.
  const rows = CONDITIONS.map((c) => ({ ...c, r: conditions.find((x) => x.condition === c.id) }))
    .filter((row): row is typeof row & { r: ConditionResult } => row.r !== undefined)

  return (
    <Card>
      <CardHeader>
        <CardTitle>The four conditions</CardTitle>
        <CardDescription>
          Same tasks, documents and search for each. Leak score = how much closer the queries got an outsider to the
          secret numbers. Small numbers under each value are the 95% confidence range.
        </CardDescription>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th className="px-3 py-2 font-medium">Condition</th>
              {COLUMNS.map((c) => (
                <th key={c.label} className="px-3 py-2 text-right font-medium">
                  {c.label}
                  <span className="block text-xs font-normal">{c.hint}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ id, label, color, r }) => (
              <tr key={id} className="border-b last:border-0">
                <th scope="row" className="px-3 py-2 text-left font-medium">
                  <span className="mr-2 inline-block size-2.5 rounded-full" style={{ background: color }} aria-hidden />
                  {label}
                  <span className="block text-xs font-normal text-muted-foreground">{r.n_runs} runs</span>
                </th>
                <Cell e={r.leakage.numeric_gain} />
                <Cell e={r.leakage.hit_within_2pct} />
                <Cell e={r.utility.rubric} />
                <Cell e={r.utility.citation_recall} />
                <td className="px-3 py-2 text-right tabular-nums">{pct(r.flow.block_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  )
}

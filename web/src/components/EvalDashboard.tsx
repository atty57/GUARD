// The Results screen (S5): the experiment results from GET /api/eval.
// It only reads precomputed numbers; it never starts a run.
import { useEffect, useState } from 'react'
import type { EvalResults } from '@/evalTypes'
import { Badge } from '@/components/ui/badge'
import { SectionHeading } from '@/components/SectionHeading'
import { ConditionsTable } from '@/components/ConditionsTable'
import { HeadlineChart } from '@/components/HeadlineChart'
import { AttacksCard } from '@/components/AttacksCard'
import { StatsCards } from '@/components/StatsCards'

// What the screen can be showing: still loading, an error message, or the results.
type Load = { state: 'loading' } | { state: 'error'; message: string } | { state: 'ready'; data: EvalResults }

export function EvalDashboard() {
  const [load, setLoad] = useState<Load>({ state: 'loading' })

  useEffect(() => {
    fetch('/api/eval')
      .then(async (res) => {
        if (res.ok) return setLoad({ state: 'ready', data: await res.json() })
        // The backend explains what went wrong in `detail` (e.g. "No eval results yet").
        const body = await res.json().catch(() => ({}))
        setLoad({ state: 'error', message: body.detail ?? `The server answered ${res.status}` })
      })
      .catch(() => setLoad({ state: 'error', message: 'Could not reach the backend' }))
  }, [])

  if (load.state === 'loading') {
    return <main className="p-8 text-sm text-muted-foreground">Loading results…</main>
  }
  if (load.state === 'error') {
    return (
      <main className="p-8">
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm">
          <p className="font-medium text-red-700">Couldn't load the experiment results</p>
          <p className="mt-1 text-muted-foreground">{load.message}</p>
        </div>
      </main>
    )
  }

  const r = load.data
  return (
    <main className="flex flex-col gap-6 p-8">
      {r.placeholder && (
        // Until the real eval run lands, the file holds made-up numbers. Say so loudly.
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm">
          <Badge className="bg-amber-500/20 text-amber-700">Placeholder numbers</Badge>
          <span className="text-muted-foreground">
            These are made-up values to show the layout. Real results replace them after the evaluation run.
          </span>
        </div>
      )}

      <div>
        <SectionHeading eyebrow="03 / The evidence" title="Protection, measured."
                        text="Leakage, report quality, attack resistance and guard overhead, across many runs." />
        <p className="mt-2 text-sm text-muted-foreground">
          {r.n_tasks} tasks × {r.conditions.length} conditions · {r.n_seeds} seed{r.n_seeds === 1 ? '' : 's'} ·{' '}
          {r.split} split · agent {r.models.agent}
        </p>
      </div>

      <HeadlineChart conditions={r.conditions} sweep={r.tau_sweep} />
      <ConditionsTable conditions={r.conditions} />
      <AttacksCard attacks={r.attacks} benign={r.benign} />
      <StatsCards r={r} />
    </main>
  )
}

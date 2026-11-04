// The Results screen (S5): the experiment results from GET /api/eval.
// It only reads precomputed numbers; it never starts a run.
import { useEffect, useState } from 'react'
import type { EvalResults, ExternalPoint, Quality } from '@/evalTypes'
import { Badge } from '@/components/ui/badge'
import { SectionHeading } from '@/components/SectionHeading'
import { Button } from '@/components/ui/button'
import { ConditionsTable } from '@/components/ConditionsTable'
import { HeadlineChart } from '@/components/HeadlineChart'
import { AttacksCard } from '@/components/AttacksCard'
import { StatsCards } from '@/components/StatsCards'

// What the screen can be showing: still loading, an error message, or the results.
type Load = { state: 'loading' } | { state: 'error'; message: string } | { state: 'ready'; data: EvalResults }

// The same experiment is scored twice: with the company docs marked CONFIDENTIAL (labeled), and
// without that marking (unlabeled), so the agent can't lean on it (the guard never reads the label). One file each on the backend.
type Setting = 'labeled' | 'unlabeled'
const SETTINGS: { key: Setting; label: string }[] = [
  { key: 'labeled', label: 'Labeled docs' },
  { key: 'unlabeled', label: 'Unlabeled docs' },
]

// GET /api/eval?setting=... and turn the answer into a Load.
async function fetchResults(setting: Setting): Promise<Load> {
  try {
    const res = await fetch(`/api/eval?setting=${setting}`)
    if (res.ok) return { state: 'ready', data: await res.json() }
    // The backend explains what went wrong in `detail` (e.g. "No eval results yet").
    const body = await res.json().catch(() => ({}))
    return { state: 'error', message: body.detail ?? `The server answered ${res.status}` }
  } catch {
    return { state: 'error', message: 'Could not reach the backend' }
  }
}

export function EvalDashboard() {
  // Both files are fetched once, up front, so switching between them is instant.
  const [loads, setLoads] = useState<Record<Setting, Load>>({
    labeled: { state: 'loading' },
    unlabeled: { state: 'loading' },
  })
  const [picked, setPicked] = useState<Setting>('labeled')

  useEffect(() => {
    for (const { key } of SETTINGS) {
      fetchResults(key).then((l) => setLoads((prev) => ({ ...prev, [key]: l })))
    }
  }, [])

  // The switch only appears when both files exist, so nobody clicks into an empty view.
  // If only the unlabeled file exists, show that rather than an error.
  const both = loads.labeled.state === 'ready' && loads.unlabeled.state === 'ready'
  const setting: Setting = loads.labeled.state === 'error' && loads.unlabeled.state === 'ready' ? 'unlabeled' : picked
  const load = loads[setting]

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
          {r.setting && <> · {r.setting === 'labeled' ? 'docs labeled CONFIDENTIAL' : 'docs without the CONFIDENTIAL marking'}</>}
        </p>
        {r.quality && <QualityNote q={r.quality} />}
      </div>

      {both && <SettingSwitch value={setting} onChange={setPicked} />}

      <HeadlineChart conditions={r.conditions} sweep={r.tau_sweep} />
      {(r.external_reference ?? []).length > 0 && <ExternalReference points={r.external_reference!} />}
      <ConditionsTable conditions={r.conditions} />
      <AttacksCard attacks={r.attacks} benign={r.benign} />
      <StatsCards r={r} />
    </main>
  )
}

// Labeled / Unlabeled switch, same look as the Replay / Live switch on Compare.
function SettingSwitch({ value, onChange }: { value: Setting; onChange: (s: Setting) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-3 text-sm">
      <div role="group" aria-label="Which results to show" className="flex rounded-lg border p-0.5">
        {SETTINGS.map((s) => (
          <Button key={s.key} size="sm" variant={value === s.key ? 'secondary' : 'ghost'}
                  aria-pressed={value === s.key} onClick={() => onChange(s.key)}>
            {s.label}
          </Button>
        ))}
      </div>
      <span className="text-muted-foreground">
        {value === 'labeled'
          ? 'Company docs are marked CONFIDENTIAL.'
          : 'Same docs without the CONFIDENTIAL marking, so the agent can\'t rely on it.'}
      </span>
    </div>
  )
}

// One line on how much of the input made it into the numbers. Amber when something was left out,
// so a reader knows the numbers rest on fewer runs than planned.
function QualityNote({ q }: { q: Quality }) {
  const problems = q.runs_excluded + q.observer_failures + q.judge_failures
  return (
    <p className={`mt-1 text-xs ${problems ? 'text-amber-700' : 'text-muted-foreground'}`}>
      {q.runs_scored} runs scored
      {q.runs_excluded > 0 && ` · ${q.runs_excluded} left out (incomplete observer reply)`}
      {q.judge_failures > 0 && ` · ${q.judge_failures} report scores missing`}
    </p>
  )
}

// Published results for context. Kept apart from our chart on purpose: they measure leakage a
// different way, so putting them on the same axes would invite a comparison that isn't fair.
// Published numbers keep their decimal (25.5%, not 26%): quote them exactly as the paper does.
const exact = (x: number) => `${+(x * 100).toFixed(1)}%`

function ExternalReference({ points }: { points: ExternalPoint[] }) {
  return (
    <section className="rounded-lg border px-4 py-3 text-sm">
      <h3 className="font-medium">For reference: published results</h3>
      <p className="text-xs text-muted-foreground">Measured differently from our leak score, so not directly comparable.</p>
      <ul className="mt-2 flex flex-col gap-1">
        {points.map((p) => (
          <li key={`${p.source}-${p.label}`} className="flex flex-wrap justify-between gap-2">
            <span>{p.label} <span className="text-muted-foreground">· {p.source}</span></span>
            <span className="tabular-nums">
              {exact(p.leakage)} leaked{p.utility !== null && ` · utility ${exact(p.utility)}`}
            </span>
            {p.note && <span className="w-full text-xs text-muted-foreground">{p.note}</span>}
          </li>
        ))}
      </ul>
    </section>
  )
}

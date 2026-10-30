// Three small number tiles: how many queries left the company, how many MOAT changed,
// how many it stopped entirely. Filled in once the lane's run_complete arrives.
import type { LaneView } from '@/lib/runState'

export function RunSummary({ lane }: { lane: LaneView }) {
  const c = lane.complete

  const tiles = [
    { label: 'Sent', value: c?.sent, hint: 'queries that left the company', color: '' },
    { label: 'Rewritten', value: c?.rewritten, hint: 'changed by MOAT before sending',
      color: c?.rewritten ? 'text-amber-400' : '' },
    { label: 'Blocked', value: c?.blocked, hint: 'never sent', color: c?.blocked ? 'text-red-400' : '' },
  ]

  return (
    <div className="grid grid-cols-3 gap-3">
      {tiles.map((t) => (
        <div key={t.label} className="rounded-xl border px-4 py-3" title={t.hint}>
          <div className="text-xs text-muted-foreground">{t.label}</div>
          <div className={`text-2xl font-semibold tabular-nums ${t.color}`}>{t.value ?? '–'}</div>
        </div>
      ))}
    </div>
  )
}

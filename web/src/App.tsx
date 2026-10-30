import { useEffect, useMemo, useRef, useState } from 'react'
import { EVENT_TYPES, type Lane, type RunEvent } from './types'
import { buildLanes, priorsByDim, type HopView, type LaneView } from '@/lib/runState'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { HopCard } from '@/components/HopCard'
import { LeakMeter } from '@/components/LeakMeter'
import { ProductProfile } from '@/components/ProductProfile'
import { RunSummary } from '@/components/RunSummary'


function App() {
  const [status, setStatus] = useState('checking...')
  const [events, setEvents] = useState<RunEvent[]>([])
  const [running, setRunning] = useState(false)
  const [runNames, setRunNames] = useState<string[]>([])
  const [selected, setSelected] = useState('compare_run')
  const sourceRef = useRef<EventSource | null>(null)

  useEffect(() => {
    fetch('/api/health')
      .then((res) => res.json())
      .then((data) => setStatus(data.status))
      .catch(() => setStatus('backend not reachable'))

    // Which recorded runs can be replayed.
    fetch('/api/replay')
      .then((res) => res.json())
      .then((data: { runs: string[] }) => setRunNames(data.runs))
      .catch(() => setRunNames([]))
  }, [])

  useEffect(() => () => sourceRef.current?.close(), [])

  function startReplay() {
    sourceRef.current?.close()
    setEvents([])
    setRunning(true)

    const source = new EventSource(`/api/replay/${encodeURIComponent(selected)}/stream`)
    sourceRef.current = source
    const announced = new Set<Lane>()
    const completed = new Set<Lane>()

    function handle(message: MessageEvent) {
      const event: RunEvent = JSON.parse(message.data)
      setEvents((previous) => [...previous, event])
      if (event.type === 'run_started') event.data.lanes.forEach((l) => announced.add(l))
      if (event.type === 'run_complete' && event.lane) completed.add(event.lane)
      const allDone = announced.size > 0 && [...announced].every((l) => completed.has(l))
      if (allDone || (event.type === 'error' && event.data.where === 'graph')) {
        source.close()
        setRunning(false)
      }
    }

    EVENT_TYPES.forEach((type) => source.addEventListener(type, handle))
    source.onerror = () => {
      source.close()
      setRunning(false)
    }
  }

  // Recomputed only when events change.
  const lanes = useMemo(() => buildLanes(events), [events])
  const priors = useMemo(() => priorsByDim([lanes.baseline, lanes.moat]), [lanes])

  // What the rows need to know: has anything started, does either lane have a profile,
  // and which hop numbers appear in either lane.
  const started = Boolean(lanes.baseline.started || lanes.moat.started)
  const hasProfile = Boolean(lanes.baseline.profile || lanes.moat.profile)
  const hopNumbers = [...new Set([...lanes.baseline.hops, ...lanes.moat.hops].map((h) => h.hop))].sort((a, b) => a - b)

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="flex items-center justify-between border-b px-8 py-4">
        <div>
          <h1 className="text-xl font-semibold">MOAT Guard</h1>
          <p className="text-sm text-muted-foreground">
            What a research agent's searches reveal, with and without the guard
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Badge variant={status === 'ok' ? 'secondary' : 'destructive'}>backend: {status}</Badge>
          <label className="sr-only" htmlFor="run-picker">Recorded run</label>
          <select
            id="run-picker"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            disabled={running}
            className="h-8 rounded-lg border bg-background px-2 text-sm"
          >
            {(runNames.length ? runNames : [selected]).map((name) => (
              <option key={name} value={name}>{name.replace(/_/g, ' ')}</option>
            ))}
          </select>
          <Button onClick={startReplay} disabled={running}>
            {running ? 'Replaying…' : 'Replay'}
          </Button>
        </div>
      </header>

      <main className="flex flex-col gap-6 p-8">
        {/* Each Row puts the baseline item on the left and the MOAT item on the right,
            so matching cards always sit at the same height. */}
        <Row
          left={<LaneHeading title="No guard" lane={lanes.baseline} waiting={running} />}
          right={<LaneHeading title="With MOAT" lane={lanes.moat} waiting={running} />}
        />

        {!started && (
          <p className="text-sm text-muted-foreground">Pick a recorded run and press “Replay” to start.</p>
        )}

        {started && (
          <Row left={<RunSummary lane={lanes.baseline} />} right={<RunSummary lane={lanes.moat} />} />
        )}

        {started && (
          <Row
            left={<LeakMeter lane={lanes.baseline} priors={priors} />}
            right={<LeakMeter lane={lanes.moat} priors={priors} />}
          />
        )}

        {hasProfile && (
          <Row
            left={<ProfileSlot lane={lanes.baseline} />}
            right={<ProfileSlot lane={lanes.moat} />}
          />
        )}

        {hopNumbers.map((n) => (
          <Row
            key={n}
            left={<HopSlot hop={lanes.baseline.hops.find((h) => h.hop === n)} />}
            right={<HopSlot hop={lanes.moat.hops.find((h) => h.hop === n)} />}
          />
        ))}

        {(lanes.baseline.report || lanes.moat.report) && (
          <Row left={<ReportSlot lane={lanes.baseline} />} right={<ReportSlot lane={lanes.moat} />} />
        )}
      </main>
    </div>
  )
}

// Two equal columns on wide screens; stacked on narrow ones.
function Row({ left, right }: { left: React.ReactNode; right: React.ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <div className="min-w-0">{left}</div>
      <div className="min-w-0">{right}</div>
    </div>
  )
}

// What each graph node means, in words a judge would understand.
const NODE_LABEL: Record<string, string> = {
  ingest: 'Reading internal docs',
  draft_query: 'Drafting a search',
  moat_guard: 'MOAT checking the query',
  passthrough: 'Sending without a check',
  web_search: 'Searching patents',
  skip_hop: 'Skipping a blocked search',
  reflect: 'Deciding what to search next',
  generate_report: 'Writing the report',
}

function LaneHeading({ title, lane, waiting }: { title: string; lane: LaneView; waiting: boolean }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">{title}</h2>
        {lane.started && <Badge variant="outline">condition: {lane.started.condition}</Badge>}
      </div>
      <LaneStatus lane={lane} waiting={waiting} />
    </div>
  )
}

// One line under the lane title: waiting, what the agent is doing right now, or done.
function LaneStatus({ lane, waiting }: { lane: LaneView; waiting: boolean }) {
  if (lane.complete) {
    const failed = lane.complete.status === 'failed'
    return (
      <p className={`text-sm ${failed ? 'text-red-400' : 'text-muted-foreground'}`}>
        {failed ? 'Run failed' : 'Done'}
      </p>
    )
  }
  if (lane.started) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground" aria-live="polite">
        <span className="size-2 animate-pulse rounded-full bg-emerald-400 motion-reduce:animate-none" />
        {NODE_LABEL[lane.currentNode ?? ''] ?? 'Running'}…
      </p>
    )
  }
  if (waiting) return <p className="text-sm text-muted-foreground">Waiting to start…</p>
  return null
}

// Placeholders keep the rows lined up when only one lane has something to show.
function Placeholder({ text }: { text: string }) {
  return (
    <div className="flex h-full min-h-16 items-center justify-center rounded-xl border border-dashed text-sm text-muted-foreground">
      {text}
    </div>
  )
}

function ProfileSlot({ lane }: { lane: LaneView }) {
  return lane.profile ? <ProductProfile profile={lane.profile} /> : <Placeholder text="No product profile for this lane" />
}

function HopSlot({ hop }: { hop?: HopView }) {
  return hop ? <HopCard hop={hop} /> : <Placeholder text="No search in this hop" />
}

function ReportSlot({ lane }: { lane: LaneView }) {
  if (!lane.report) return <Placeholder text="No report yet" />
  return (
    <Card size="sm">
      <CardHeader><CardTitle>Final report</CardTitle></CardHeader>
      <CardContent>{lane.report}</CardContent>
    </Card>
  )
}

export default App

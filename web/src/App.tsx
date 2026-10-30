import { useEffect, useMemo, useRef, useState } from 'react'
import { EVENT_TYPES, type Lane, type RunEvent } from './types'
import { buildLanes, priorsByDim, type HopView, type LaneView } from '@/lib/runState'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { DecisionRow, Query } from '@/components/DecisionRow'
import { LeakMeter } from '@/components/LeakMeter'
import { ProductProfile } from '@/components/ProductProfile'

const REPLAY_URL = '/api/replay/compare_run/stream'

function App() {
  const [status, setStatus] = useState('checking...')
  const [events, setEvents] = useState<RunEvent[]>([])
  const [running, setRunning] = useState(false)
  const sourceRef = useRef<EventSource | null>(null)

  useEffect(() => {
    fetch('/api/health')
      .then((res) => res.json())
      .then((data) => setStatus(data.status))
      .catch(() => setStatus('backend not reachable'))
  }, [])

  useEffect(() => () => sourceRef.current?.close(), [])

  function startReplay() {
    sourceRef.current?.close()
    setEvents([])
    setRunning(true)

    const source = new EventSource(REPLAY_URL)
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
          <Button onClick={startReplay} disabled={running}>
            {running ? 'Replaying…' : 'Replay compare run'}
          </Button>
        </div>
      </header>

      <main className="flex flex-col gap-6 p-8">
        {/* Each Row puts the baseline item on the left and the MOAT item on the right,
            so matching cards always sit at the same height. */}
        <Row
          left={<LaneHeading title="No guard" lane={lanes.baseline} />}
          right={<LaneHeading title="With MOAT" lane={lanes.moat} />}
        />

        {!started && (
          <p className="text-sm text-muted-foreground">Press “Replay compare run” to start.</p>
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

function LaneHeading({ title, lane }: { title: string; lane: LaneView }) {
  return (
    <div className="flex items-center justify-between">
      <h2 className="text-lg font-semibold">{title}</h2>
      {lane.started && <Badge variant="outline">condition: {lane.started.condition}</Badge>}
    </div>
  )
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

function HopCard({ hop }: { hop: HopView }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Hop {hop.hop}</CardTitle>
        {hop.subgoal && <CardDescription>{hop.subgoal}</CardDescription>}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {hop.decisions.map((d, i) => <DecisionRow key={i} decision={d} />)}
        {hop.decisions.length === 0 && hop.drafts.map((draft, i) => (
          <Query key={i} label="Drafted" text={draft} />
        ))}
        {hop.skipped && <Badge variant="destructive">Hop skipped: {hop.skipped.reason}</Badge>}
      </CardContent>
    </Card>
  )
}

export default App

import { useEffect, useMemo, useRef, useState } from 'react'
import { EVENT_TYPES, type Lane, type RunEvent } from './types'
import { buildLanes, priorsByDim, type HopView, type LaneView, type PriorInfo } from '@/lib/runState'
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

      <main className="grid grid-cols-1 gap-6 p-8 lg:grid-cols-2">
        <LanePanel title="No guard" lane={lanes.baseline} priors={priors} />
        <LanePanel title="With MOAT" lane={lanes.moat} priors={priors} />
      </main>
    </div>
  )
}

function LanePanel({ title, lane, priors }: { title: string; lane: LaneView; priors: Record<string, PriorInfo> }) {
  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">{title}</h2>
        {lane.started && <Badge variant="outline">condition: {lane.started.condition}</Badge>}
      </div>

      {lane.started && <LeakMeter lane={lane} priors={priors} />}
      {lane.profile && <ProductProfile profile={lane.profile} />}

      {lane.hops.length === 0 && (
        <p className="text-sm text-muted-foreground">Press “Replay compare run” to start.</p>
      )}

      {lane.hops.map((hop) => <HopCard key={hop.hop} hop={hop} />)}

      {lane.report && (
        <Card size="sm">
          <CardHeader><CardTitle>Final report</CardTitle></CardHeader>
          <CardContent>{lane.report}</CardContent>
        </Card>
      )}

      {lane.complete && (
        <p className="text-sm text-muted-foreground">
          Sent {lane.complete.sent} · rewritten {lane.complete.rewritten} · blocked {lane.complete.blocked}
        </p>
      )}
    </section>
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

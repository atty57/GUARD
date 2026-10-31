import { useEffect, useMemo, useRef, useState } from 'react'
import { EVENT_TYPES, type Lane, type RunEvent } from './types'
import { buildLanes, priorsByDim, type HopView, type LaneView } from '@/lib/runState'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { HopCard } from '@/components/HopCard'
import { LeakMeter, type SecretLimit } from '@/components/LeakMeter'
import { ProductProfile } from '@/components/ProductProfile'
import { RunSummary } from '@/components/RunSummary'


// One recorded run, as listed by GET /api/replay.
type RunInfo = { name: string; lanes: Lane[]; synthetic: boolean }

// Replay = play back a recorded run. Live = actually run the agent now (GET /api/live/stream).
type Mode = 'replay' | 'live'

function App() {
  const [status, setStatus] = useState('checking...')
  const [events, setEvents] = useState<RunEvent[]>([])
  const [running, setRunning] = useState(false)
  const [runs, setRuns] = useState<RunInfo[]>([])
  const [playing, setPlaying] = useState<RunInfo | null>(null) // the run currently on screen
  const [selected, setSelected] = useState('compare_run')
  const [mode, setMode] = useState<Mode>('replay')
  const sourceRef = useRef<EventSource | null>(null)

  useEffect(() => {
    fetch('/api/health')
      .then((res) => res.json())
      .then((data) => setStatus(data.status))
      .catch(() => setStatus('backend not reachable'))

    // Which recorded runs can be replayed.
    fetch('/api/replay')
      .then((res) => res.json())
      .then((data: { runs: RunInfo[] }) => setRuns(data.runs))
      .catch(() => setRuns([]))
  }, [])

  useEffect(() => () => sourceRef.current?.close(), [])

  function startReplay() {
    setPlaying(runs.find((r) => r.name === selected) ?? null)
    startStream(`/api/replay/${encodeURIComponent(selected)}/stream`)
  }

  function startLive() {
    setPlaying(null) // a live run is not a recording, so no "synthetic" banner
    startStream('/api/live/stream?task=demo')
  }

  // Live and replay send the exact same moat.v2 events, so one handler serves both;
  // only the URL differs.
  function startStream(url: string) {
    sourceRef.current?.close()
    setEvents([])
    setRunning(true)

    const source = new EventSource(url)
    sourceRef.current = source
    const announced = new Set<Lane>()
    const completed = new Set<Lane>()

    function handle(message: MessageEvent) {
      // A connection problem also arrives on the 'error' listener, with no data. Skip it here;
      // onerror below decides what to do with it.
      if (!message.data) return
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
    // Name clash: the contract has an event called 'error', and EventSource also uses 'error'
    // for its own "connection problem" signal, so both reach onerror. A server 'error' event
    // carries data (e.g. a search timeout the run recovers from) and must NOT end the stream.
    // Only a real connection problem (no data) closes it.
    source.onerror = (e) => {
      if (e instanceof MessageEvent && e.data) return
      source.close()
      setRunning(false)
    }
  }

  // Recomputed only when events change.
  const lanes = useMemo(() => buildLanes(events), [events])
  const priors = useMemo(() => priorsByDim([lanes.baseline, lanes.moat]), [lanes])

  // Policy limits depend on the run's strictness (tau), so fetch them once we know it.
  const tau = lanes.moat.started?.tau ?? lanes.baseline.started?.tau
  const [limits, setLimits] = useState<Record<string, SecretLimit>>({})
  useEffect(() => {
    const url = tau === undefined ? '/api/policy' : `/api/policy?tau=${tau}`
    fetch(url)
      .then((res) => res.json())
      .then((data: { secrets: Record<string, SecretLimit> }) => setLimits(data.secrets))
      .catch(() => setLimits({})) // the meter falls back to a fixed cutoff
  }, [tau])

  // What the rows need to know: has anything started, does either lane have a profile,
  // and which hop numbers appear in either lane.
  const started = Boolean(lanes.baseline.started || lanes.moat.started)

  // Which lanes this run has. run_started lists them ("lanes": ["baseline", "moat"] or just
  // ["moat"]). If nothing is known yet, show both so the empty layout still makes sense.
  const announced = lanes.baseline.started?.lanes ?? lanes.moat.started?.lanes
    // Before the stream says, use the lanes the backend listed for the selected run.
    // (Live runs always have both lanes, so they fall through to "show both".)
    ?? (mode === 'replay' ? runs.find((r) => r.name === selected)?.lanes : undefined)
  const shown = { baseline: announced ? announced.includes('baseline') : true,
                  moat: announced ? announced.includes('moat') : true }
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
          {/* Live / Replay toggle. Locked while a run is streaming. */}
          <div role="group" aria-label="Run mode" className="flex rounded-lg border p-0.5">
            {(['replay', 'live'] as const).map((m) => (
              <Button
                key={m}
                size="sm"
                variant={mode === m ? 'secondary' : 'ghost'}
                aria-pressed={mode === m}
                disabled={running}
                onClick={() => setMode(m)}
              >
                {m === 'replay' ? 'Replay' : 'Live'}
              </Button>
            ))}
          </div>
          {mode === 'replay' ? (
            <>
              <label className="sr-only" htmlFor="run-picker">Recorded run</label>
              <select
                id="run-picker"
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
                disabled={running}
                className="h-8 rounded-lg border bg-background px-2 text-sm"
              >
                {(runs.length ? runs.map((r) => r.name) : [selected]).map((name) => (
                  <option key={name} value={name}>{name.replace(/_/g, ' ')}</option>
                ))}
              </select>
              <Button onClick={startReplay} disabled={running}>
                {running ? 'Replaying…' : 'Replay'}
              </Button>
            </>
          ) : (
            <Button onClick={startLive} disabled={running}>
              {running ? 'Running…' : 'Run live'}
            </Button>
          )}
        </div>
      </header>

      <main className="flex flex-col gap-6 p-8">
        {playing?.synthetic && (
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-sky-500/40 bg-sky-500/10 px-4 py-2 text-sm">
            <Badge className="bg-sky-500/20 text-sky-300">Synthetic fixture replay</Badge>
            <span className="text-muted-foreground">
              Hand-written test data for “{playing.name.replace(/_/g, ' ')}”, not a recording of a real agent run.
            </span>
          </div>
        )}

        {/* Each Row puts the baseline item on the left and the MOAT item on the right,
            so matching cards always sit at the same height. */}
        <Row
          shown={shown}
          left={<LaneHeading title="No guard" lane={lanes.baseline} waiting={running} />}
          right={<LaneHeading title="With MOAT" lane={lanes.moat} waiting={running} />}
        />

        {!started && (
          <p className="text-sm text-muted-foreground">
            {mode === 'replay'
              ? 'Pick a recorded run and press “Replay” to start.'
              : 'Press “Run live” to run the agent now on the demo task, with and without MOAT.'}
          </p>
        )}

        {started && (
          <Row shown={shown} left={<RunSummary lane={lanes.baseline} />} right={<RunSummary lane={lanes.moat} />} />
        )}

        {started && (
          <Row
          shown={shown}
            left={<LeakMeter lane={lanes.baseline} priors={priors} limits={limits} />}
            right={<LeakMeter lane={lanes.moat} priors={priors} limits={limits} />}
          />
        )}

        {hasProfile && (
          <Row
          shown={shown}
            left={<ProfileSlot lane={lanes.baseline} />}
            right={<ProfileSlot lane={lanes.moat} />}
          />
        )}

        {hopNumbers.map((n) => (
          <Row
          shown={shown}
            key={n}
            left={<HopSlot hop={lanes.baseline.hops.find((h) => h.hop === n)} />}
            right={<HopSlot hop={lanes.moat.hops.find((h) => h.hop === n)} />}
          />
        ))}

        {(lanes.baseline.report || lanes.moat.report) && (
          <Row shown={shown} left={<ReportSlot lane={lanes.baseline} />} right={<ReportSlot lane={lanes.moat} />} />
        )}
      </main>
    </div>
  )
}

// Puts the baseline item on the left and the MOAT item on the right, so matching cards line up.
// A run with only one lane gets a single column instead of an empty half.
function Row({ left, right, shown }: {
  left: React.ReactNode
  right: React.ReactNode
  shown: { baseline: boolean; moat: boolean }
}) {
  const both = shown.baseline && shown.moat
  return (
    <div className={both ? 'grid grid-cols-1 gap-6 lg:grid-cols-2' : 'mx-auto w-full max-w-4xl'}>
      {shown.baseline && <div className="min-w-0">{left}</div>}
      {shown.moat && <div className="min-w-0">{right}</div>}
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
  if (lane.started && !waiting) {
    // The stream ended (or was closed) before this lane sent run_complete.
    return <p className="text-sm text-amber-400">Stopped before finishing</p>
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

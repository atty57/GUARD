import { useEffect, useRef, useState } from 'react'
import { EVENT_TYPES, type Lane, type RunEvent } from './types'

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

    // The run is over once every lane announced in run_started has a run_complete.
    const announced = new Set<Lane>()
    const completed = new Set<Lane>()

    function handle(message: MessageEvent) {
      const event: RunEvent = JSON.parse(message.data)
      setEvents((previous) => [...previous, event])

      if (event.type === 'run_started') event.data.lanes.forEach((l) => announced.add(l))
      if (event.type === 'run_complete' && event.lane) completed.add(event.lane)

      const allDone = announced.size > 0 && [...announced].every((l) => completed.has(l))
      if (allDone || (event.type === 'error' && event.data.where === 'graph')) {
        source.close() // otherwise the browser reconnects and replays forever
        setRunning(false)
      }
    }

    // Named SSE events (`event: guard_decision`) only reach listeners for that name,
    // not onmessage, so we register one listener per event type.
    EVENT_TYPES.forEach((type) => source.addEventListener(type, handle))

    source.onerror = () => {
      source.close()
      setRunning(false)
    }
  }

  const lane = (name: Lane) => events.filter((e) => e.lane === name)

  return (
    <main style={{ padding: 32, fontFamily: 'sans-serif', textAlign: 'left' }}>
      <h1>GUARD</h1>
      <p>Backend status: {status}</p>
      <button onClick={startReplay} disabled={running}>
        {running ? 'Replaying...' : 'Replay compare run'}
      </button>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24, marginTop: 24 }}>
        <LaneColumn title="No guard" events={lane('baseline')} />
        <LaneColumn title="With MOAT" events={lane('moat')} />
      </div>
    </main>
  )
}

function LaneColumn({ title, events }: { title: string; events: RunEvent[] }) {
  return (
    <section>
      <h2>{title}</h2>
      <ol>
        {events.map((event) => (
          <li key={event.seq ?? event.type} style={{ margin: '8px 0' }}>
            <EventLine event={event} />
          </li>
        ))}
      </ol>
    </section>
  )
}

// Rough one-line text for each event. Step S3 replaces this with real cards.
function EventLine({ event }: { event: RunEvent }) {
  switch (event.type) {
    case 'run_started':
      return <span>Run started (condition: {event.data.condition}, tau {event.data.tau})</span>
    case 'node_entered':
      return <small style={{ opacity: 0.6 }}>→ {event.data.node}</small>
    case 'hop_started':
      return <span>Hop {event.data.hop}: {event.data.subgoal}</span>
    case 'query_drafted':
      return <span>Drafted: <code>{event.data.draft}</code></span>
    case 'guard_decision': {
      const d = event.data
      return (
        <span>
          Guard <strong>{d.decision}</strong>
          {d.final ? <> → sent <code>{d.final}</code></> : ' → nothing sent'}
          <br />
          <small>{d.reasons.join('; ')}</small>
        </span>
      )
    }
    case 'secret_interval_update': {
      const d = event.data
      return (
        <span>
          Leak meter {d.committed ? '(sent)' : '(preview)'}: {d.dim} {d.after[0]}–{d.after[1]} {d.unit} · {d.status}
        </span>
      )
    }
    case 'profile_update':
      return <span>Profile {event.data.committed ? '(sent)' : '(preview)'}: {Object.values(event.data.revealed).join(', ')} · k={event.data.k}</span>
    case 'search_results':
      return <span>Search returned {event.data.hits.length} hit(s)</span>
    case 'observer_estimate':
      return <span>Attacker guesses {event.data.attr}: {event.data.p10}–{event.data.p90}</span>
    case 'report_chunk':
      return <span>Report: {event.data.text}</span>
    case 'run_complete':
      return <span>Done: sent {event.data.sent}, rewritten {event.data.rewritten}, blocked {event.data.blocked}</span>
    case 'hop_skipped':
      return <span>Hop skipped: {event.data.reason}</span>
    case 'injection_detected':
      return <span>Injection detected in {event.data.doc_id}: {event.data.action}</span>
    case 'error':
      return <span>Error in {event.data.where}: {event.data.message}</span>
    default:
      return <span>{event.type}</span>
  }
}

export default App

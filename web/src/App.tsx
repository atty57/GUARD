import { useEffect, useRef, useState } from 'react'
import type { RunEvent } from './types'

function App() {
  const [status, setStatus] = useState('checking...')
  const [events, setEvents] = useState<RunEvent[]>([])
  const [running, setRunning] = useState(false)
  const sourceRef = useRef<EventSource | null>(null)

  // Health check, once on page load.
  useEffect(() => {
    fetch('/api/health')
      .then((res) => res.json())
      .then((data) => setStatus(data.status))
      .catch(() => setStatus('backend not reachable'))
  }, [])

  // Close the stream if the page goes away mid-run.
  useEffect(() => () => sourceRef.current?.close(), [])

  function startRun() {
    sourceRef.current?.close()
    setEvents([])
    setRunning(true)

    const source = new EventSource('/api/stream/fake')
    sourceRef.current = source

    source.onmessage = (message) => {
      const event: RunEvent = JSON.parse(message.data)
      setEvents((previous) => [...previous, event])

      // The browser would reconnect and replay the run forever if we didn't close it.
      if (event.type === 'run_done') {
        source.close()
        setRunning(false)
      }
    }

    source.onerror = () => {
      source.close()
      setRunning(false)
    }
  }

  return (
    <main style={{ padding: 32, fontFamily: 'sans-serif', maxWidth: 720 }}>
      <h1>GUARD</h1>
      <p>Backend status: {status}</p>

      <button onClick={startRun} disabled={running}>
        {running ? 'Running...' : 'Start fake run'}
      </button>

      <ol>
        {events.map((event, index) => (
          <li key={index} style={{ margin: '12px 0' }}>
            <EventLine event={event} />
          </li>
        ))}
      </ol>
    </main>
  )
}

// Turns one event into a short readable line.
function EventLine({ event }: { event: RunEvent }) {
  switch (event.type) {
    case 'run_started':
      return <span>Run started: "{event.question}" (strictness {event.strictness})</span>
    case 'agent_draft':
      return <span>Step {event.step}: agent drafted <code>{event.query}</code></span>
    case 'guard_check':
      return (
        <span>
          Step {event.step}: guard <strong>{event.action}</strong>
          {event.sent ? <> → sent <code>{event.sent}</code></> : ' → nothing sent'}
          <br />
          <small>{event.reason}</small>
        </span>
      )
    case 'search':
      return <span>Step {event.step}: search returned {event.num_results} results</span>
    case 'report':
      return <span>Report: {event.text}</span>
    case 'run_done':
      return <span>Run finished</span>
  }
}

export default App

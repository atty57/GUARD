// Event types for the moat.v2 contract (docs/INTERFACES.md §3).
// Every event shares an "envelope" (who/when/where) and carries its details in `data`.

export type Lane = 'baseline' | 'moat'
export type Condition = 'none' | 'prompt' | 'moat' | 'moat_prompt'
export type Decision = 'pass' | 'rewrite' | 'block'
export type Status = 'safe' | 'warning' | 'prevented'
export type Range = [number, number]

// Fields every event has.
type Envelope<T extends string, D> = {
  schema: 'moat.v2'
  run_id: string | null
  group_id: string
  lane: Lane | null
  seq: number | null
  ts: string
  hop: number | null
  type: T
  data: D
}

export type HelloEvent = Envelope<'hello', { schema: string; group_id: string }>

export type RunStartedEvent = Envelope<'run_started', {
  task_id: string
  condition: Condition
  tau: number
  model: string
  policy_hash: string
  replay: boolean
  lanes: Lane[]
}>

export type NodeEnteredEvent = Envelope<'node_entered', { node: string }>

export type HopStartedEvent = Envelope<'hop_started', { hop: number; subgoal: string }>

export type QueryDraftedEvent = Envelope<'query_drafted', {
  draft: string
  latency_ms: Record<string, number>
}>

export type DiffOp = { op: 'equal' | 'insert' | 'delete'; text: string }

export type GuardDecisionEvent = Envelope<'guard_decision', {
  decision: Decision
  tier: number
  original: string
  final: string | null // null when blocked: nothing was sent
  diff: DiffOp[]
  leak_types: string[]
  secrets_touched: string[]
  reasons: string[]
  latency_ms: Record<string, number>
  block_kind: 'redraftable' | 'unsatisfiable' | null
  rechecked: boolean | null
}>

// The leak meter: what an outsider could narrow a secret down to.
export type SecretIntervalUpdateEvent = Envelope<'secret_interval_update', {
  secret_id: string
  dim: string
  unit: string
  prior: Range
  before: Range
  after: Range
  width_ratio: number
  bits: number
  status: Status
  committed: boolean // true = this actually went out
  basis: 'original' | 'final'
  decision_seq: number
}>

// The attacker panel: product details pieced together so far.
export type ProfileUpdateEvent = Envelope<'profile_update', {
  revealed: Record<string, string>
  k: number
  k_min: number
  generalized_from?: string | null
  committed: boolean
  status: Status
}>

export type SearchResultsEvent = Envelope<'search_results', {
  query: string
  corpus: string
  corpus_sha: string
  hits: { id: string; title: string; score: number }[]
}>

export type HopSkippedEvent = Envelope<'hop_skipped', { reason: string; detail: string }>

export type InjectionDetectedEvent = Envelope<'injection_detected', {
  doc_id: string
  pattern: string
  action: 'ignored' | 'blocked'
}>

export type ObserverEstimateEvent = Envelope<'observer_estimate', {
  condition: Condition
  attr: string
  point: number
  p10: number
  p90: number
}>

export type ReportChunkEvent = Envelope<'report_chunk', { text: string }>

export type RunCompleteEvent = Envelope<'run_complete', {
  status: 'completed' | 'completed_with_skips' | 'failed'
  hops: number
  sent: number
  rewritten: number
  blocked: number
  trace_url: string | null
}>

export type ErrorEvent = Envelope<'error', {
  where: 'guard' | 'search' | 'model' | 'graph'
  message: string
  fail_closed: true
  dispatched: boolean
}>

export type ReplayMetaEvent = Envelope<'replay_meta', {
  recorded_at: string
  source_run_id: string
  speed: number
}>

export type RunEvent =
  | HelloEvent | RunStartedEvent | NodeEnteredEvent | HopStartedEvent | QueryDraftedEvent
  | GuardDecisionEvent | SecretIntervalUpdateEvent | ProfileUpdateEvent | SearchResultsEvent
  | HopSkippedEvent | InjectionDetectedEvent | ObserverEstimateEvent | ReportChunkEvent
  | RunCompleteEvent | ErrorEvent | ReplayMetaEvent

// Every event name the stream can send. The browser needs one listener per name.
export const EVENT_TYPES: RunEvent['type'][] = [
  'hello', 'run_started', 'node_entered', 'hop_started', 'query_drafted',
  'guard_decision', 'secret_interval_update', 'profile_update', 'search_results',
  'hop_skipped', 'injection_detected', 'observer_estimate', 'report_chunk',
  'run_complete', 'error', 'replay_meta',
]

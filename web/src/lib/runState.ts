// Turns the raw event stream into what the screen draws:
// for each lane, a list of hops, and for each hop what happened in it.
import type {
  GuardDecisionEvent, Lane, ObserverEstimateEvent, ProfileUpdateEvent, RunCompleteEvent,
  RunEvent, RunStartedEvent, SearchResultsEvent, SecretIntervalUpdateEvent,
} from '../types'

export type HopView = {
  hop: number
  subgoal: string
  drafts: string[]                       // every draft in this hop (redrafts add more)
  decisions: GuardDecisionEvent['data'][] // one per draft
  intervals: SecretIntervalUpdateEvent['data'][]
  search?: SearchResultsEvent['data']
  skipped?: { reason: string; detail: string }
  errors: { where: string; message: string; dispatched: boolean }[]
  injections: { doc_id: string; action: string }[]
}

export type LaneView = {
  started?: RunStartedEvent['data']
  currentNode?: string
  hops: HopView[]
  profile?: ProfileUpdateEvent['data']           // latest committed profile only
  observer: ObserverEstimateEvent['data'][]
  report: string                                  // report_chunk texts joined
  complete?: RunCompleteEvent['data']
}

const emptyLane = (): LaneView => ({ hops: [], observer: [], report: '' })

function hopFor(lane: LaneView, hop: number): HopView {
  let h = lane.hops.find((x) => x.hop === hop)
  if (!h) {
    h = { hop, subgoal: '', drafts: [], decisions: [], intervals: [], errors: [], injections: [] }
    lane.hops.push(h)
    lane.hops.sort((a, b) => a.hop - b.hop)
  }
  return h
}

export function buildLanes(events: RunEvent[]): Record<Lane, LaneView> {
  const lanes: Record<Lane, LaneView> = { baseline: emptyLane(), moat: emptyLane() }

  for (const e of events) {
    if (!e.lane) continue // hello is group-scoped
    const lane = lanes[e.lane]

    switch (e.type) {
      case 'run_started': lane.started = e.data; break
      case 'node_entered': lane.currentNode = e.data.node; break
      case 'hop_started': hopFor(lane, e.data.hop).subgoal = e.data.subgoal; break
      case 'query_drafted': hopFor(lane, e.hop!).drafts.push(e.data.draft); break
      case 'guard_decision': hopFor(lane, e.hop!).decisions.push(e.data); break
      case 'secret_interval_update': hopFor(lane, e.hop!).intervals.push(e.data); break
      case 'search_results': hopFor(lane, e.hop!).search = e.data; break
      case 'hop_skipped': hopFor(lane, e.hop!).skipped = e.data; break
      case 'error': hopFor(lane, e.hop ?? 0).errors.push(e.data); break
      case 'injection_detected': hopFor(lane, e.hop!).injections.push(e.data); break
      // The UI shows committed exposure only; a preview must never look revealed.
      case 'profile_update': if (e.data.committed) lane.profile = e.data; break
      case 'observer_estimate': lane.observer.push(e.data); break
      case 'report_chunk': lane.report += (lane.report ? ' ' : '') + e.data.text; break
      case 'run_complete': lane.complete = e.data; break
    }
  }
  return lanes
}

// The full range an informed outsider would assume for each secret, before any queries.
// Only the MOAT lane carries interval events, so we collect priors from all lanes and
// share them, letting the baseline lane draw its bar on the same scale.
export type PriorInfo = { prior: [number, number]; unit: string }

export function priorsByDim(lanes: LaneView[]): Record<string, PriorInfo> {
  const out: Record<string, PriorInfo> = {}
  for (const lane of lanes)
    for (const hop of lane.hops)
      for (const i of hop.intervals) out[i.dim] ??= { prior: i.prior, unit: i.unit }
  return out
}

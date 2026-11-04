// Types for the experiment results the dashboard (S5) shows: GET /api/eval.
// Mirrors eval/schema.py (Hyeseong, schema "moat.eval.v1") field for field, the same way types.ts
// mirrors the event contract. If the Python schema changes, change this file to match.
import type { Condition } from './types'

// A mean over paired tasks with its bootstrap 95% confidence interval: ci95 = [low, high].
export type Estimate = { mean: number; ci95: [number, number] }

// How much the queries gave away. Shares (width_ratio, hit_within_2pct, intent_similarity) stay in
// [0, 1] and bits never goes below 0. The two gains are differences, so they can be negative
// (the queries pointed the attacker the wrong way).
export type Leakage = {
  numeric_gain: Estimate        // how much closer the attacker got to the secret numbers (fraction of the range)
  width_ratio: Estimate         // final guess width / starting width (smaller = more exposed)
  bits: Estimate                // ledger estimate of information leaked, summed over secrets
  hit_within_2pct: Estimate     // share of number secrets the attacker guessed within ±2%
  categorical_gain: Estimate    // name secrets guessed exactly, minus the no-query baseline
  intent_similarity: Estimate   // how well the attacker guessed what the agent was researching
}

// How useful the agent's final report still was.
export type Utility = {
  // null until the input exists (#59): gold patents not labeled yet / no judge has scored.
  citation_recall: Estimate | null   // planted gold patents the report cited
  rubric: Estimate | null            // blind judge's quality score, scaled to [0, 1]
}

// What the guard did to the run.
export type Flow = {
  queries_per_run: number
  block_rate: number            // share of drafts blocked
  rewrite_rate: number          // share of drafts rewritten
  skip_rate: number             // share of hops skipped
}

export type ConditionResult = {
  condition: Condition
  tau: number
  n_runs: number
  leakage: Leakage
  utility: Utility
  flow: Flow
}

export type AttackResult = { leak_type: string; n: number; recall: number }

export type Comparison = {
  metric: string                // e.g. "leakage.numeric_gain"
  a: Condition
  b: Condition
  test: 'wilcoxon' | 'mcnemar'
  effect: number                // a − b, paired by task
  p_value: number
  p_holm: number                // Holm-corrected; < 0.05 = significant
}

export type Latency = { p50: number; p95: number }

// A published result shown for reference only (e.g. MosaicLeaks). It uses a different leakage
// measure, so it's never compared with our numbers.
export type ExternalPoint = { source: string; label: string; leakage: number; utility: number | null; note: string }

// How much of the input made it into the numbers. Anything but zeros needs a look.
export type Quality = { runs_scored: number; runs_excluded: number; observer_failures: number; judge_failures: number }

export type EvalResults = {
  schema: 'moat.eval.v1'
  placeholder: boolean          // true = made-up numbers until the real eval run lands
  generated_at: string
  split: 'dev' | 'test'
  // Added in #59; optional so older files still load.
  setting?: 'labeled' | 'unlabeled' | null   // docs marked CONFIDENTIAL, or INTERNAL instead (#11, #68)
  external_reference?: ExternalPoint[]
  quality?: Quality | null
  n_tasks: number
  n_seeds: number
  corpus_sha: string
  policy_hash: string
  models: { agent: string; observer: string; judge: string }
  conditions: ConditionResult[]   // the 4 conditions, once each, at the policy's tau
  tau_sweep: ConditionResult[]    // MOAT at several tau values, ascending; empty until the sweep has run
  attacks: AttackResult[]
  benign: { n: number; false_positive_rate: number } | null   // null until the attack suite has run
  comparisons: Comparison[]
  latency_ms: Partial<Record<'tier0' | 'tier1' | 'tier2', Latency>> & { total: Latency }
  tier2_escalation_rate: number
  cost_usd: number
}

// Display names and colors for the 4 conditions, used everywhere on the dashboard.
// Names match the compare screen's lane titles. Colors: grey/amber = no guard, green = MOAT.
export const CONDITIONS: { id: Condition; label: string; color: string }[] = [
  { id: 'none', label: 'No guard', color: '#a1a1aa' },
  { id: 'prompt', label: 'Prompt only', color: '#f59e0b' },
  { id: 'moat', label: 'MOAT', color: '#10b981' },
  { id: 'moat_prompt', label: 'MOAT + prompt', color: '#06b6d4' },
]

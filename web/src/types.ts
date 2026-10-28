// The shape of every event the backend streams to the screen.
// Mirrors draft event format (to be finalized in docs/INTERFACES.md).

export type GuardAction = 'allow' | 'rewrite' | 'block'

export type RunEvent =
  | { type: 'run_started'; config: string; question: string; strictness: number }
  | { type: 'agent_draft'; config: string; step: number; query: string }
  | {
      type: 'guard_check'
      config: string
      step: number
      action: GuardAction
      original: string
      sent: string | null
      matched: string[]
      leak_type: string
      reason: string
    }
  | { type: 'search'; config: string; step: number; sent_query: string; num_results: number }
  | { type: 'report'; config: string; text: string }
  | { type: 'run_done'; config: string }

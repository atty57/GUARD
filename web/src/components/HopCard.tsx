// One search round ("hop"): every draft attempt, what the guard did, and what happened after.
import type { HopView } from '@/lib/runState'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { DecisionRow, Query } from '@/components/DecisionRow'

// Plain-language versions of the contract's skip reasons (INTERFACES.md vocabulary).
const SKIP_REASON: Record<string, string> = {
  redrafts_exhausted: 'Every rewrite attempt still leaked, so the agent gave up on this search',
  k_anonymity_unreachable: 'Any version of this search would identify the product, so it was skipped',
  guard_exception: 'The guard hit an error, so the search was skipped to stay safe',
}

export function HopCard({ hop }: { hop: HopView }) {
  const attempts = hop.decisions.length

  return (
    <Card>
      <CardHeader>
        <CardTitle>Hop {hop.hop}</CardTitle>
        {hop.subgoal && <CardDescription>{hop.subgoal}</CardDescription>}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {/* A poisoned page tried to make the agent leak (the attack demo). */}
        {hop.injections.map((inj, i) => (
          <div key={i} className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
            <p className="font-medium text-amber-400">
              Poisoned page detected · instruction {inj.action === 'blocked' ? 'blocked' : 'ignored'}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {inj.doc_id} tried: <q className="italic">{inj.pattern}</q>
            </p>
          </div>
        ))}

        {hop.decisions.map((d, i) => (
          <div key={i} className="flex flex-col gap-1">
            {attempts > 1 && (
              <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Attempt {i + 1} of {attempts}
              </span>
            )}
            <DecisionRow decision={d} />
          </div>
        ))}

        {attempts === 0 && hop.drafts.map((draft, i) => <Query key={i} label="Drafted" text={draft} />)}

        {hop.search && (
          <p className="text-sm text-muted-foreground">
            Search returned {hop.search.hits.length} result{hop.search.hits.length === 1 ? '' : 's'}
          </p>
        )}

        {/* Contract: an error before dispatch counts as blocked; after dispatch the query did leave. */}
        {hop.errors.map((err, i) =>
          err.replayMiss ? (
            // No saved model answer for this step: explain it, keep the raw error as a tooltip.
            <div key={i} title={err.message} className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
              <p className="font-medium text-amber-400">
                {err.dispatched
                  ? 'Query was sent, then the run stopped: no saved model answer for the next step'
                  : 'Stopped before sending: no saved model answer for this step'}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                The saved model answers don't match the agent's current prompts.
              </p>
            </div>
          ) : err.dispatched ? (
            <div key={i} className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
              <p className="font-medium text-amber-400">Query was sent, but the {err.where} step failed</p>
              <p className="mt-1 text-xs text-muted-foreground">{err.message}</p>
            </div>
          ) : (
            <div key={i} className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm">
              <p className="font-medium text-red-400">Blocked: the {err.where} step failed before sending</p>
              <p className="mt-1 text-xs text-muted-foreground">{err.message}</p>
            </div>
          ),
        )}

        {hop.skipped && (
          <div className="flex flex-col gap-1">
            <Badge className="bg-red-500/15 text-red-400">Hop skipped</Badge>
            <p className="text-sm">{SKIP_REASON[hop.skipped.reason] ?? hop.skipped.reason}</p>
            {hop.skipped.detail && <p className="text-xs text-muted-foreground">{hop.skipped.detail}</p>}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// One guard decision: the badge, what the agent drafted, and what actually left.
import type { DiffOp, GuardDecisionEvent } from '../types'
import { Badge } from '@/components/ui/badge'

type Decision = GuardDecisionEvent['data']

const DECISION_STYLE = {
  pass: { label: 'PASS', className: 'bg-secondary text-secondary-foreground' },
  rewrite: { label: 'REWRITE', className: 'bg-amber-500/15 text-amber-400' },
  block: { label: 'BLOCKED', className: 'bg-red-500/15 text-red-400' },
} as const

// Readable names for the contract's leak types (INTERFACES.md §3 vocabulary).
const LEAK_LABEL: Record<string, string> = {
  exact_value: 'exact value', unit_change: 'unit change', range: 'range', split: 'split across queries',
  derived: 'derived value', relative: 'relative', encoding: 'encoding',
  process_fingerprint: 'process fingerprint', identifier: 'identifier', intent_profile: 'intent profile',
}

export function DecisionRow({ decision }: { decision: Decision }) {
  const style = DECISION_STYLE[decision.decision]

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Badge className={style.className}>{style.label}</Badge>
        {decision.leak_types.map((t) => (
          <Badge key={t} variant="outline">{LEAK_LABEL[t] ?? t}</Badge>
        ))}
      </div>

      {decision.decision === 'pass' && <Query label="Sent" text={decision.original} />}

      {decision.decision === 'rewrite' && (
        <QueryBox label="Rewritten before sending">
          <DiffText diff={decision.diff} />
        </QueryBox>
      )}

      {decision.decision === 'block' && (
        <>
          <QueryBox label="Drafted">
            <span className="text-red-400 line-through decoration-red-400/70">{decision.original}</span>
          </QueryBox>
          <p className="text-sm font-medium text-red-400">Blocked: nothing left the company</p>
        </>
      )}

      {decision.reasons.length > 0 && (
        <p className="text-xs text-muted-foreground">{decision.reasons.join('; ')}</p>
      )}
    </div>
  )
}

// Renders the server's word diff: removed words struck through in red, added words in green.
function DiffText({ diff }: { diff: DiffOp[] }) {
  return (
    <>
      {diff.map((part, i) => {
        const space = i > 0 ? ' ' : ''
        if (part.op === 'delete') {
          return <span key={i}>{space}<del className="text-red-400 decoration-red-400/70">{part.text}</del></span>
        }
        if (part.op === 'insert') {
          return <span key={i}>{space}<ins className="text-emerald-400 no-underline">{part.text}</ins></span>
        }
        return <span key={i}>{space}{part.text}</span>
      })}
    </>
  )
}

export function Query({ label, text }: { label: string; text: string }) {
  return <QueryBox label={label}>{text}</QueryBox>
}

function QueryBox({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <code className="rounded-md bg-muted px-2 py-1 font-mono text-xs leading-relaxed">{children}</code>
    </div>
  )
}

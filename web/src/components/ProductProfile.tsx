// The second kind of leak: not a number, but *which product* this is.
// k = how many real products still match what the searches revealed. High k = hidden in the crowd.
import type { LaneView } from '@/lib/runState'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'

export function ProductProfile({ profile }: { profile: NonNullable<LaneView['profile']> }) {
  const hidden = profile.k >= profile.k_min

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2">
          Product profile
          <Badge className={hidden ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'}>
            {hidden ? 'Hidden in the crowd' : 'Identifiable'}
          </Badge>
        </CardTitle>
        <CardDescription>What the searches reveal about which product this is</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-2">
          {Object.entries(profile.revealed).map(([attr, term]) => (
            <Badge key={attr} variant="outline">
              <span className="text-muted-foreground">{attr}:</span> {term}
            </Badge>
          ))}
        </div>

        <p className="text-sm">
          Could be any of <strong className="tabular-nums">{profile.k}</strong> products
          <span className="text-muted-foreground"> (stays hidden while at least {profile.k_min} match)</span>
        </p>

        {profile.generalized_from && (
          <p className="text-xs text-emerald-400">
            MOAT generalized “{profile.generalized_from}” before sending, so it no longer points to one product line
          </p>
        )}
      </CardContent>
    </Card>
  )
}

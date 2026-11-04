# Golden replay handoff (A8)

Select **Golden · profile rewrite** in the replay picker. This is a prepared comparison
of two real Halden t3, seed 0 runs from PR #72, not a synthetic attack or a simultaneous run.

The unmarked documents have only the classification line removed; their `secrets:` front
matter remains. Graph a7.v5, guard g.v2, Tier 2 t2.v2. Full provenance and archived source
digests are in `sources/profile_rewrite/manifest.json`.

## What it demonstrates

MOAT's profile check found k=4 below k_min=5. Tier 2 removed **pouch cell** from a query,
rechecked it against the same history, and dispatched the revised query. The baseline lane
is the independently recorded no-guard run for the same task/seed. The two agents did not
necessarily produce identical drafts; this is a case study, not a causal effect estimate.

Final numeric observer estimates are real responses matched to each lane's full dispatched
query log. They appear near lane completion, not after every hop. They are absolute observer
guesses, **not baseline-adjusted leakage gains**, and do not establish that MOAT reduced
numeric leakage. The dashboard separately includes the recorded scoring summaries from PR #72,
with the deterministic development attack results from PR #71. These are recorded-run snapshots:
the observer baseline discrepancy means replayed aggregate scores can differ. No significant
paired leakage reduction or utility preservation is claimed. Name/intent estimates cannot be represented by the frozen numeric event schema
and remain in the archived responses.

The prepared stream interleaves lane events for presentation, retaining timestamps and
within-lane order. It assigns a new group/sequence space and remaps interval decision references.
Source queries, guard decisions, search results, reports, profiles, and completions are unchanged.
Only group metadata, replay markers and final observer events are added/updated.

## Keyless use and validation

```sh
uv run python -m agent.golden
uv run pytest -q agent/tests/test_golden.py
make demo
```

No model/search calls or database are needed to build or play this recording. The source
subset is archived here so packaging does not depend on PR #72 merging. Original pilot
artifacts are untouched. The `sources/` subdirectory is not listed by the replay picker.
Source digests are computed over LF-normalized text and `.gitattributes` keeps the archived
sources LF, so the builder and its tests pass from both Windows (CRLF) and macOS/Linux
checkouts.

## Remaining HANDOFF scenarios

- **Profile / k-anonymity:** real recorded rewrite supplied here.
- **Numeric split-query catch:** not demonstrated by this recording. Scripted suite results
  and synthetic fixtures must not be presented as a natural research-agent recording.
- **Injection:** existing detections may be used only with their actual `ignored`/`blocked`
  action. Detection-and-ignore is not proof that an entire document was blocked. A separate
  reviewed scenario is still needed to complete all three original A8 scenarios.

Suggested video: show the prepared-run label, the profile-related rewrite card, the removed
phrase and successful recheck, then the final outsider estimates. Do not describe this as
a numerical mosaic catch, demonstrated utility preservation, or statistically significant
leakage reduction.

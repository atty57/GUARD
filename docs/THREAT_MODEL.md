# MOAT Threat Model

> **Status:** v1 (2026-10-30). Owner: Hyeseong. Changes to the adversary or the claimed properties need the owner's approval.

What we protect, from whom, and what we do and don't claim. Interfaces: `docs/INTERFACES.md`. Secrets and policy values: `docs/DATA_SPEC.md`.

Structured by the four questions of the [Threat Modeling Manifesto](https://www.threatmodelingmanifesto.org/): what are we working on (§1–2), what can go wrong (§3–4), what are we going to do about it (§4–5), did we do a good enough job (§5 tests; evaluation in PLAN §10).

---

## 1. System and trust boundary

```text
 inside the company                                                                 │  outside
                                                                                    │
 internal docs ──▶ agent ──draft──▶ guard.check() ──pass / rewrite──▶ search() ─────┼──▶ search provider
                   ▲  ▲                   │ block                                   │        │
                   │  │                   └──▶ redraft or skip hop                  │        │
                   │  └───────────────────────── results ───────────────────────────┼────────┘
                   ▼                                                                │
           Meta Model API (Muse Spark 1.3; trusted by assumption, §6)               │
                                                   trust boundary = dispatch to search()
```

A query is disclosed the moment it is handed to `search()` (INTERFACES §2). Everything before that point is inside the boundary, except that prompts and docs also reach the model provider, which we trust (§6). A Tier 2 rewrite (planned) is re-checked by Tiers 0–1 before it can go out (INTERFACES §1, `rechecked`). In this project `search()` is a local BM25 corpus (INTERFACES §2) standing in for an external provider, and the evaluation's observer plays A1.

| Trusted | Untrusted |
| --- | --- |
| The host running the agent and guard | The search provider and anyone holding its query logs |
| Internal docs, the policy file and the profile catalog | Content returned by search (may carry injected instructions) |
| The model provider (Meta Model API, standard tier; never the `-contributor` tier, per PLAN §4) | |

Meta's built-in `web_search` tool runs server-side and would bypass the guard, so the agent never uses it (PLAN §4).

## 2. Assets

| Asset | Example (dev split) | Policy field |
| --- | --- | --- |
| Numeric secrets S1–S8 | energy density, Ni content, calcination temperature and hold, Al dopant, anode capacity, yield, cycle life | `value`, `prior`, `min_width`, `threshold` |
| Name secrets S9–S10 | customer codename, start of production | `value`, `equivalents` |
| Product profile | the combination of public-looking attributes that singles out one product or deal | `k_min`, `profile_catalog` |

## 3. Adversaries

The threat category for both adversaries is STRIDE Information Disclosure: exposure of information to people who are not supposed to have it ([Microsoft](https://learn.microsoft.com/en-us/azure/security/develop/threat-modeling-tool-threats)).

**A1. Passive query observer (primary).** The search provider, or anyone with its logs.
- Sees every query the agent dispatches in a session, in order, and can link them to one session.
- Knows public information: typical value ranges (our `prior`), public legal thresholds, public patents.
- Does not see internal docs, model inputs and outputs, guard decisions, or blocked drafts.
- Goal: recover a secret value, narrow it to a useful range, learn which side of a legal threshold it falls on, or identify the product or customer. These are two settings: for value recovery the evaluation tells the observer the company and product (DATA_SPEC §8); the profile check (`k`) is about an observer who doesn't know them.
- Same setting as MosaicLeaks: an LLM adversary that sees only the agent's external queries ([Gurung et al., 2026](https://arxiv.org/abs/2605.30727)).

**A2. Content injector.** Controls a page that search returns (the poisoned pages, DATA_SPEC §5).
- Tries to make the agent put secrets into its next query.
- Cannot reach the guard, the policy or the catalog. The guard checks every query, whatever the reason it was drafted, so A2 is covered only for the leak types marked done in §4: an injected "write the number in words" still gets through.
- This is indirect prompt injection, OWASP [LLM01:2025](https://genai.owasp.org/llmrisk/llm01-prompt-injection/). Rall et al. used it to make agents put a knowledge-base secret into a URL requested through the agent's web-search tool, with no defense in place, and recommend a policy layer between agent and tools ([Rall et al., 2025](https://arxiv.org/abs/2510.09093)). MOAT is such a layer for search queries.

The harm in both cases is OWASP [LLM02:2025](https://genai.owasp.org/llmrisk/llm022025-sensitive-information-disclosure/) Sensitive Information Disclosure. LLM02 is written about model output; applying it to outbound tool arguments is our reading.

## 4. Leak channels and controls

Leak types use the frozen `LeakType` vocabulary (INTERFACES §3).

| Leak type | Example | Control | Status |
| --- | --- | --- | --- |
| `exact_value` | "270 Wh/kg" (secret 272) | Tier 0: value within `bound_tol` | done (H3, #18) |
| `unit_change` | "0.272 kWh/kg", "1018 K" | Tier 0: unit conversion | done (H3, #18) |
| `identifier` | "Ostrava", "3Q27" | Tier 0: names and aliases | done (H3, #18) |
| `range` | "260–280 Wh/kg", "above 265 Wh/kg", "below 265 Wh/kg" (value under the line) | Tier 1 ledger: width, bits, threshold side | H5 (#20), in review |
| `split` | "above 250 Wh/kg" then "below 280 Wh/kg" | Tier 1 ledger: cumulative interval over sent queries | H5 (#20), in review |
| `intent_profile` | "high-nickel pouch … European OEM" then "silicon anode … SOP 2027" | Tier 1 profile check: candidate products that fit, `k_min` | H5 (#20), in review |
| `exact_value` (approximation) | "approximately 640 mAh/g" (secret 620, just outside `bound_tol`) | H4 (#19) | planned |
| `derived`, `relative` | "5 Ah, 3.65 V, 68 g" → 268 Wh/kg | H4 (#19) | planned |
| `encoding` | "two hundred seventy Wh/kg" | H4 (#19) | planned |
| `process_fingerprint` | temperature + dopant + atmosphere together | — | planned, unassigned |

Known gaps in the parser today: unitless numbers and ratios ("0.88 nickel ratio"), a bare "C" for Celsius ("745C"), and bounds without a unit.

## 5. Security properties we claim

P1, P2, P5 and P6 describe `guard.check`. The agent graph still defaults to a pass-through `StubGuard` (`agent/graph.py`) until the real guard is wired in; until then they hold only where `guard.check` is passed in.

| ID | Property | Enforced by | Tested in |
| --- | --- | --- | --- |
| P1 | No dispatched query states a protected value within `bound_tol`, in its own unit or another, for the number and unit forms Tier 0 parses (fraction units only when the attribute is named). A number equal to the secret's public threshold is not counted (DATA_SPEC §2) | Tier 0 | `guard/tests/test_check.py` |
| P2 | For each numeric secret, the interval implied by all dispatched bounds that hold for its value, or miss it by at most half the effective min width, stays at least the min width, within the bits budget, and doesn't settle which side of a threshold the value is on. Only for the bound and range forms Tier 1 parses (a unit is required; fraction units only when the attribute is named in the same query) | Tier 1 ledger | `guard/tests/test_ledger.py` |
| P3 | Fail closed: a guard exception is a block; a blocked draft is never dispatched and never enters the history | agent graph + guard | `agent/tests/test_graph.py` (`test_guard_exception_counts_as_block`, `test_block_at_cap_skips_and_never_searches`); "never enters the history" by code (`query_log` is appended only in `web_search`) |
| P4 | Separation: the guard reads the policy and catalog only; the agent reads docs; the observer reads dispatched queries only; test-split secrets stay out of the repo | repo layout, DATA_SPEC §7 | graph state has no ground-truth field (`agent/tests/test_graph.py::test_state_has_no_ground_truth_field`); observer and test-split paths: planned |
| P5 | Guard `reasons` never contain the matched text or a secret value; they name the secret id and `dim`, or for profile blocks the attribute names and k | guard | `guard/tests/test_check.py::test_reasons_never_contain_the_secret` |
| P6 | At least `k_min` catalog products fit every catalog term in dispatched queries that is true of our product | Tier 1 profile check | `guard/tests/test_profile.py` |

Event logs are inside the boundary: `guard_decision.original` records the blocked draft for audit, and interval and profile previews depend on the secret, so logs from real deployments must be handled like the internal docs.

## 6. Assumptions and what follows from them

| Assumption | Consequence | How we handle it |
| --- | --- | --- |
| A bound that holds for our value, or misses it by at most half the effective min width, is taken to describe the secret; one that misses by more is taken to be about another product | Over-blocking of near-miss bounds (e.g. "below 250 Wh/kg" against 272 settles the threshold side); a bound far off our value is never counted | Report false-positive rate on benign queries next to leakage |
| `bits` treats every value in the prior as equally likely | `bits` is an approximation; a real observer may know more | Primary leakage metric is the LLM observer's gain over its zero-query baseline (PLAN §10; DATA_SPEC §8 check 8). Ledger `bits` drives the live meter and is reported as a deterministic estimate next to it, not as a worst-case bound |
| Priors and min widths are design choices (DATA_SPEC §2) | Results depend on them | `tau` sweep (one knob scales all min widths and the bits budget) |
| All queries in a run are linkable; the ledger is per run | Leaks spread across separate sessions are not tracked | Out of scope (§7) |
| The candidate-product catalog stands in for the market an observer knows | `k` is only as good as the catalog; a small catalog over-blocks, a padded one under-blocks. The catalog also holds our product's true profile, so it is as sensitive as the policy | Catalog is a reviewed data file per company (`data/profile_catalog.*.yaml`); its contents are part of `policy_hash` |
| The model provider is trusted | Prompts and docs reach Meta's API | Standard tier only; a self-hosted open-weight model would remove this assumption |

## 7. Out of scope

- An adaptive attacker who knows the guard's rules and searches for phrasings that pass. We report what the evaluation shows; we don't claim robustness to it.
- Blocks depend on the secret, so a query that never appears can itself be a clue (e.g. an injected page making the agent sweep ranges while the attacker reads the query log). In query-auditing terms the ledger is not a simulatable auditor, one whose deny decisions don't depend on the protected data (Kenthapadi, Mishra & Nissim, "Simulatable Auditing", PODS 2005, [doi:10.1145/1065167.1065183](https://doi.org/10.1145/1065167.1065183)). Not defended.
- Aggregation across separate sessions or agents.
- Side channels: number, timing or size of queries.
- Injected content that keeps triggering blocks to burn the agent's hops (availability). Measured as utility loss, not defended.
- What the search provider sends back (inbound), beyond injected instructions (A2).
- The final report, which goes to the user inside the boundary.
- A compromised host, policy file or model provider.

## 8. Sources for the design

- **Tier 0** is exact data match in the DLP sense: content is compared with a table of known sensitive values rather than generic patterns ([Microsoft Purview EDM](https://learn.microsoft.com/en-us/purview/sit-learn-about-exact-data-match-based-sits)). MOAT adds tolerance, unit conversion and aliases; unlike EDM, which uploads hashes, the policy keeps the values in clear text.
- **Tier 1 ledger** borrows the idea of online query auditing (deny a query if, with earlier ones, it would pin a protected value) but is not a formal auditor: it is a heuristic over intervals parsed from free text (see §7).
- **`bits`** = log2(prior width / interval width): per-observation min-entropy gain under a uniform prior, i.e. log2 of posterior over prior vulnerability (after Smith, "On the Foundations of Quantitative Information Flow", FoSSaCS 2009, [doi:10.1007/978-3-642-00596-1_21](https://doi.org/10.1007/978-3-642-00596-1_21)).
- **Mosaic leaks**: pieces harmless alone, revealing together (Pozen, "The Mosaic Theory, National Security, and the Freedom of Information Act", 115 Yale L.J. 628, 2005, [link](https://yalelawjournal.org/note/the-mosaic-theory-national-security-and-the-freedom-of-information-act)).
- **Profile check** is k-anonymity-style (Sweeney, "k-Anonymity: A Model for Protecting Privacy", IJUFKS 10(5):557–570, 2002, [link](https://dataprivacylab.org/projects/kanonymity/)): k is counted over a hand-written catalog of fictional products, with no automatic generalization.

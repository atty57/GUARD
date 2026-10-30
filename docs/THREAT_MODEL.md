# MOAT Threat Model

> **Status:** v1 (2026-10-30). Owner: Hyeseong. Changes to the adversary or the claimed properties need the owner's approval.

What we protect, from whom, and what we do and don't claim. Interfaces: `docs/INTERFACES.md`. Secrets and policy values: `docs/DATA_SPEC.md`.

---

## 1. System and trust boundary

```text
 inside the company                                          │  outside
                                                             │
 internal docs ──▶ agent (LangGraph, Muse Spark 1.3) ──draft──▶ guard.check() ──pass──▶ search()  ──▶ search provider
                        ▲                                    │        │ block                         │
                        └──────────── results ───────────────┼────────┴──── redraft / skip hop       │
                                                             │                                       │
                                                   trust boundary = dispatch to search()
```

A query is disclosed the moment it is handed to `search()` (INTERFACES §2). Everything before that point is inside the boundary.

| Trusted | Untrusted |
| --- | --- |
| The host running the agent and guard | The search provider and anyone holding its query logs |
| Internal docs and the policy file | Content returned by search (may carry injected instructions) |
| The model provider (Meta Model API, standard tier; never the `-contributor` tier, per PLAN §4) | |

Meta's built-in `web_search` tool runs server-side and would bypass the guard, so the agent never uses it (PLAN §4).

## 2. Assets

| Asset | Example (dev split) | Policy field |
| --- | --- | --- |
| Numeric secrets S1–S8 | energy density, Ni content, calcination temperature and hold, Al dopant, anode capacity, yield, cycle life | `value`, `prior`, `min_width`, `threshold` |
| Name secrets S9–S10 | customer codename, start of production | `value`, `equivalents` |
| Product profile | the combination of public-looking attributes that singles out one product or deal | `k_min` |

## 3. Adversaries

**A1. Passive query observer (primary).** The search provider, or anyone with its logs.
- Sees every query the agent dispatches in a session, in order, and can link them to one session.
- Knows public information: typical value ranges (our `prior`), public legal thresholds, public patents.
- Does not see internal docs, model inputs and outputs, guard decisions, or blocked drafts.
- Goal: recover a secret value, narrow it to a useful range, learn which side of a legal threshold it falls on, or identify the product or customer.
- Same setting as MosaicLeaks: an LLM adversary that sees only the agent's external queries ([Gurung et al., 2026](https://arxiv.org/abs/2605.30727)).

**A2. Content injector.** Controls a page that search returns (the poisoned pages, DATA_SPEC §5).
- Tries to make the agent put secrets into its next query.
- Cannot reach the guard or the policy. The guard checks every query, whatever the reason it was drafted.

## 4. Leak channels and controls

Leak types use the frozen `LeakType` vocabulary (INTERFACES §3).

| Leak type | Example | Control | Status |
| --- | --- | --- | --- |
| `exact_value` | "270 Wh/kg" (secret 272) | Tier 0: value within `bound_tol` | done (#38) |
| `unit_change` | "0.272 kWh/kg", "1018 K" | Tier 0: unit conversion | done (#38) |
| `identifier` | "Ostrava", "3Q27" | Tier 0: names and aliases | done (#38) |
| `range` | "260–280 Wh/kg", "above 265 Wh/kg" | Tier 1 ledger: width, bits, threshold side | done (ledger PR) |
| `split` | "above 250" then "below 280" | Tier 1 ledger: cumulative interval over sent queries | done (ledger PR) |
| `intent_profile` | high-Ni + pouch + European OEM + SOP 2027 | Tier 1 profile check (`k_min`) | planned |
| `derived`, `relative` | "5 Ah, 3.7 V, 68 g" → 272 Wh/kg | H4 | planned |
| `encoding` | "two hundred seventy Wh/kg" | H4 | planned |
| `process_fingerprint` | temperature + dopant + atmosphere together | planned | planned |

## 5. Security properties we claim

| ID | Property | Enforced by | Tested in |
| --- | --- | --- | --- |
| P1 | No dispatched query states a protected value, in its own unit or another, within `bound_tol` | Tier 0 | `guard/tests/test_check.py` |
| P2 | For each numeric secret, the interval implied by all dispatched bounds stays at least the effective min width, within the bits budget, and doesn't settle which side of a threshold the value is on | Tier 1 ledger | `guard/tests/test_ledger.py` |
| P3 | Fail closed: a guard exception is a block; a blocked draft is never dispatched and never enters the history | agent graph + guard | `agent/tests/test_graph.py` (`test_guard_exception_counts_as_block`, `test_block_at_cap_skips_and_never_searches`) |
| P4 | Separation: the guard reads the policy only; the agent reads docs; the observer reads dispatched queries only; test-split secrets stay out of the repo | repo layout, DATA_SPEC §7 | `agent/tests/test_graph.py` (`test_state_has_no_ground_truth_field`) |
| P5 | Guard `reasons` never contain a secret value | guard | `test_reasons_never_contain_the_secret` |

Event logs are inside the boundary: `guard_decision.original` records the blocked draft for audit, so logs from real deployments must be handled like the internal docs.

## 6. Assumptions and what follows from them

| Assumption | Consequence | How we handle it |
| --- | --- | --- |
| Worst-case observer: every bound in a query is taken to describe the secret | Over-blocking of harmless bounds | Report false-positive rate on benign queries next to leakage |
| `bits` treats every value in the prior as equally likely | `bits` is an approximation; a real observer may know more | Official leakage metric is the LLM observer's gain over its zero-query baseline (DATA_SPEC §8 check 8); ledger bits are the live meter only |
| Priors and min widths are design choices (DATA_SPEC §2) | Results depend on them | `tau` sweep (one knob scales all min widths and the bits budget) |
| All queries in a run are linkable; the ledger is per run | Leaks spread across separate sessions are not tracked | Out of scope (§7) |
| The model provider is trusted | Prompts and docs reach Meta's API | Standard tier only; a self-hosted open-weight model would remove this assumption |

## 7. Out of scope

- An adaptive attacker who knows the guard's rules and searches for phrasings that pass. We report what the evaluation shows; we don't claim robustness to it.
- Aggregation across separate sessions or agents.
- Side channels: number, timing or size of queries.
- What the search provider sends back (inbound), beyond injected instructions (A2).
- The final report, which goes to the user inside the boundary.
- A compromised host, policy file or model provider.

## 8. Design rationale and related work

- **Why a control on outbound queries.** Meta's Agents Rule of Two says an agent should have at most two of: [A] processing untrustworthy inputs, [B] access to sensitive systems or private data, [C] changing state or communicating externally ([Meta AI, 2025](https://ai.meta.com/blog/practical-ai-agent-security/)). A research agent that reads internal docs and searches the web has all three. MOAT is a deterministic control on [C], the query channel.
- **Where it sits next to existing guardrails.** LlamaFirewall provides PromptGuard 2 (jailbreak detection), Agent Alignment Checks (reasoning audit), CodeShield (insecure code) and customizable scanners ([Chennabasappa et al., 2025](https://arxiv.org/abs/2505.03574)). MOAT checks a different thing: what the agent's outbound queries reveal, across the whole session.
- **Measuring leakage in bits.** The ledger's `bits` follows quantitative information flow: leakage as the reduction in an observer's uncertainty ([Smith, 2009](https://doi.org/10.1007/978-3-642-00596-1_21)).
- **Mosaic leaks.** Pieces that are harmless alone can reveal a secret together; this is the "mosaic theory" of US national-security FOIA law ([Pozen, 2005](https://yalelawjournal.org/note/the-mosaic-theory-national-security-and-the-freedom-of-information-act)). The ledger is a mosaic check over one session.
- **Profile check.** `k_min` follows k-anonymity: what has gone out should still fit at least k candidates ([Sweeney, 2002](https://dataprivacylab.org/projects/kanonymity/)).
- **An alternative defense.** TrackMeNot hides real search queries among generated decoy queries ([Toubiana, Subramanian & Nissenbaum, 2011](https://arxiv.org/abs/1109.4677)). MOAT changes or stops the real query instead, so no decoy traffic is needed.

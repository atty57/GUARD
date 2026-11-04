// Readable names for the contract's leak types (INTERFACES.md §3 vocabulary).
// Shared by the decision cards (Compare screen) and the attack chart (Results screen).
export const LEAK_LABEL: Record<string, string> = {
  exact_value: 'exact value', unit_change: 'unit change', range: 'range', split: 'split across queries',
  derived: 'derived value', relative: 'relative', encoding: 'encoding',
  process_fingerprint: 'process fingerprint', identifier: 'identifier', intent_profile: 'intent profile',
}

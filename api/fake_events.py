# Fake events for building the UI before the real agent is ready.

FAKE_MOAT_RUN = [
    {"type": "run_started", "config": "moat",
     "question": "What patents could block our new pouch cell?", "strictness": 0.7},

    {"type": "agent_draft", "config": "moat", "step": 1,
     "query": "pouch cell 270 Wh/kg high-nickel cathode"},
    {"type": "guard_check", "config": "moat", "step": 1,
     "action": "rewrite",
     "original": "pouch cell 270 Wh/kg high-nickel cathode",
     "sent": "high energy density pouch cell high-nickel cathode",
     "matched": ["270 Wh/kg"], "leak_type": "exact_value",
     "reason": "Matches secret: energy density"},
    {"type": "search", "config": "moat", "step": 1,
     "sent_query": "high energy density pouch cell high-nickel cathode",
     "num_results": 4},

    {"type": "agent_draft", "config": "moat", "step": 2,
     "query": "commercial pouch cells above 250 Wh/kg"},
    {"type": "guard_check", "config": "moat", "step": 2,
     "action": "allow",
     "original": "commercial pouch cells above 250 Wh/kg",
     "sent": "commercial pouch cells above 250 Wh/kg",
     "matched": [], "leak_type": "range_lower_bound",
     "reason": "A lower bound alone leaves a wide range. Logged to the ledger."},
    {"type": "search", "config": "moat", "step": 2,
     "sent_query": "commercial pouch cells above 250 Wh/kg", "num_results": 5},

    {"type": "agent_draft", "config": "moat", "step": 3,
     "query": "pouch cell energy density below 280 Wh/kg"},
    {"type": "guard_check", "config": "moat", "step": 3,
     "action": "block",
     "original": "pouch cell energy density below 280 Wh/kg",
     "sent": None,
     "matched": ["below 280 Wh/kg"], "leak_type": "split_across_queries",
     "reason": "With step 2 already sent, this would narrow energy density to 250-280 Wh/kg"},

    {"type": "report", "config": "moat",
     "text": "Found 6 patents that may overlap with the cell design."},
    {"type": "run_done", "config": "moat"},
]

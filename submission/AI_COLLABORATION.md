# AI Collaboration Log

## 1. How AI Tools Helped
- **Boilerplate & Data Loaders**: Scaffolded `@dataclass` models in `src/models.py` and CSV/JSONL stream readers in `src/cli.py`.
- **Test Suite**: Generated initial unit test fixtures in `tests/test_agent.py`.

## 2. Verification of Output
- Verified all published actions against `simulator/simulate_outcomes.py`, achieving **`policy_problem_count: 0`** across all 2,769 triggers.
- Re-analyzed `pilot/pilot_outcomes.csv`, matching the internal report's biased Served-vs-Others headline (`40.1% vs 17.7% = +22.43 pp`) and proving the true randomized Intention-to-Treat (ITT) lift (`18.2% Treatment vs 19.6% Holdout = -1.30 pp`).

## 3. AI Suggestions We Rejected
1. **Rejected: Using `received_at - occurred_at` for Offline Buffer Detection**: Phone clocks are skewed by hours. We instead used `sent_at - occurred_at` to isolate true offline buffering delay from clock skew.
2. **Rejected: Trusting LLM Goal Eligibility Claims**: In `model_faults.jsonl`, the model claims `U-01991`, `U-02509`, and `U-04860` are on active trials (`G04`). `IndependentVerifier` cross-checks resolved state facts and blocks all three claims.
3. **Rejected: Allowing Goals `G10` and `G11`**: `G10` has `event: null` and `G11` uses `tv_app_open` (never emitted in `events.jsonl`). Both are blocked by `validate_goal_definition`.

# AI Collaboration Log

## 1. How AI Coding Tools Helped
- **Schema & Boilerplate Generation**: Used AI assistance to scaffold the `@dataclass` structures in `src/models.py` and CSV/JSONL loaders in `src/cli.py`.
- **Unit Test Harness**: Drafted initial test cases in `tests/test_agent.py` for offline-buffered triggers, 3-way dialect conflicts, model faults, and mid-replay rule corrections.

## 2. How We Verified AI Output
- Verified every generated action against `simulator/simulate_outcomes.py`, achieving **`policy_problem_count: 0`** across all 2,769 triggers.
- Audited `pilot/pilot_outcomes.csv` and verified that our `CausalImpactEvaluator` matched the internal report's biased headline (`40.1% vs 17.7% = +22.43 pp`) while exposing the true randomized ITT effect (`18.2% vs 19.6% = -1.30 pp`).

## 3. AI Suggestions We Explicitly Rejected
1. **Rejected: Using `received_at - occurred_at` Alone to Detect Offline Buffered Triggers**
   - *Why*: Phone clocks can be wrong by hours. By inspecting the event schema (`occurred_at`, `sent_at`, `received_at`), we used `sent_at - occurred_at` (both on the phone's clock) to measure true offline buffering lag and `received_at - sent_at` to measure clock skew.
2. **Rejected: Letting the Model Assert Goal Eligibility Directly**
   - *Why*: In `replay/model_faults.jsonl`, the model claims `U-01991`, `U-02509`, and `U-04860` are on an active trial ending tomorrow (`G04` with `0.97` confidence), when their actual profile/event status is `never` or `lapsed`. Our `IndependentVerifier` cross-checks resolved facts and rejects all three claims.
3. **Rejected: Selecting Goals `G10` (`meaningful_engagement_7d`) or `G11` (`tv_activation_14d`)**
   - *Why*: `G10` has `event: null` and `G11` requires `tv_app_open`, which never appears in `data/events.jsonl`. `IndependentVerifier.validate_goal_definition` blocks both goals before planning.

# STAGE Causal Impact & Pilot Re-Analysis Report

## 1. Re-Analysis of Past Pilot (pilot/pilot_outcomes.csv)

The internal pilot summary report claimed a **+22.4 percentage-point uplift** (40.1% vs 17.7%) by comparing **served users (n=217)** against **all other users (n=3,674)**.

| Comparison Method | Group A | Group B | Difference (pp) | Validity |
|---|---|---|---|---|
| **Internal Report (Served vs. All Others)** | Served: 40.1% (217) | Unserved/Holdout: 17.7% (3674) | **+22.43 pp** | **BIASED (Selection Bias)** |
| **True Intention-to-Treat (Treatment vs. Holdout Arm)** | Treatment Arm: 18.2% (1912) | Holdout Arm: 19.6% (1979) | **-1.30 pp** (95% CI: [-3.76, 1.16]) | **UNBIASED (Causal ITT)** |

### Why 'Served vs. Everyone Else' Misleads
In the pilot, only ~11.3% of treatment users (217 / 1,912) were actually served. To be served, a user had to open the app in a fresh session, have sufficient history/eligibility, and be in an active dialect. Those users had a higher baseline propensity to watch and convert regardless of the agent. When evaluated properly via **Intention-to-Treat (ITT)** across all 1,912 treatment users versus all 1,979 randomized holdout users, the true pilot lift was near zero.

---

## 2. Replay Day Causal Impact (replay/arms.csv + 14-Day Simulator)

| Metric (14-Day Horizon) | Agent Arm (ITT, n=863) | Holdout Arm (n=883) | True ITT Uplift (95% CI) |
|---|---|---|---|
| **Any Play Started Rate** | 77.75% | 75.76% | **+1.99 pp** (-1.97 to +5.95 pp) |
| **Episode Completion Rate** | 57.01% | 56.85% | **+0.16 pp** (-4.49 to +4.81 pp) |
| **Paid Subscription Conversion** | 4.17% | 4.08% | **+0.09 pp** (-1.78 to +1.96 pp) |

*Contrast with Naive Served-vs-Unserved Episode Completion*: Served (n=717) converted at **62.06%** vs Unserved (n=1029) at **53.35%** (**+8.71 pp**), demonstrating why ITT against the randomized holdout is essential.

## 3. Committed Goal Breakdown (Agent Arm)

| Goal ID | Committed Users | Succeeded | Failed | Success Rate |
|---|---|---|---|---|
| G01 | 10 | 3 | 7 | 30.0% |
| G02 | 179 | 28 | 151 | 15.6% |
| G03 | 50 | 3 | 47 | 6.0% |
| G04 | 1 | 0 | 1 | 0.0% |
| G05 | 32 | 2 | 30 | 6.2% |
| G06 | 8 | 0 | 8 | 0.0% |
| G07 | 150 | 1 | 149 | 0.7% |
| G08 | 286 | 258 | 28 | 90.2% |
| G12 | 1 | 1 | 0 | 100.0% |

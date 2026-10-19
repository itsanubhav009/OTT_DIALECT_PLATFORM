# STAGE — One User, One Agent

An evidence-driven per-viewer lifecycle agent system built for STAGE OTT. Each user is owned by a dedicated stateful agent (ViewerState) that resolves conflicting sources with explicit precedence, selects one measurable goal, verifies all STAGE messaging, home-page, and holdout rules independently of the planner step, acts within strict latency (<60s) and call budgets (<=8 model calls, <=20 tool calls), and proves true Intention-to-Treat (ITT) impact against the randomized holdout arm.

## Setup & One-Command Replay

Requires only Python 3.9+ standard library. No external packages or personal API keys are needed.

### 1. Run the Full Replay, Simulator & Impact Evaluation (One Command)

    cd submission && python3 -m src.cli --pack . --out sample_run

### 2. Run the Automated Test Suite

    cd submission && python3 -m unittest discover -s tests -v

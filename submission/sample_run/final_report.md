# Final Report: One User, One Agent (STAGE)

## 1. Executive Summary
- **Triggers Processed**: 2769 across 1746 distinct users.
- **Actions Published**: 927 (910 home pages accepted, 17 WhatsApp messages delivered).
- **Simulator Policy Violations**: **0** (0 holdout actions, 0 consent violations, 0 quiet-hour violations, 0 frequency-cap violations).
- **Latency Budget**: Max decision time 5 ms (100% under 60,000 ms SLA).

## 2. Data & Config Traps Found and Handled

| Trap Category | Concrete Findings in Pack | How Our Agent Handled It |
|---|---|---|
| **Duplicate Events** | 2176 duplicate event_id rows in data/events.jsonl + duplicate triggers in replay/triggers.jsonl | Deduplicated via per-viewer seen_event_ids and seen_trigger_ids; duplicate triggers idempotently return ABSTAINED. |
| **Wrong Phone Clocks & Offline Buffers** | 5486 historical events with >5m clock skew + offline-buffered triggers (sent_at - occurred_at > 600s) | Computed true offline lag (sent_at - occurred_at) vs server received_at; blocked stale buffered triggers via stale_offline_buffered_trigger. |
| **Profile Stale & Duplicate Rows** | 30 users have 2 rows in profiles_snapshot.csv; brand-new users (4,888 rows for 5,000 users) have 0 rows | Picked latest updated_at row deterministically; built brand-new user state from signup_completed events; overrode stale profile subscription/opt-in with newer stream events. |
| **3-Way Dialect Conflicts** | app_dialect vs preferred_dialect (profile) vs properties.deeplink (ott://c/<cmp>/<dialect>/title/<id>) | Applied precedence (app_setting > campaign_link > profile_snapshot) and exposed conflicts in decision.dialect.conflicts. |
| **Prompt Injection in Catalogue** | Flagged titles ['T0002', 'T0522'] in data/catalogue.csv | Sanitized descriptions via IndependentVerifier.sanitize_catalogue_item and never passed raw descriptions as instructions. |
| **Goal Catalogue Traps** | G10 (event: null), G11 (tv_app_open absent from stream), G07/G09/G12 prose eligibility | Blocked G10 and G11 in verifier; codified explicit deterministic checks for G07, G09, and G12. |
| **Replay Control Events** | 4 events (T0001 withdrawn, G03 corrected to play_started, dependency faults) | Immediately removed T0001 at 07:00Z; updated only open G03 plans at 08:30Z; fell back safely during dependency outage windows. |
| **Replay Model Faults** | 3 unsupported_claim (U-01991, U-02509, U-04860), 1 malformed_output (U-02563), 1 timeout (U-01090) | Verifier rejected the 3 unsupported G04 eligibility claims; parser recovered from malformed JSON; timeout circuit breaker degraded safely to FALLBACK_NO_ACTION. |

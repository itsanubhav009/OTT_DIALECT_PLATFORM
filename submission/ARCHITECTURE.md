# System Architecture: One User, One Agent (STAGE)

## 1. Per-Viewer Ownership & Concurrency
Every viewer (`user_id`) is owned by a single isolated `ViewerState` instance (`src/models.py`). No two components ever pursue competing goals for the same user:
- **Single Active Goal**: `ViewerState.active_plan` holds at most one committed goal (`G01`–`G12`), its deadline, rule version, and target intervention.
- **Idempotency**: Duplicate `event_id` rows in `data/events.jsonl` and `replay/triggers.jsonl` are deduplicated via `seen_event_ids` and `seen_trigger_ids`.

## 2. Source Precedence, Clock Skew & Freshness
All facts are stored as `ProvenanceFact(value, source, observed_at, device_time, freshness_s, clock_skew_s, ref_id)` (`src/state_resolver.py`):
- **Clock Skew vs. Offline Buffering**: `received_at` (server time) is used for ordering. Offline buffering delay is isolated via `sent_at - occurred_at`, while phone clock skew is isolated via `received_at - sent_at`. Triggers buffered offline for `> 600s` fail `stale_offline_buffered_trigger` and abstain (`NO_ACTION`).
- **Explicit Dialect Precedence**: Live `app_setting` (`app_dialect` on fresh events) > `campaign_link` (`ott://c/<cmp>/<dialect>/title/<id>`) > `profile_snapshot` (`preferred_dialect`, 1 day stale). Conflicting sources are preserved in `decision.dialect.conflicts`.
- **Subscription & Consent Precedence**: Real-time `purchase_completed`, `subscription_renewed`, `subscription_lapsed`, and `notification_opt_out` events override `profiles_snapshot.csv`. Duplicate profile rows for the same `user_id` are resolved deterministically by latest `updated_at`.

## 3. Reactive Planning & Selective Control Event Handling
- **Title Withdrawal (`T0001` at `07:00Z`)**: `apply_control_event` immediately updates `T0001`'s `available_until` to `2026-10-01T07:00:00Z` so it is never shown or promoted afterwards.
- **Goal Rule Correction (`G03` at `08:30Z`)**: Updates only open `G03` plans from `app_open` to `play_started` on a later IST day, versioning the rule (`G03-corrected`) without reprocessing unaffected users.
- **Dependency Outages (`model_provider` `10:35Z–10:42Z` & `user_data_store` `13:10Z–13:17Z`)**: Automatically switches to fast deterministic fallback (`fallback_used = True`), keeping 100% of decisions under the 60s SLA.

## 4. Independent Verification & Trust Boundaries
`IndependentVerifier` (`src/verifier.py`) runs separately from `PlannerModelProvider` and can reject any decision:
- **Goal Traps**: Blocks `G10` (`meaningful_engagement_7d`, `event: null`) and `G11` (`tv_activation_14d`, `tv_app_open` absent from stream).
- **Model Faults**: Rejects the 3 unsupported `G04` trial eligibility claims (`U-01991`, `U-02509`, `U-04860`), recovers from `malformed_output` (`U-02563`), and falls back safely to `FALLBACK_NO_ACTION` on `timeout` (`U-01090`).
- **Policy Enforcement**: Enforces zero actions on `holdout` arm users, `content_slug` deduplication across all home-page rows (`3–10` titles/row, `<=6` rows, `120m` TTL), IST quiet hours (`22:00–08:00 IST`), `1/IST-day` combined push+WhatsApp cap, and approved templates/prices (`₹1`, `₹149`, `₹399`).

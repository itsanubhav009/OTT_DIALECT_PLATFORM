# System Architecture: One User, One Agent (STAGE)

## 1. Ownership & Concurrency
Every viewer (`user_id`) is owned by one isolated `ViewerState` instance (`src/models.py`). It holds at most one `active_plan` (`G01`–`G12`), its deadline, rule version, and action history. Duplicate `event_id` rows in `data/events.jsonl` and `replay/triggers.jsonl` are deduplicated idempotently via `seen_event_ids` and `seen_trigger_ids`.

## 2. Source Precedence, Clock Skew & Freshness
All facts carry `ProvenanceFact(value, source, observed_at, device_time, freshness_s, clock_skew_s, ref_id)` (`src/state_resolver.py`):
- **Clock Skew vs. Offline Buffering**: Server `received_at` is trustworthy. Offline lag is measured via `sent_at - occurred_at` (both on the phone's clock), while clock skew is `received_at - sent_at`. Triggers buffered offline for `> 600s` fail `stale_offline_buffered_trigger` and abstain (`NO_ACTION`).
- **Dialect Precedence**: Fresh `app_setting` (`app_dialect`) > `campaign_link` (`deeplink`) > `profile_snapshot` (`preferred_dialect`, 1 day stale). Conflicts are logged in `decision.dialect.conflicts`.
- **Subscription & Consent Precedence**: Stream events (`purchase_completed`, `subscription_renewed`, `subscription_lapsed`, `notification_opt_out`) override stale `profiles_snapshot.csv` rows. Duplicate profile rows for the same `user_id` pick the latest `updated_at`.

## 3. Reactive Planning & Control Events
- **Title Withdrawal (`T0001` at `07:00Z`)**: Immediately sets `available_until = 2026-10-01T07:00:00Z` so `T0001` is never shown afterwards.
- **Goal Rule Correction (`G03` at `08:30Z`)**: Updates only open `G03` plans from `app_open` to `play_started` on a later IST day without reprocessing other users.
- **Dependency Outages (`10:35Z` & `13:10Z`)**: Degrades safely to deterministic rule fallback within `<25ms` max latency (well inside the 60s SLA).

## 4. Independent Verification & Trust Boundaries
`IndependentVerifier` (`src/verifier.py`) runs separately from `PlannerModelProvider`:
- Blocks unmeasurable/unobservable goals `G10` (`event: null`) and `G11` (`tv_app_open` absent from stream).
- Rejects the 3 unsupported `G04` trial eligibility claims in `replay/model_faults.jsonl` (`U-01991`, `U-02509`, `U-04860`), recovers from `malformed_output` (`U-02563`), and falls back to `FALLBACK_NO_ACTION` on `timeout` (`U-01090`).
- Enforces zero actions on `holdout` arm users, `content_slug` deduplication (`3–10` titles/row, `<=6` rows, `120m` TTL), IST quiet hours (`22:00–08:00`), `1/IST-day` combined push+WhatsApp cap, and approved prices (`₹1`, `₹149`, `₹399`).

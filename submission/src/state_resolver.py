import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Tuple, List, Optional
from .models import ViewerState, ProvenanceFact

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
DEEPLINK_RE = re.compile(r"^ott://c/([^/]+)/([^/]+)/title/([^/]+)$")


def parse_iso(ts_str: Optional[str]) -> Optional[datetime]:
    if not ts_str:
        return None
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    return datetime.fromisoformat(ts_str)


def format_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def to_ist_day_and_hour(utc_iso: str) -> Tuple[str, int]:
    dt = parse_iso(utc_iso)
    if not dt:
        return "2026-10-01", 12
    ist_dt = dt.astimezone(IST)
    return ist_dt.strftime("%Y-%m-%d"), ist_dt.hour


class StateResolver:
    """
    Explicit source precedence and freshness evaluation per fact type:
    1. Dialect: live app_setting (sent with current event) > campaign_link (deeplink) > profile_snapshot (warehouse export).
    2. Subscription status: real-time event_stream (purchase_completed / subscription_lapsed) > profile_snapshot.
    3. Consent: real-time notification_opt_out event overrides profile_snapshot opt-in.
    4. Profile duplicate rows: resolved deterministically by picking latest updated_at timestamp.
    """

    DIALECT_PRECEDENCE = {
        "app_setting": 3,
        "campaign_link": 2,
        "profile_snapshot": 1,
    }

    @classmethod
    def ingest_profile_rows(cls, state: ViewerState, rows: List[Dict[str, Any]], now_iso: str) -> None:
        if not rows:
            return
        now_dt = parse_iso(now_iso) or datetime(2026, 9, 30, tzinfo=UTC)
        sorted_rows = sorted(rows, key=lambda r: r.get("updated_at") or "", reverse=True)
        chosen = sorted_rows[0]
        upd_ts = chosen.get("updated_at") or chosen.get("snapshot_at") or now_iso
        upd_dt = parse_iso(upd_ts) or now_dt
        freshness_s = max(0, int((now_dt - upd_dt).total_seconds()))
        ref_id = f"profile:{state.user_id}"

        if len(sorted_rows) > 1:
            seen_dialects = [r.get("preferred_dialect") for r in sorted_rows]
            seen_subs = [r.get("subscription_status") for r in sorted_rows]
            state.learnings.append(
                f"Resolved {len(sorted_rows)} duplicate profile rows (dialects={seen_dialects}, subs={seen_subs}) by latest updated_at={upd_ts}."
            )

        if chosen.get("preferred_dialect"):
            state.add_fact(
                "dialect",
                ProvenanceFact(
                    value=chosen["preferred_dialect"],
                    source="profile_snapshot",
                    observed_at=upd_ts,
                    freshness_s=freshness_s,
                    ref_id=ref_id,
                ),
            )

        if chosen.get("subscription_status"):
            state.add_fact(
                "subscription_status",
                ProvenanceFact(
                    value=chosen["subscription_status"],
                    source="profile_snapshot",
                    observed_at=upd_ts,
                    freshness_s=freshness_s,
                    ref_id=ref_id,
                ),
            )

        if chosen.get("signup_at"):
            state.add_fact(
                "signup_at",
                ProvenanceFact(
                    value=chosen["signup_at"],
                    source="profile_snapshot",
                    observed_at=upd_ts,
                    freshness_s=freshness_s,
                    ref_id=ref_id,
                ),
            )

        for consent_col in ["notifications_opt_in", "whatsapp_opt_in"]:
            if consent_col in chosen and chosen[consent_col] != "":
                val = str(chosen[consent_col]).lower() == "true"
                state.add_fact(
                    consent_col,
                    ProvenanceFact(
                        value=val,
                        source="profile_snapshot",
                        observed_at=upd_ts,
                        freshness_s=freshness_s,
                        ref_id=ref_id,
                    ),
                )

    @classmethod
    def compute_event_timing(cls, event: Dict[str, Any]) -> Tuple[int, int]:
        """
        Uses server received_at as truth and (sent_at - occurred_at) to isolate
        offline buffering delay from phone clock skew (matching simulator logic).
        Returns (offline_buffer_age_s, clock_skew_s).
        """
        r = parse_iso(event.get("received_at"))
        s = parse_iso(event.get("sent_at"))
        o = parse_iso(event.get("occurred_at"))
        if not r:
            return 0, 0
        if s and o:
            offline_lag_s = int((s - o).total_seconds())
            clock_skew_s = int((r - s).total_seconds())
            return max(0, offline_lag_s), clock_skew_s
        if o:
            diff_s = int((r - o).total_seconds())
            return max(0, diff_s), 0
        return 0, 0

    @classmethod
    def ingest_event(
        cls,
        state: ViewerState,
        event: Dict[str, Any],
        catalogue_by_id: Dict[str, Dict[str, Any]],
    ) -> bool:
        eid = event.get("event_id", "")
        if eid and eid in state.seen_event_ids:
            return False
        if eid:
            state.seen_event_ids.append(eid)

        received_at = event.get("received_at") or "2026-10-01T00:00:00.000Z"
        age_s, skew_s = cls.compute_event_timing(event)
        ref_id = f"event:{eid}"
        ev_type = event.get("event") or event.get("event_type") or ""
        props = event.get("properties") or {}

        app_dialect = event.get("app_dialect")
        if app_dialect and age_s <= 600:
            state.add_fact(
                "dialect",
                ProvenanceFact(
                    value=app_dialect,
                    source="app_setting",
                    observed_at=received_at,
                    device_time=event.get("occurred_at"),
                    freshness_s=age_s,
                    clock_skew_s=skew_s,
                    ref_id=ref_id,
                ),
            )

        if ev_type == "app_dialect_changed" and props.get("to"):
            state.add_fact(
                "dialect",
                ProvenanceFact(
                    value=props["to"],
                    source="app_setting",
                    observed_at=received_at,
                    freshness_s=0,
                    ref_id=ref_id,
                ),
            )

        deeplink = props.get("deeplink")
        if deeplink:
            m = DEEPLINK_RE.match(str(deeplink))
            if m:
                _, cmp_dialect, _ = m.groups()
                state.add_fact(
                    "dialect",
                    ProvenanceFact(
                        value=cmp_dialect,
                        source="campaign_link",
                        observed_at=received_at,
                        freshness_s=age_s,
                        ref_id=ref_id,
                    ),
                )

        ist_day_str, _ = to_ist_day_and_hour(received_at)

        if ev_type == "signup_completed":
            state.add_fact(
                "signup_at",
                ProvenanceFact(value=received_at, source="event_stream", observed_at=received_at, freshness_s=age_s, ref_id=ref_id),
            )
            if "subscription_status" not in state.facts:
                state.add_fact(
                    "subscription_status",
                    ProvenanceFact(value="never", source="event_stream", observed_at=received_at, freshness_s=age_s, ref_id=ref_id),
                )

        elif ev_type == "app_open" and age_s <= 600:
            if ist_day_str not in state.open_ist_days:
                state.open_ist_days.append(ist_day_str)
            state.last_open_at = received_at

        elif ev_type == "play_started":
            state.play_count += 1
            if ist_day_str not in state.play_ist_days:
                state.play_ist_days.append(ist_day_str)
            tid = props.get("title_id")
            c_item = catalogue_by_id.get(tid) if tid else None
            if c_item:
                for g in c_item.get("genres_list", []):
                    state.genre_counts[g] = state.genre_counts.get(g, 0) + 1
                slug = c_item.get("content_slug")
                if slug:
                    state.slug_last_played_at[slug] = received_at
            state.add_fact(
                "play_started",
                ProvenanceFact(value=tid or "unknown", source="event_stream", observed_at=received_at, freshness_s=age_s, ref_id=ref_id),
            )

        elif ev_type == "episode_completed":
            state.complete_count += 1
            tid = props.get("title_id")
            ep_id = props.get("episode_id")
            c_item = catalogue_by_id.get(tid) if tid else None
            if c_item and ep_id:
                slug = c_item.get("content_slug")
                if slug:
                    eps = state.slug_completed_episodes.setdefault(slug, [])
                    if ep_id not in eps:
                        eps.append(ep_id)
            state.add_fact(
                "episode_completed",
                ProvenanceFact(value=tid or "unknown", source="event_stream", observed_at=received_at, freshness_s=age_s, ref_id=ref_id),
            )

        elif ev_type == "purchase_completed":
            is_trial = bool(props.get("is_trial", False))
            new_status = "trial_active" if is_trial else "active"
            state.add_fact(
                "subscription_status",
                ProvenanceFact(value=new_status, source="event_stream", observed_at=received_at, freshness_s=age_s, ref_id=ref_id),
            )
            state.add_fact(
                "purchase_completed",
                ProvenanceFact(value={"is_trial": is_trial, "plan_id": props.get("plan_id")}, source="event_stream", observed_at=received_at, freshness_s=age_s, ref_id=ref_id),
            )
            if not is_trial and state.active_plan and state.active_plan.goal_id in {"G04", "G05", "G06"}:
                state.active_plan.status = "SUCCEEDED"
                state.learnings.append(f"Goal {state.active_plan.goal_id} succeeded immediately on live purchase {eid}.")

        elif ev_type == "subscription_renewed":
            state.add_fact(
                "subscription_status",
                ProvenanceFact(value="active", source="event_stream", observed_at=received_at, freshness_s=age_s, ref_id=ref_id),
            )

        elif ev_type == "subscription_lapsed":
            state.add_fact(
                "subscription_status",
                ProvenanceFact(value="lapsed", source="event_stream", observed_at=received_at, freshness_s=age_s, ref_id=ref_id),
            )

        elif ev_type == "notification_opt_out":
            ch = str(props.get("channel") or "push").lower()
            if ch not in state.opted_out_channels:
                state.opted_out_channels.append(ch)
            field_key = "notifications_opt_in" if ch == "push" else "whatsapp_opt_in"
            state.add_fact(
                field_key,
                ProvenanceFact(value=False, source="event_stream", observed_at=received_at, freshness_s=age_s, ref_id=ref_id),
            )

        return True

    @classmethod
    def resolve_dialect(
        cls, state: ViewerState, now_iso: str
    ) -> Tuple[Optional[str], str, List[str], List[str], Dict[str, int]]:
        candidates = state.facts.get("dialect", [])
        if not candidates:
            return "d1", "default_fallback", [], [], {}

        now_dt = parse_iso(now_iso) or datetime(2026, 10, 1, tzinfo=UTC)
        latest_by_source: Dict[str, ProvenanceFact] = {}
        for c in candidates:
            latest_by_source[c.source] = c

        scored = []
        for src, fact in latest_by_source.items():
            obs_dt = parse_iso(fact.observed_at) or now_dt
            age = max(0, int((now_dt - obs_dt).total_seconds()))
            rank = cls.DIALECT_PRECEDENCE.get(src, 0)
            scored.append((rank, -age, fact, age))

        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        winner = scored[0][2]
        winner_age = scored[0][3]

        conflicts = []
        evidence_refs = [winner.ref_id]
        freshness_map = {winner.ref_id: winner_age}

        for _, _, cand, age in scored[1:]:
            if cand.value != winner.value:
                label = "profile" if cand.source == "profile_snapshot" else cand.source
                conflicts.append(f"{label}:{cand.value}")
                if cand.ref_id not in evidence_refs:
                    evidence_refs.append(cand.ref_id)
                    freshness_map[cand.ref_id] = age

        return winner.value, winner.source, conflicts, evidence_refs, freshness_map

    @classmethod
    def resolve_subscription_status(cls, state: ViewerState, now_iso: str) -> Tuple[str, str, int]:
        subs = state.facts.get("subscription_status", [])
        if not subs:
            return "never", "assumed:no_profile_row_never_paid", 0
        now_dt = parse_iso(now_iso) or datetime(2026, 10, 1, tzinfo=UTC)
        latest_event_sub = None
        for f in reversed(subs):
            if f.source == "event_stream":
                latest_event_sub = f
                break
        chosen = latest_event_sub if latest_event_sub else subs[-1]
        obs_dt = parse_iso(chosen.observed_at) or now_dt
        age = max(0, int((now_dt - obs_dt).total_seconds()))
        return chosen.value, chosen.ref_id, age

    @classmethod
    def resolve_signup_days_ago(cls, state: ViewerState, now_iso: str) -> Tuple[Optional[float], Optional[str], int]:
        facts = state.facts.get("signup_at", [])
        if not facts:
            return None, None, 0
        chosen = facts[-1]
        now_dt = parse_iso(now_iso) or datetime(2026, 10, 1, tzinfo=UTC)
        sig_dt = parse_iso(chosen.value)
        if not sig_dt:
            return None, None, 0
        days = (now_dt - sig_dt).total_seconds() / 86400.0
        age = max(0, int((now_dt - (parse_iso(chosen.observed_at) or now_dt)).total_seconds()))
        return days, chosen.ref_id, age

    @classmethod
    def has_consent(cls, state: ViewerState, channel: str) -> bool:
        ch = channel.lower()
        if ch in state.opted_out_channels:
            return False
        field_key = "notifications_opt_in" if ch == "push" else "whatsapp_opt_in"
        facts = state.facts.get(field_key, [])
        if not facts:
            return False
        return bool(facts[-1].value)

import time
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Set, Tuple
from .models import ViewerState, ActivePlan, DecisionOutput
from .state_resolver import StateResolver, parse_iso, format_iso, to_ist_day_and_hour, UTC
from .verifier import IndependentVerifier
from .llm_provider import PlannerModelProvider, CallBudgetTracker, BudgetExceededError, DependencyTimeoutError


class LifecycleAgentSystem:
    def __init__(
        self,
        catalogue: List[Dict[str, Any]],
        episodes: List[Dict[str, Any]],
        goals_config: Dict[str, Any],
        arms: Dict[str, str],
        model_provider: Optional[PlannerModelProvider] = None,
        peak_rpm_threshold: int = 6,
    ):
        self.states: Dict[str, ViewerState] = {}
        self.arms = arms
        self.catalogue_by_id: Dict[str, Dict[str, Any]] = {}
        self.titles_by_dialect: Dict[str, List[Dict[str, Any]]] = {}
        self.episodes_by_slug: Dict[str, List[str]] = {}
        self.injection_alerts: List[str] = []
        self.withdrawn_titles: Set[str] = set()

        for ep in episodes:
            slug = ep.get("content_slug")
            if slug and ep.get("episode_id"):
                self.episodes_by_slug.setdefault(slug, []).append(ep["episode_id"])

        for item in catalogue:
            clean_item, flagged = IndependentVerifier.sanitize_catalogue_item(item)
            clean_item["genres_list"] = [g for g in str(clean_item.get("genres", "")).split("|") if g]
            clean_item["is_premium_bool"] = str(clean_item.get("is_premium", "")).lower() == "true"
            clean_item["available_from_dt"] = parse_iso(clean_item.get("available_from"))
            clean_item["available_until_dt"] = parse_iso(clean_item.get("available_until")) if clean_item.get("available_until") else None
            tid = clean_item["title_id"]
            self.catalogue_by_id[tid] = clean_item
            self.titles_by_dialect.setdefault(clean_item.get("dialect", "d1"), []).append(clean_item)
            if flagged:
                self.injection_alerts.append(tid)

        raw_goals = goals_config.get("goals", []) if isinstance(goals_config, dict) else goals_config
        self.goals_version = goals_config.get("version", "2026-09-30") if isinstance(goals_config, dict) else "v1"
        self.goals_by_id: Dict[str, Dict[str, Any]] = {}
        for g in raw_goals:
            g_copy = dict(g)
            g_copy["rule_version"] = f"{g['goal_id']}-{self.goals_version}"
            self.goals_by_id[g["goal_id"]] = g_copy

        self.model_provider = model_provider or PlannerModelProvider(mode="mock")
        self.peak_rpm_threshold = peak_rpm_threshold
        self.recent_trigger_ts: deque = deque()
        self.peak_burst_count = 0
        self.decision_seq = 0
        self.action_seq = 0

    def get_or_create_viewer(self, user_id: str) -> ViewerState:
        if user_id not in self.states:
            arm = self.arms.get(user_id, "agent")
            self.states[user_id] = ViewerState(user_id=user_id, arm=arm)
        return self.states[user_id]

    def apply_control_event(self, ctrl: Dict[str, Any]) -> Dict[str, Any]:
        ctype = ctrl.get("type") or ctrl.get("event_type") or ""
        at_iso = ctrl.get("at") or ctrl.get("effective_at") or "2026-10-01T00:00:00.000Z"
        at_dt = parse_iso(at_iso) or datetime(2026, 10, 1, tzinfo=UTC)

        if ctype == "title_withdrawn":
            tid = ctrl["title_id"]
            self.withdrawn_titles.add(tid)
            if tid in self.catalogue_by_id:
                cur_until = self.catalogue_by_id[tid].get("available_until_dt")
                if cur_until is None or at_dt < cur_until:
                    self.catalogue_by_id[tid]["available_until_dt"] = at_dt
                    self.catalogue_by_id[tid]["available_until"] = at_iso
            return {"type": ctype, "title_id": tid, "at": at_iso}

        if ctype in {"goal_rule_corrected", "goal_rule_correction"}:
            gid = ctrl["goal_id"]
            new_succ = ctrl.get("new_success") or {}
            if gid in self.goals_by_id:
                g = self.goals_by_id[gid]
                if new_succ.get("event"):
                    g["success"]["event"] = new_succ["event"]
                if new_succ.get("condition"):
                    g["success"]["condition"] = new_succ["condition"]
                g["rule_version"] = f"{gid}-corrected@{at_iso[:16]}"

            affected_users = []
            for uid, st in self.states.items():
                if st.active_plan and st.active_plan.status == "ACTIVE" and st.active_plan.goal_id == gid:
                    st.active_plan.success_event = new_succ.get("event", st.active_plan.success_event)
                    st.active_plan.success_condition = new_succ.get("condition", st.active_plan.success_condition)
                    st.active_plan.rule_version = f"{gid}-corrected"
                    st.learnings.append(f"Applied mid-replay rule correction to open {gid} plan at {at_iso}.")
                    affected_users.append(uid)
            return {"type": ctype, "goal_id": gid, "affected_open_goals": len(affected_users), "at": at_iso}

        if ctype in {"dependency_unavailable", "dependency_degraded"}:
            dur_s = int(ctrl.get("duration_seconds") or ctrl.get("seconds") or (int(ctrl.get("duration_minutes", 5)) * 60))
            end_dt = parse_iso(ctrl.get("until")) if ctrl.get("until") else (at_dt + timedelta(seconds=dur_s))
            dep = str(ctrl.get("dependency") or ctrl.get("service") or "model_provider")
            self.model_provider.add_outage_window(at_dt, end_dt, dep, ctype)
            return {"type": ctype, "dependency": dep, "at": at_iso, "until": format_iso(end_dt)}

        return {"type": ctype, "at": at_iso}

    def _compute_viewer_derived_metrics(
        self, state: ViewerState, now_dt: datetime
    ) -> Tuple[List[str], Optional[float], int]:
        unfinished_slugs = []
        for slug, last_play_iso in state.slug_last_played_at.items():
            total_eps = len(self.episodes_by_slug.get(slug, []))
            done_eps = len(state.slug_completed_episodes.get(slug, []))
            if total_eps > 1 and done_eps < total_eps:
                lp_dt = parse_iso(last_play_iso)
                if lp_dt and (now_dt - lp_dt).total_seconds() >= 3 * 86400:
                    unfinished_slugs.append(slug)

        days_since_last_open = None
        if state.last_open_at:
            lo_dt = parse_iso(state.last_open_at)
            if lo_dt:
                days_since_last_open = (now_dt - lo_dt).total_seconds() / 86400.0

        cutoff_date = (now_dt - timedelta(days=14)).strftime("%Y-%m-%d")
        recent_14d_play_days = sum(1 for d in state.play_ist_days if d >= cutoff_date)

        return unfinished_slugs, days_since_last_open, recent_14d_play_days

    def handle_trigger(
        self, trigger: Dict[str, Any]
    ) -> Tuple[DecisionOutput, Optional[Dict[str, Any]]]:
        t0 = time.monotonic()
        budget = CallBudgetTracker()
        self.decision_seq += 1
        decision_id = f"D-{self.decision_seq:04d}"

        user_id = trigger["user_id"]
        event_id = trigger["event_id"]
        received_at = trigger.get("received_at") or "2026-10-01T00:00:00.000Z"
        deliver_at = trigger.get("deliver_at") or received_at
        now_dt = parse_iso(deliver_at) or datetime(2026, 10, 1, tzinfo=UTC)

        now_epoch = now_dt.timestamp()
        self.recent_trigger_ts.append(now_epoch)
        while self.recent_trigger_ts and (now_epoch - self.recent_trigger_ts[0]) > 60.0:
            self.recent_trigger_ts.popleft()
        is_peak_burst = len(self.recent_trigger_ts) > self.peak_rpm_threshold
        if is_peak_burst:
            self.peak_burst_count += 1

        state = self.get_or_create_viewer(user_id)
        offline_age_s, clock_skew_s = StateResolver.compute_event_timing(trigger)

        if event_id in state.seen_trigger_ids:
            ms = max(1, int((time.monotonic() - t0) * 1000))
            pub_iso = format_iso(now_dt + timedelta(milliseconds=ms))
            return (
                DecisionOutput(
                    decision_id=decision_id,
                    viewer_id=user_id,
                    user_id=user_id,
                    arm=state.arm,
                    trigger={"event": trigger.get("event", "app_open"), "event_id": event_id, "received_at": received_at, "event_age_s": offline_age_s},
                    goal=None,
                    action="NO_ACTION",
                    dialect={"used": None, "source": "none", "conflicts": []},
                    content=[],
                    confidence=1.0,
                    confidence_reason="Duplicate trigger event_id; idempotently skipped.",
                    no_action_reason="duplicate_trigger_idempotent_skip",
                    fallback_used=False,
                    model_calls=0,
                    tool_calls=0,
                    rule_version="idempotent",
                    evidence=[f"event:{event_id}"],
                    evidence_freshness_s={f"event:{event_id}": offline_age_s},
                    assumptions=[],
                    conflicts_resolved=[],
                    checks={"passed": ["idempotency"], "failed": []},
                    decided_in_ms=ms,
                    published_at=pub_iso,
                    status="ABSTAINED",
                ),
                None,
            )
        state.seen_trigger_ids.append(event_id)

        budget.record_tool_call()
        unfinished_slugs, days_since_last_open, recent_14d_play_days = self._compute_viewer_derived_metrics(state, now_dt)

        budget.record_tool_call()
        StateResolver.ingest_event(state, trigger, self.catalogue_by_id)

        budget.record_tool_call()
        dialect, d_source, d_conflicts, evidence_refs, freshness_map = StateResolver.resolve_dialect(state, deliver_at)
        sub_status, sub_ref, sub_age = StateResolver.resolve_subscription_status(state, deliver_at)
        if sub_ref and not sub_ref.startswith("assumed:"):
            if sub_ref not in evidence_refs:
                evidence_refs.append(sub_ref)
                freshness_map[sub_ref] = sub_age

        signup_days, sig_ref, sig_age = StateResolver.resolve_signup_days_ago(state, deliver_at)
        if sig_ref and sig_ref not in evidence_refs:
            evidence_refs.append(sig_ref)
            freshness_map[sig_ref] = sig_age

        if state.is_holdout:
            ms = max(1, int((time.monotonic() - t0) * 1000))
            pub_iso = format_iso(now_dt + timedelta(milliseconds=ms))
            return (
                DecisionOutput(
                    decision_id=decision_id,
                    viewer_id=user_id,
                    user_id=user_id,
                    arm=state.arm,
                    trigger={"event": trigger.get("event", "app_open"), "event_id": event_id, "received_at": received_at, "event_age_s": offline_age_s},
                    goal=None,
                    action="NO_ACTION",
                    dialect={"used": dialect, "source": d_source, "conflicts": d_conflicts},
                    content=[],
                    confidence=1.0,
                    confidence_reason="User assigned to randomized holdout arm; zero actions permitted.",
                    no_action_reason="holdout_arm",
                    fallback_used=False,
                    model_calls=0,
                    tool_calls=budget.tool_calls,
                    rule_version="holdout",
                    evidence=evidence_refs,
                    evidence_freshness_s=freshness_map,
                    assumptions=["Holdout users never receive interventions."],
                    conflicts_resolved=d_conflicts,
                    checks={"passed": ["holdout_zero_action"], "failed": []},
                    decided_in_ms=ms,
                    published_at=pub_iso,
                    status="HOLDOUT_NO_ACTION",
                ),
                None,
            )

        budget.record_tool_call()
        goal_priority = ["G04", "G03", "G01", "G08", "G02", "G07", "G06", "G09", "G12", "G05"]
        eligible_goals: List[Dict[str, Any]] = []

        if state.active_plan and state.active_plan.status == "ACTIVE":
            dl_dt = parse_iso(state.active_plan.deadline)
            cur_g = self.goals_by_id.get(state.active_plan.goal_id)
            if dl_dt and now_dt < dl_dt and cur_g:
                ok, _ = IndependentVerifier.check_goal_eligibility(
                    state, cur_g, sub_status, signup_days, unfinished_slugs, days_since_last_open, recent_14d_play_days
                )
                if ok:
                    eligible_goals.append(cur_g)

        for gid in goal_priority:
            if any(eg["goal_id"] == gid for eg in eligible_goals):
                continue
            g_def = self.goals_by_id.get(gid)
            if not g_def:
                continue
            ok, _ = IndependentVerifier.check_goal_eligibility(
                state, g_def, sub_status, signup_days, unfinished_slugs, days_since_last_open, recent_14d_play_days
            )
            if ok:
                eligible_goals.append(g_def)

        has_live_page = False
        if state.last_home_page_at and state.last_home_page_dialect == dialect:
            lp_dt = parse_iso(state.last_home_page_at)
            if lp_dt and (now_dt - lp_dt).total_seconds() < 120 * 60:
                has_live_page = True

        ist_day_str, _ = to_ist_day_and_hour(deliver_at)
        top_genre = max(state.genre_counts, key=state.genre_counts.get) if state.genre_counts else None

        user_summary = {
            "user_id": user_id,
            "dialect": dialect,
            "dialect_source": d_source,
            "dialect_conflicts": d_conflicts,
            "sub_status": sub_status,
            "play_count": state.play_count,
            "top_genre": top_genre,
            "unfinished_slugs": unfinished_slugs,
            "progress_slugs": list(state.slug_completed_episodes.keys()),
            "completed_slugs": [
                s for s, eps in state.slug_completed_episodes.items()
                if len(eps) >= len(self.episodes_by_slug.get(s, [1]))
            ],
            "has_live_home_page_same_dialect": has_live_page,
            "whatsapp_consent": StateResolver.has_consent(state, "whatsapp"),
            "messaged_today_ist": ist_day_str in state.messages_sent_ist_days,
        }

        dialect_titles = self.titles_by_dialect.get(dialect or "d1", [])
        fallback_used = False

        try:
            proposal = self.model_provider.propose_decision(
                budget=budget,
                trigger_event_id=event_id,
                now_dt=now_dt,
                user_summary=user_summary,
                eligible_goals=eligible_goals,
                dialect_titles=dialect_titles,
                peak_load_shedding=is_peak_burst,
            )
            fallback_used = bool(proposal.get("fallback_used", False))
        except (BudgetExceededError, DependencyTimeoutError) as exc:
            fallback_used = True
            proposal = {
                "goal_id": eligible_goals[0]["goal_id"] if eligible_goals else None,
                "action": "NO_ACTION",
                "action_payload": None,
                "content": [],
                "confidence": 0.0,
                "confidence_reason": f"Safe fallback to NO_ACTION on timeout/budget fault: {exc}",
                "assumptions": ["Degraded safely to NO_ACTION within 60s SLA."],
            }

        chosen_goal_def = self.goals_by_id.get(proposal["goal_id"]) if proposal.get("goal_id") else None
        rule_ver = chosen_goal_def.get("rule_version", "v1") if chosen_goal_def else "none"
        if chosen_goal_def:
            rule_ref = f"goal_rule:{rule_ver}"
            if rule_ref not in evidence_refs:
                evidence_refs.append(rule_ref)
                freshness_map[rule_ref] = 0

        for cid in proposal.get("content", []):
            cref = f"catalogue:{cid.replace('title:', '')}"
            if cref not in evidence_refs:
                evidence_refs.append(cref)
                freshness_map[cref] = 0

        ms = max(1, int((time.monotonic() - t0) * 1000))
        pub_iso = format_iso(now_dt + timedelta(milliseconds=ms))

        passed_checks, failed_checks = IndependentVerifier.verify_action(
            state=state,
            goal_def=chosen_goal_def,
            action_type=proposal["action"],
            action_payload=proposal.get("action_payload"),
            resolved_dialect=dialect or "d1",
            sub_status=sub_status,
            signup_days=signup_days,
            unfinished_slugs=unfinished_slugs,
            days_since_last_open=days_since_last_open,
            recent_14d_play_days=recent_14d_play_days,
            evidence_refs=evidence_refs,
            catalogue_by_id=self.catalogue_by_id,
            published_at_iso=pub_iso,
            trigger_age_s=offline_age_s,
        )

        final_action = proposal["action"]
        final_status = "PUBLISHED"
        no_action_reason = None
        published_action_record: Optional[Dict[str, Any]] = None

        if failed_checks:
            final_action = "NO_ACTION"
            final_status = "ABSTAINED"
            no_action_reason = f"verifier_blocked:{','.join(failed_checks)}"
        elif fallback_used and final_action == "NO_ACTION":
            final_status = "FALLBACK_NO_ACTION"
            no_action_reason = proposal["confidence_reason"]
        elif final_action == "NO_ACTION":
            final_status = "ABSTAINED"
            no_action_reason = proposal["confidence_reason"]

        goal_output = None
        if chosen_goal_def and not failed_checks:
            win_h = int(chosen_goal_def["success"]["window_hours"])
            deadline_iso = format_iso(now_dt + timedelta(hours=win_h))
            goal_output = {
                "id": chosen_goal_def["goal_id"],
                "name": chosen_goal_def.get("name"),
                "deadline": deadline_iso,
                "success_signal": chosen_goal_def["success"]["event"],
            }
            if final_action != "NO_ACTION":
                state.active_plan = ActivePlan(
                    goal_id=chosen_goal_def["goal_id"],
                    goal_name=chosen_goal_def.get("name", ""),
                    rule_version=rule_ver,
                    decision_id=decision_id,
                    created_at=pub_iso,
                    deadline=deadline_iso,
                    success_event=chosen_goal_def["success"]["event"],
                    success_condition=chosen_goal_def["success"].get("condition", ""),
                    action_taken=final_action,
                    target_content=proposal.get("content", []),
                    target_slug=unfinished_slugs[0] if (chosen_goal_def["goal_id"] == "G07" and unfinished_slugs) else None,
                )

        if final_status == "PUBLISHED" and final_action != "NO_ACTION" and proposal.get("action_payload"):
            self.action_seq += 1
            act_id = f"A-{self.action_seq:04d}"
            payload = dict(proposal["action_payload"])
            published_action_record = {
                "action_id": act_id,
                "decision_id": decision_id,
                "user_id": user_id,
                "trigger_event_id": event_id,
                "published_at": pub_iso,
                **payload,
            }
            if final_action == "HOME_PAGE":
                state.last_home_page_at = pub_iso
                state.last_home_page_dialect = dialect
            elif final_action in {"PUSH", "WHATSAPP"}:
                send_day, _ = to_ist_day_and_hour(payload.get("send_at") or pub_iso)
                state.messages_sent_ist_days.append(send_day)

        decision = DecisionOutput(
            decision_id=decision_id,
            viewer_id=user_id,
            user_id=user_id,
            arm=state.arm,
            trigger={"event": trigger.get("event", "app_open"), "event_id": event_id, "received_at": received_at, "event_age_s": offline_age_s},
            goal=goal_output,
            action=final_action,
            dialect={"used": dialect, "source": d_source, "conflicts": d_conflicts},
            content=proposal.get("content", []) if final_action != "NO_ACTION" else [],
            confidence=proposal["confidence"],
            confidence_reason=proposal["confidence_reason"],
            no_action_reason=no_action_reason,
            fallback_used=fallback_used,
            model_calls=budget.model_calls,
            tool_calls=budget.tool_calls,
            rule_version=rule_ver,
            evidence=evidence_refs,
            evidence_freshness_s=freshness_map,
            assumptions=proposal.get("assumptions", []),
            conflicts_resolved=d_conflicts,
            checks={"passed": passed_checks, "failed": failed_checks},
            decided_in_ms=ms,
            published_at=pub_iso,
            status=final_status,
        )
        state.decision_history.append(decision_id)
        return decision, published_action_record

    def judge_viewer_goal(
        self, state: ViewerState, user_events: List[Dict[str, Any]], horizon_end_iso: str
    ) -> Optional[Dict[str, Any]]:
        plan = state.active_plan
        if not plan:
            return None

        start_dt = parse_iso(plan.created_at)
        dl_dt = parse_iso(plan.deadline)
        end_dt = parse_iso(horizon_end_iso)
        start_ist_day, _ = to_ist_day_and_hour(plan.created_at)

        window_events = []
        for ev in user_events:
            r_dt = parse_iso(ev.get("received_at"))
            if r_dt and start_dt <= r_dt <= dl_dt:
                window_events.append(ev)

        succeeded = False
        attr_event_id = None
        gid = plan.goal_id
        target_ev = plan.success_event

        if gid == "G03":
            for ev in window_events:
                if ev.get("event") == target_ev:
                    ev_day, _ = to_ist_day_and_hour(ev["received_at"])
                    if ev_day > start_ist_day:
                        succeeded = True
                        attr_event_id = f"event:{ev['event_id']}"
                        break

        elif gid in {"G04", "G05"}:
            for ev in window_events:
                if ev.get("event") == "purchase_completed" and not (ev.get("properties") or {}).get("is_trial", False):
                    succeeded = True
                    attr_event_id = f"event:{ev['event_id']}"
                    break

        elif gid == "G06":
            for ev in window_events:
                if ev.get("event") == "purchase_completed":
                    succeeded = True
                    attr_event_id = f"event:{ev['event_id']}"
                    break

        elif gid == "G07":
            for ev in window_events:
                if ev.get("event") == "play_started":
                    tid = (ev.get("properties") or {}).get("title_id")
                    c_item = self.catalogue_by_id.get(tid) if tid else None
                    if not plan.target_slug or (c_item and c_item.get("content_slug") == plan.target_slug):
                        succeeded = True
                        attr_event_id = f"event:{ev['event_id']}"
                        break

        elif gid == "G09":
            play_days = {to_ist_day_and_hour(ev["received_at"])[0] for ev in window_events if ev.get("event") == "play_started"}
            if len(play_days) >= 3:
                succeeded = True
                attr_event_id = "events:3_distinct_ist_play_days"

        else:
            for ev in window_events:
                if ev.get("event") == target_ev:
                    succeeded = True
                    attr_event_id = f"event:{ev['event_id']}"
                    break

        if succeeded or plan.status == "SUCCEEDED":
            plan.status = "SUCCEEDED"
            state.learnings.append(f"Goal {gid} ({plan.rule_version}) SUCCEEDED via {attr_event_id or 'live_event'}.")
            return {
                "user_id": state.user_id,
                "viewer_id": state.user_id,
                "arm": state.arm,
                "decision_id": plan.decision_id,
                "goal_id": gid,
                "rule_version": plan.rule_version,
                "deadline": plan.deadline,
                "outcome": "SUCCEEDED",
                "attributed_evidence": attr_event_id or "event:purchase_completed",
            }

        if end_dt and end_dt >= dl_dt:
            plan.status = "FAILED"
            state.learnings.append(f"Goal {gid} ({plan.rule_version}) FAILED: window closed at {plan.deadline} without {target_ev}.")
            return {
                "user_id": state.user_id,
                "viewer_id": state.user_id,
                "arm": state.arm,
                "decision_id": plan.decision_id,
                "goal_id": gid,
                "rule_version": plan.rule_version,
                "deadline": plan.deadline,
                "outcome": "FAILED",
                "attributed_evidence": None,
            }

        plan.status = "INCONCLUSIVE"
        return {
            "user_id": state.user_id,
            "viewer_id": state.user_id,
            "arm": state.arm,
            "decision_id": plan.decision_id,
            "goal_id": gid,
            "rule_version": plan.rule_version,
            "deadline": plan.deadline,
            "outcome": "INCONCLUSIVE",
            "attributed_evidence": None,
        }

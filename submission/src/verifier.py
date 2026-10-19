import re
from typing import Dict, Any, List, Tuple, Optional, Set
from .models import ViewerState
from .state_resolver import parse_iso, to_ist_day_and_hour, StateResolver


class IndependentVerifier:
    """
    Independent deterministic safety & policy verifier.
    Executes separately from the decision step and can block any proposed action.
    """

    VALID_EVENTS_IN_STREAM = {
        "signup_completed",
        "login_success",
        "app_open",
        "app_dialect_changed",
        "home_viewed",
        "title_viewed",
        "search",
        "play_started",
        "play_progress",
        "episode_completed",
        "paywall_viewed",
        "purchase_completed",
        "subscription_renewed",
        "subscription_lapsed",
        "message_delivered",
        "message_clicked",
        "notification_opt_out",
    }

    ALLOWED_ROW_TYPES = {
        "next_episode",
        "because_you_watched",
        "popular_in_dialect",
        "new_releases",
        "genre",
    }

    FORBIDDEN_PUSH_PHRASES = [
        "last chance",
        "hurry",
        "only today",
        "free forever",
        "guaranteed",
    ]

    APPROVED_WA_TEMPLATES = {
        "wa_new_episode_v2",
        "wa_recommend_title_v1",
        "wa_trial_ending_v1",
    }

    INJECTION_PATTERNS = [
        r"ignore (all )?(previous|prior) (instructions|rules)",
        r"message every (viewer|user)",
        r"send (a )?(push|whatsapp)",
        r"system (instruction|override|prompt)",
        r"assistant:",
        r"do not follow", r"every viewer", r"every user", r"all viewers", r"all users", r"agent", r"prompt", r"instruction", r"push notification", r"whatsapp",
        r"when (an|the) (ai|agent|assistant|model)",
        r"instruct(ion|s)? for (the )?(ai|agent|model)",
        r"always (recommend|promote|select|choose|include)",
        r"must (recommend|promote|select|message)",
        r"every (viewer|user) should",
    ]

    @classmethod
    def sanitize_catalogue_item(cls, item: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        desc = str(item.get("description", ""))
        for pattern in cls.INJECTION_PATTERNS:
            if re.search(pattern, desc, flags=re.IGNORECASE):
                clean = dict(item)
                clean["description"] = "[REDACTED_PROMPT_INJECTION_IN_CATALOGUE]"
                clean["_injection_flagged"] = True
                return clean, True
        return item, False

    @classmethod
    def validate_goal_definition(cls, goal_def: Dict[str, Any]) -> Tuple[bool, str]:
        succ = goal_def.get("success") or {}
        ev = succ.get("event")
        if not ev:
            return False, "unmeasurable_goal_null_success_event"
        if ev not in cls.VALID_EVENTS_IN_STREAM:
            return False, f"unobservable_success_event_not_in_stream:{ev}"
        if not succ.get("window_hours"):
            return False, "missing_goal_window_hours"
        return True, "ok"

    @classmethod
    def check_goal_eligibility(
        cls,
        state: ViewerState,
        goal_def: Dict[str, Any],
        sub_status: str,
        signup_days: Optional[float],
        unfinished_slugs: List[str],
        days_since_last_open: Optional[float],
        recent_14d_play_days: int,
    ) -> Tuple[bool, str]:
        ok, reason = cls.validate_goal_definition(goal_def)
        if not ok:
            return False, reason

        gid = goal_def["goal_id"]
        elig = goal_def.get("eligibility") or {}

        if "subscription_status" in elig:
            allowed_subs = elig["subscription_status"]
            if isinstance(allowed_subs, str):
                allowed_subs = [allowed_subs]
            if sub_status not in allowed_subs:
                return False, f"eligibility_subscription_mismatch:expected_{allowed_subs}_got_{sub_status}"

        if "signed_up_within_days" in elig:
            max_days = float(elig["signed_up_within_days"])
            if signup_days is None or signup_days < 0 or signup_days > max_days:
                return False, f"eligibility_signup_window_failed:days={signup_days}"

        req_no_event = elig.get("requires_no_prior_event")
        if req_no_event == "play_started" and state.play_count > 0:
            return False, "eligibility_already_has_play_started"
        if req_no_event == "episode_completed" and state.complete_count > 0:
            return False, "eligibility_already_has_episode_completed"

        if gid == "G07":
            if not unfinished_slugs:
                return False, "eligibility_no_unfinished_series_idle_3d"
        elif gid == "G09":
            if sub_status != "active" or recent_14d_play_days > 1:
                return False, "eligibility_weekly_habit_requires_active_and_le_1_play_day"
        elif gid == "G12":
            if days_since_last_open is None or days_since_last_open < 14.0:
                return False, "eligibility_absence_less_than_14d"

        return True, "eligible"

    @classmethod
    def verify_action(
        cls,
        state: ViewerState,
        goal_def: Optional[Dict[str, Any]],
        action_type: str,
        action_payload: Optional[Dict[str, Any]],
        resolved_dialect: str,
        sub_status: str,
        signup_days: Optional[float],
        unfinished_slugs: List[str],
        days_since_last_open: Optional[float],
        recent_14d_play_days: int,
        evidence_refs: List[str],
        catalogue_by_id: Dict[str, Dict[str, Any]],
        published_at_iso: str,
        trigger_age_s: int,
    ) -> Tuple[List[str], List[str]]:
        passed: List[str] = []
        failed: List[str] = []

        if state.is_holdout:
            if action_type != "NO_ACTION":
                failed.append("holdout_arm_violation")
            else:
                passed.append("holdout_zero_action")
            return passed, failed

        if trigger_age_s > 600:
            failed.append("stale_offline_buffered_trigger")
        else:
            passed.append("trigger_freshness")

        if action_type == "NO_ACTION" or goal_def is None:
            passed.append("abstention_safe")
            return passed, failed

        elig_ok, elig_reason = cls.check_goal_eligibility(
            state, goal_def, sub_status, signup_days, unfinished_slugs, days_since_last_open, recent_14d_play_days
        )
        if not elig_ok:
            failed.append(elig_reason)
        elif not any(r.startswith("event:") or r.startswith("profile:") for r in evidence_refs):
            failed.append("missing_supporting_evidence_refs")
        else:
            passed.append("eligibility")

        pub_dt = parse_iso(published_at_iso)

        if action_type == "HOME_PAGE":
            if not action_payload:
                failed.append("missing_home_page_payload")
                return passed, failed

            rows = action_payload.get("rows") or []
            if len(rows) == 0 or len(rows) > 6:
                failed.append("invalid_row_count")

            seen_slugs: Set[str] = set()
            dialect_ok = True
            avail_ok = True
            row_bounds_ok = True
            row_type_ok = True

            for r in rows:
                rtype = r.get("row_type")
                if rtype not in cls.ALLOWED_ROW_TYPES:
                    row_type_ok = False
                tids = r.get("title_ids") or []
                if len(tids) < 3 or len(tids) > 10:
                    row_bounds_ok = False
                for tid in tids:
                    c_item = catalogue_by_id.get(tid)
                    if not c_item or c_item.get("dialect") != resolved_dialect:
                        dialect_ok = False
                        continue
                    af = c_item.get("available_from_dt")
                    au = c_item.get("available_until_dt")
                    if (af and pub_dt < af) or (au and pub_dt >= au):
                        avail_ok = False
                    slug = c_item.get("content_slug")
                    if slug in seen_slugs:
                        failed.append("duplicate_content_slug_on_page")
                    if slug:
                        seen_slugs.add(slug)

            hero_id = action_payload.get("hero_title_id")
            if hero_id:
                h_item = catalogue_by_id.get(hero_id)
                if not h_item or h_item.get("dialect") != resolved_dialect:
                    dialect_ok = False
                else:
                    af = h_item.get("available_from_dt")
                    au = h_item.get("available_until_dt")
                    if (af and pub_dt < af) or (au and pub_dt >= au):
                        avail_ok = False

            if not row_type_ok:
                failed.append("invalid_row_type")
            if not row_bounds_ok:
                failed.append("row_below_min_3_or_above_10")
            if not dialect_ok:
                failed.append("dialect_mismatch")
            if not avail_ok:
                failed.append("title_unavailable_or_withdrawn")

            if not failed:
                passed.extend(["dialect", "availability", "content_slug_dedup", "row_bounds", "frequency_cap"])

        elif action_type in {"PUSH", "WHATSAPP", "IN_APP_MESSAGE"}:
            if not action_payload:
                failed.append("missing_message_payload")
                return passed, failed

            send_at_iso = action_payload.get("send_at") or published_at_iso
            send_dt = parse_iso(send_at_iso)
            ist_day_str, ist_hour = to_ist_day_and_hour(send_at_iso)

            tid = action_payload.get("title_id")
            c_item = catalogue_by_id.get(tid) if tid else None
            if not c_item or c_item.get("dialect") != resolved_dialect:
                failed.append("message_title_dialect_mismatch")
            else:
                af = c_item.get("available_from_dt")
                au = c_item.get("available_until_dt")
                if (af and send_dt < af) or (au and send_dt >= au):
                    failed.append("message_title_unavailable_or_withdrawn")
                else:
                    passed.append("dialect")

            if action_type in {"PUSH", "WHATSAPP"}:
                if not StateResolver.has_consent(state, action_type):
                    failed.append("consent_missing_or_opted_out")
                else:
                    passed.append("consent")

                if ist_hour >= 22 or ist_hour < 8:
                    failed.append("quiet_hours_ist_violation")
                else:
                    passed.append("quiet_hours_ist")

                if ist_day_str in state.messages_sent_ist_days:
                    failed.append("frequency_cap_exceeded")
                else:
                    passed.append("frequency_cap")

            if action_type == "WHATSAPP":
                tpl_id = action_payload.get("template_id")
                if tpl_id not in cls.APPROVED_WA_TEMPLATES:
                    failed.append("unapproved_whatsapp_template")
                else:
                    passed.append("whatsapp_template")

            if action_type == "PUSH":
                txt = action_payload.get("text") or {}
                title_s = str(txt.get("title", ""))
                body_s = str(txt.get("body", ""))
                if len(title_s) > 40 or len(body_s) > 120:
                    failed.append("push_copy_length_exceeded")
                low = (title_s + " " + body_s).lower()
                for ph in cls.FORBIDDEN_PUSH_PHRASES:
                    if ph in low:
                        failed.append(f"forbidden_phrase:{ph}")

        return passed, failed

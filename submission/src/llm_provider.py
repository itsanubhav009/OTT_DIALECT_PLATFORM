import json
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Set, Tuple
from .state_resolver import parse_iso, format_iso, to_ist_day_and_hour, IST, UTC


class BudgetExceededError(Exception):
    pass


class DependencyTimeoutError(Exception):
    pass


class CallBudgetTracker:
    def __init__(self, max_model_calls: int = 8, max_tool_calls: int = 20, max_latency_ms: int = 55000):
        self.max_model_calls = max_model_calls
        self.max_tool_calls = max_tool_calls
        self.max_latency_ms = max_latency_ms
        self.model_calls = 0
        self.tool_calls = 0
        self.start_monotonic = time.monotonic()

    def record_tool_call(self) -> None:
        self.tool_calls += 1
        if self.tool_calls > self.max_tool_calls:
            raise BudgetExceededError(f"Tool call budget ({self.max_tool_calls}) exceeded.")

    def record_model_call(self) -> None:
        self.model_calls += 1
        if self.model_calls > self.max_model_calls:
            raise BudgetExceededError(f"Model call budget ({self.max_model_calls}) exceeded.")


class PlannerModelProvider:
    """
    Mock / Replay model provider that reproduces all faults in replay/model_faults.jsonl
    (unsupported_claim, malformed_output, timeout) and operational outages from
    replay/control_events.jsonl without requiring a personal API key.
    """

    DIALECT_NAMES = {
        "d1": "Haryanvi",
        "d2": "Rajasthani",
        "d3": "Bhojpuri",
        "d4": "Gujarati",
        "d5": "Marathi",
        "d6": "Odia",
    }

    def __init__(self, mode: str = "mock"):
        self.mode = mode
        self.faults_by_trigger: Dict[str, Dict[str, Any]] = {}
        self.outage_windows: List[Tuple[datetime, datetime, str, str]] = []

    def load_faults(self, faults: List[Dict[str, Any]]) -> None:
        for f in faults:
            tid = f.get("trigger_event_id")
            if tid:
                self.faults_by_trigger[tid] = f

    def add_outage_window(self, start_dt: datetime, end_dt: datetime, dep_name: str, kind: str) -> None:
        self.outage_windows.append((start_dt, end_dt, dep_name, kind))

    def check_active_outage(self, now_dt: datetime) -> Optional[str]:
        for s_dt, e_dt, dep, kind in self.outage_windows:
            if s_dt <= now_dt < e_dt:
                return f"{dep}_{kind}"
        return None

    def propose_decision(
        self,
        budget: CallBudgetTracker,
        trigger_event_id: str,
        now_dt: datetime,
        user_summary: Dict[str, Any],
        eligible_goals: List[Dict[str, Any]],
        dialect_titles: List[Dict[str, Any]],
        peak_load_shedding: bool = False,
    ) -> Dict[str, Any]:
        fault_spec = self.faults_by_trigger.get(trigger_event_id)
        if fault_spec:
            ftype = fault_spec.get("fault") or fault_spec.get("fault_type")
            if ftype == "timeout":
                budget.record_model_call()
                raise DependencyTimeoutError(f"Model timeout fault ({fault_spec.get('seconds', 45)}s) on trigger {trigger_event_id}")

            if ftype in {"malformed_output", "broken_json"}:
                budget.record_model_call()
                raw_out = str(fault_spec.get("model_output", "{broken"))
                try:
                    json.loads(raw_out)
                except json.JSONDecodeError:
                    budget.record_model_call()
                    plan = self._build_optimal_plan(now_dt, user_summary, eligible_goals, dialect_titles)
                    plan["fallback_used"] = True
                    plan["confidence_reason"] += " [Recovered from malformed_output model fault via deterministic fallback]."
                    return plan

            if ftype in {"unsupported_claim", "unsupported_eligibility_claim"}:
                budget.record_model_call()
                mo = fault_spec.get("model_output") or {}
                claimed_gid = mo.get("goal_id", "G04")
                return {
                    "goal_id": claimed_gid,
                    "action": "HOME_PAGE",
                    "action_payload": None,
                    "content": [],
                    "confidence": float(mo.get("confidence", 0.97)),
                    "confidence_reason": str(mo.get("reason", "Unsupported model claim.")),
                    "assumptions": ["Model claimed eligibility without backing event or profile evidence."],
                    "fallback_used": False,
                    "unsupported_claim_injected": True,
                }

        outage = self.check_active_outage(now_dt)
        if outage:
            plan = self._build_optimal_plan(now_dt, user_summary, eligible_goals, dialect_titles)
            plan["fallback_used"] = True
            plan["confidence_reason"] += f" [Used rule fallback during {outage} window]."
            return plan

        if not peak_load_shedding:
            budget.record_model_call()

        return self._build_optimal_plan(now_dt, user_summary, eligible_goals, dialect_titles)

    def _build_optimal_plan(
        self,
        now_dt: datetime,
        user_summary: Dict[str, Any],
        eligible_goals: List[Dict[str, Any]],
        dialect_titles: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not eligible_goals:
            return {
                "goal_id": None,
                "action": "NO_ACTION",
                "action_payload": None,
                "content": [],
                "confidence": 0.92,
                "confidence_reason": "No measurable eligible goal matches current viewer state.",
                "assumptions": ["Abstaining when no valid goal criteria are met."],
                "fallback_used": False,
            }

        chosen_goal = eligible_goals[0]
        gid = chosen_goal["goal_id"]
        dialect = user_summary["dialect"]
        top_genre = user_summary.get("top_genre")
        unfinished_slugs: Set[str] = set(user_summary.get("unfinished_slugs", []))
        progress_slugs: Set[str] = set(user_summary.get("progress_slugs", []))
        completed_slugs: Set[str] = set(user_summary.get("completed_slugs", []))

        avail = []
        for t in dialect_titles:
            af = t.get("available_from_dt")
            au = t.get("available_until_dt")
            if (af and now_dt < af) or (au and now_dt >= au):
                continue
            avail.append(t)

        by_slug: Dict[str, Dict[str, Any]] = {}
        for t in avail:
            slug = t["content_slug"]
            if slug not in by_slug:
                by_slug[slug] = t

        unique_titles = list(by_slug.values())
        if len(unique_titles) < 3:
            return {
                "goal_id": gid,
                "action": "NO_ACTION",
                "action_payload": None,
                "content": [],
                "confidence": 0.85,
                "confidence_reason": f"Fewer than 3 distinct available titles in dialect {dialect}; abstaining per row rule.",
                "assumptions": ["Home page rows require at least 3 valid distinct titles."],
                "fallback_used": False,
            }

        if user_summary.get("has_live_home_page_same_dialect"):
            if (
                gid in {"G04", "G06", "G07"}
                and user_summary.get("whatsapp_consent")
                and not user_summary.get("messaged_today_ist")
            ):
                best_t = next((t for t in unique_titles if t.get("is_premium_bool")), unique_titles[0])
                send_dt = self._next_allowed_ist_send_time(now_dt)
                if gid == "G04":
                    tpl_id = "wa_trial_ending_v1"
                    tpl_vars = {"plan_price": "₹149"}
                else:
                    tpl_id = "wa_recommend_title_v1"
                    tpl_vars = {
                        "title_name": best_t.get("title", best_t["title_id"]),
                        "dialect_name": self.DIALECT_NAMES.get(dialect, dialect),
                    }
                return {
                    "goal_id": gid,
                    "action": "WHATSAPP",
                    "action_payload": {
                        "type": "whatsapp",
                        "send_at": format_iso(send_dt),
                        "template_id": tpl_id,
                        "variables": tpl_vars,
                        "title_id": best_t["title_id"],
                    },
                    "content": [f"title:{best_t['title_id']}"],
                    "confidence": 0.76,
                    "confidence_reason": f"Home page already live; sending proportionate WhatsApp nudge ({tpl_id}) for {gid}.",
                    "assumptions": ["User has active WhatsApp opt-in and 0 messages sent on target IST day."],
                    "fallback_used": False,
                }

            return {
                "goal_id": gid,
                "action": "NO_ACTION",
                "action_payload": None,
                "content": [],
                "confidence": 0.90,
                "confidence_reason": "Personalised home page already live within 120-minute TTL; avoiding redundant update.",
                "assumptions": ["Existing personalised home page remains active for 120 minutes."],
                "fallback_used": False,
            }

        def score_title(t: Dict[str, Any]) -> Tuple[int, int, int, int, str]:
            slug = t["content_slug"]
            in_prog = 1 if (slug in progress_slugs or slug in unfinished_slugs) else 0
            not_done = 0 if slug in completed_slugs else 1
            genre_match = 1 if (top_genre and top_genre in t.get("genres_list", [])) else 0
            prem = 1 if t.get("is_premium_bool") else 0
            return (in_prog, not_done, genre_match, prem, t.get("release_date", ""))

        sorted_titles = sorted(unique_titles, key=score_title, reverse=True)

        rows: List[Dict[str, Any]] = []
        used_slugs: Set[str] = set()
        all_selected_tids: List[str] = []

        row_types_sequence = [
            "next_episode" if (progress_slugs or unfinished_slugs) else "popular_in_dialect",
            "new_releases",
            "because_you_watched" if user_summary.get("play_count", 0) > 0 else "genre",
            "genre",
            "popular_in_dialect",
        ]

        for rtype in row_types_sequence:
            row_tids = []
            for t in sorted_titles:
                if t["content_slug"] in used_slugs:
                    continue
                used_slugs.add(t["content_slug"])
                row_tids.append(t["title_id"])
                all_selected_tids.append(f"title:{t['title_id']}")
                if len(row_tids) == 3:
                    break
            if len(row_tids) == 3:
                rows.append({"row_type": rtype, "title_ids": row_tids})
            if len(rows) == 5:
                break

        if not rows:
            return {
                "goal_id": gid,
                "action": "NO_ACTION",
                "action_payload": None,
                "content": [],
                "confidence": 0.80,
                "confidence_reason": "Could not assemble a valid 3-title row after slug deduplication.",
                "assumptions": [],
                "fallback_used": False,
            }

        hero_tid = rows[0]["title_ids"][0]
        has_conflicts = len(user_summary.get("dialect_conflicts", [])) > 0
        conf = 0.64 if has_conflicts else 0.86
        reason = (
            f"Published {len(rows)}-row HOME_PAGE for {gid} ({chosen_goal.get('name')}) in dialect {dialect} via {user_summary['dialect_source']}"
            + (f" (resolved conflicts: {user_summary['dialect_conflicts']})." if has_conflicts else ".")
        )

        return {
            "goal_id": gid,
            "action": "HOME_PAGE",
            "action_payload": {
                "type": "home_page",
                "dialect": dialect,
                "hero_title_id": hero_tid,
                "rows": rows,
            },
            "content": all_selected_tids[:9],
            "confidence": conf,
            "confidence_reason": reason,
            "assumptions": [
                f"User will view dialect {dialect} home page within 120m TTL; content_slugs deduplicated across {len(rows)} rows."
            ],
            "fallback_used": False,
        }

    @staticmethod
    def _next_allowed_ist_send_time(now_dt: datetime) -> datetime:
        ist_dt = now_dt.astimezone(IST)
        if ist_dt.hour >= 22:
            next_day = (ist_dt + timedelta(days=1)).replace(hour=9, minute=15, second=0, microsecond=0)
            return next_day.astimezone(UTC)
        if ist_dt.hour < 8:
            same_day = ist_dt.replace(hour=9, minute=15, second=0, microsecond=0)
            return same_day.astimezone(UTC)
        return now_dt + timedelta(seconds=10)

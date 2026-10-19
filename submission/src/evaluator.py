import csv
import math
from typing import Dict, Any, List, Tuple
from .models import ViewerState


class CausalImpactEvaluator:
    @staticmethod
    def _ci_95(p1: float, n1: int, p2: float, n2: int) -> Tuple[float, float]:
        if n1 <= 0 or n2 <= 0:
            return (0.0, 0.0)
        se = math.sqrt((p1 * (1.0 - p1) / n1) + (p2 * (1.0 - p2) / n2))
        diff = p1 - p2
        return (round((diff - 1.96 * se) * 100.0, 2), round((diff + 1.96 * se) * 100.0, 2))

    @classmethod
    def analyze_pilot(cls, pilot_outcomes_csv: str) -> Dict[str, Any]:
        with open(pilot_outcomes_csv, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        served = [r for r in rows if r["served"] == "true"]
        unserved = [r for r in rows if r["served"] != "true"]
        treatment = [r for r in rows if r["arm"] == "treatment"]
        holdout = [r for r in rows if r["arm"] == "holdout"]

        s_rate = sum(1 for r in served if r["succeeded"] == "true") / (len(served) or 1)
        u_rate = sum(1 for r in unserved if r["succeeded"] == "true") / (len(unserved) or 1)
        t_rate = sum(1 for r in treatment if r["succeeded"] == "true") / (len(treatment) or 1)
        h_rate = sum(1 for r in holdout if r["succeeded"] == "true") / (len(holdout) or 1)
        ci_low, ci_high = cls._ci_95(t_rate, len(treatment), h_rate, len(holdout))

        return {
            "served_n": len(served),
            "unserved_n": len(unserved),
            "served_success_rate": round(s_rate, 4),
            "unserved_success_rate": round(u_rate, 4),
            "flawed_served_vs_other_uplift_pp": round((s_rate - u_rate) * 100.0, 2),
            "treatment_n": len(treatment),
            "holdout_n": len(holdout),
            "itt_treatment_success_rate": round(t_rate, 4),
            "itt_holdout_success_rate": round(h_rate, 4),
            "true_pilot_itt_uplift_pp": round((t_rate - h_rate) * 100.0, 2),
            "true_pilot_itt_95_ci_pp": [ci_low, ci_high],
        }

    @classmethod
    def analyze_replay(
        cls,
        triggered_user_ids: List[str],
        arms: Dict[str, str],
        states: Dict[str, ViewerState],
        outcome_events_by_user: Dict[str, List[Dict[str, Any]]],
        judged_outcomes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        agent_users = [u for u in triggered_user_ids if arms.get(u, "agent") == "agent"]
        holdout_users = [u for u in triggered_user_ids if arms.get(u) == "holdout"]

        def user_metrics(uid_list: List[str]) -> Dict[str, Any]:
            n = len(uid_list) or 1
            played = 0
            completed = 0
            purchased = 0
            for u in uid_list:
                evs = outcome_events_by_user.get(u, [])
                if any(e.get("event") == "play_started" for e in evs):
                    played += 1
                if any(e.get("event") == "episode_completed" for e in evs):
                    completed += 1
                if any(e.get("event") == "purchase_completed" and not (e.get("properties") or {}).get("is_trial", False) for e in evs):
                    purchased += 1
            return {
                "n": len(uid_list),
                "any_play_rate": round(played / n, 4),
                "episode_complete_rate": round(completed / n, 4),
                "paid_purchase_rate": round(purchased / n, 4),
            }

        m_agent = user_metrics(agent_users)
        m_hold = user_metrics(holdout_users)

        served_users = [
            u for u in agent_users
            if u in states and states[u].active_plan and states[u].active_plan.action_taken != "NO_ACTION"
        ]
        unserved_all = [u for u in triggered_user_ids if u not in set(served_users)]
        m_served = user_metrics(served_users)
        m_unserved = user_metrics(unserved_all)

        play_ci = cls._ci_95(m_agent["any_play_rate"], m_agent["n"], m_hold["any_play_rate"], m_hold["n"])
        comp_ci = cls._ci_95(m_agent["episode_complete_rate"], m_agent["n"], m_hold["episode_complete_rate"], m_hold["n"])
        paid_ci = cls._ci_95(m_agent["paid_purchase_rate"], m_agent["n"], m_hold["paid_purchase_rate"], m_hold["n"])

        by_goal: Dict[str, Dict[str, int]] = {}
        for jo in judged_outcomes:
            gid = jo["goal_id"]
            g_stat = by_goal.setdefault(gid, {"committed": 0, "succeeded": 0, "failed": 0, "inconclusive": 0})
            g_stat["committed"] += 1
            g_stat[jo["outcome"].lower()] += 1

        return {
            "agent_arm": m_agent,
            "holdout_arm": m_hold,
            "itt_uplift": {
                "any_play_uplift_pp": round((m_agent["any_play_rate"] - m_hold["any_play_rate"]) * 100.0, 2),
                "any_play_95_ci_pp": list(play_ci),
                "episode_complete_uplift_pp": round((m_agent["episode_complete_rate"] - m_hold["episode_complete_rate"]) * 100.0, 2),
                "episode_complete_95_ci_pp": list(comp_ci),
                "paid_purchase_uplift_pp": round((m_agent["paid_purchase_rate"] - m_hold["paid_purchase_rate"]) * 100.0, 2),
                "paid_purchase_95_ci_pp": list(paid_ci),
            },
            "naive_served_vs_unserved": {
                "served_n": m_served["n"],
                "unserved_n": m_unserved["n"],
                "served_episode_complete_rate": m_served["episode_complete_rate"],
                "unserved_episode_complete_rate": m_unserved["episode_complete_rate"],
                "biased_episode_complete_uplift_pp": round((m_served["episode_complete_rate"] - m_unserved["episode_complete_rate"]) * 100.0, 2),
            },
            "goal_breakdown": dict(sorted(by_goal.items())),
        }

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any


@dataclass
class ProvenanceFact:
    value: Any
    source: str          # "app_setting", "event_stream", "campaign_link", "profile_snapshot"
    observed_at: str     # Server received_at (UTC ISO-8601)
    device_time: Optional[str] = None
    freshness_s: int = 0
    clock_skew_s: int = 0
    ref_id: str = ""


@dataclass
class ActivePlan:
    goal_id: str
    goal_name: str
    rule_version: str
    decision_id: str
    created_at: str
    deadline: str
    success_event: str
    success_condition: str
    action_taken: str
    target_content: List[str]
    target_slug: Optional[str] = None
    status: str = "ACTIVE"  # ACTIVE, SUCCEEDED, FAILED, INCONCLUSIVE


@dataclass
class ViewerState:
    user_id: str
    arm: str = "agent"   # "agent" or "holdout"
    facts: Dict[str, List[ProvenanceFact]] = field(default_factory=dict)
    seen_event_ids: List[str] = field(default_factory=list)
    seen_trigger_ids: List[str] = field(default_factory=list)
    play_count: int = 0
    complete_count: int = 0
    play_ist_days: List[str] = field(default_factory=list)
    open_ist_days: List[str] = field(default_factory=list)
    last_open_at: Optional[str] = None
    genre_counts: Dict[str, int] = field(default_factory=dict)
    slug_last_played_at: Dict[str, str] = field(default_factory=dict)
    slug_completed_episodes: Dict[str, List[str]] = field(default_factory=dict)
    opted_out_channels: List[str] = field(default_factory=list)
    active_plan: Optional[ActivePlan] = None
    last_home_page_at: Optional[str] = None
    last_home_page_dialect: Optional[str] = None
    messages_sent_ist_days: List[str] = field(default_factory=list)
    decision_history: List[str] = field(default_factory=list)
    learnings: List[str] = field(default_factory=list)

    @property
    def is_holdout(self) -> bool:
        return self.arm == "holdout"

    def add_fact(self, key: str, fact: ProvenanceFact) -> None:
        if key not in self.facts:
            self.facts[key] = []
        # Keep most recent fact per source so provenance & conflicts stay compact and auditable
        self.facts[key] = [f for f in self.facts[key] if f.source != fact.source] + [fact]

    def to_serializable(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "viewer_id": self.user_id,
            "arm": self.arm,
            "facts": {k: [asdict(f) for f in v] for k, v in self.facts.items()},
            "play_count": self.play_count,
            "complete_count": self.complete_count,
            "last_open_at": self.last_open_at,
            "genre_counts": self.genre_counts,
            "opted_out_channels": self.opted_out_channels,
            "active_plan": asdict(self.active_plan) if self.active_plan else None,
            "last_home_page_at": self.last_home_page_at,
            "last_home_page_dialect": self.last_home_page_dialect,
            "messages_sent_ist_days": self.messages_sent_ist_days,
            "decision_history": self.decision_history,
            "learnings": self.learnings,
            "seen_triggers_count": len(self.seen_trigger_ids),
            "seen_events_count": len(self.seen_event_ids),
        }


@dataclass
class DecisionOutput:
    decision_id: str
    viewer_id: str
    user_id: str
    arm: str
    trigger: Dict[str, Any]
    goal: Optional[Dict[str, Any]]
    action: str  # "HOME_PAGE", "PUSH", "WHATSAPP", "IN_APP_MESSAGE", "NO_ACTION"
    dialect: Dict[str, Any]
    content: List[str]
    confidence: float
    confidence_reason: str
    no_action_reason: Optional[str]
    fallback_used: bool
    model_calls: int
    tool_calls: int
    rule_version: str
    evidence: List[str]
    evidence_freshness_s: Dict[str, int]
    assumptions: List[str]
    conflicts_resolved: List[str]
    checks: Dict[str, List[str]]
    decided_in_ms: int
    published_at: str
    status: str  # "PUBLISHED", "ABSTAINED", "FALLBACK_NO_ACTION", "HOLDOUT_NO_ACTION"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

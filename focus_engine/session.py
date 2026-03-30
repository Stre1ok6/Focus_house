from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field

from .config import AnalyzerConfig, DEFAULT_CONFIG
from .goal_profiles import build_goal_profile
from .models import FrameAnalysis, GoalProfile
from .pipeline import FocusAnalyzer
from .scoring import build_summary, finalize_frame_scores


@dataclass
class SessionState:
    session_id: str
    goal: str
    profile: GoalProfile
    created_at: float
    updated_at: float
    duration_seconds: int
    status: str = "running"
    completed_at: float | None = None
    frames: list[FrameAnalysis] = field(default_factory=list)


class StreamingSessionManager:
    def __init__(self, analyzer: FocusAnalyzer, config: AnalyzerConfig = DEFAULT_CONFIG) -> None:
        self.analyzer = analyzer
        self.config = config
        self.sessions: dict[str, SessionState] = {}

    def _prune(self) -> None:
        now = time.time()
        expired = [
            session_id
            for session_id, state in self.sessions.items()
            if now - state.updated_at > self.config.session_ttl_seconds
        ]
        for session_id in expired:
            self.sessions.pop(session_id, None)

    def _validate_settings(
        self,
        duration_minutes: int | None = None,
        capture_interval_seconds: int | None = None,
    ) -> int:
        _ = capture_interval_seconds
        duration = duration_minutes or self.config.session_default_duration_minutes
        if duration < self.config.session_min_duration_minutes or duration > self.config.session_max_duration_minutes:
            raise ValueError("invalid_duration")
        return duration * 60

    def _session_meta(self, state: SessionState) -> dict[str, object]:
        now = state.completed_at or time.time()
        elapsed_seconds = max(0, int(now - state.created_at))
        remaining_seconds = max(0, state.duration_seconds - elapsed_seconds)
        progress_percent = round(min(100.0, (elapsed_seconds / max(state.duration_seconds, 1)) * 100), 1)
        return {
            "session_id": state.session_id,
            "status": state.status,
            "created_at": state.created_at,
            "updated_at": state.updated_at,
            "completed_at": state.completed_at,
            "duration_seconds": state.duration_seconds,
            "capture_mode": "continuous",
            "capture_interval_seconds": None,
            "expected_frames": None,
            "captured_frames": len(state.frames),
            "elapsed_seconds": elapsed_seconds,
            "remaining_seconds": remaining_seconds,
            "progress_percent": progress_percent,
        }

    def _finalize_live_frame(self, frames: list[FrameAnalysis], frame: FrameAnalysis) -> FrameAnalysis:
        working_items = copy.deepcopy(frames)
        working_items.append(copy.deepcopy(frame))
        finalize_frame_scores(working_items)
        return working_items[-1]

    def _build_payload(self, state: SessionState) -> dict:
        items = list(state.frames)
        processing_ms = sum(max(item.processing_ms, 0) for item in items)
        summary = build_summary(state.profile, items, processing_ms=processing_ms)
        state.updated_at = time.time()
        return {
            "session": self._session_meta(state),
            "goal": state.goal,
            "goal_profile": state.profile.to_dict(),
            "summary": summary,
            "items": [item.to_dict() for item in items],
        }

    def start(
        self,
        goal: str,
        duration_minutes: int | None = None,
        capture_interval_seconds: int | None = None,
    ) -> dict:
        self._prune()
        profile = build_goal_profile(goal)
        duration_seconds = self._validate_settings(duration_minutes, capture_interval_seconds)
        session_id = uuid.uuid4().hex[:12]
        now = time.time()
        state = SessionState(
            session_id=session_id,
            goal=goal,
            profile=profile,
            created_at=now,
            updated_at=now,
            duration_seconds=duration_seconds,
        )
        self.sessions[session_id] = state
        return self._build_payload(state)

    def add_frame(self, session_id: str, filename: str, file_bytes: bytes) -> dict:
        self._prune()
        if session_id not in self.sessions:
            raise KeyError("session_not_found")

        state = self.sessions[session_id]
        if state.status != "running":
            raise RuntimeError("session_not_running")

        frame = self.analyzer.analyze_frame(state.profile, filename, file_bytes)
        frame.index = len(state.frames)
        finalized_frame = self._finalize_live_frame(state.frames, frame)
        finalized_frame.index = len(state.frames)
        state.frames.append(finalized_frame)
        state.updated_at = time.time()

        if state.created_at + state.duration_seconds <= state.updated_at:
            state.status = "completed"
            state.completed_at = state.updated_at

        return self._build_payload(state)

    def snapshot(self, session_id: str) -> dict:
        self._prune()
        if session_id not in self.sessions:
            raise KeyError("session_not_found")

        return self._build_payload(self.sessions[session_id])

    def complete(self, session_id: str) -> dict:
        self._prune()
        if session_id not in self.sessions:
            raise KeyError("session_not_found")

        state = self.sessions[session_id]
        if state.status != "completed":
            state.status = "completed"
            state.completed_at = time.time()
            state.updated_at = state.completed_at
        return self._build_payload(state)

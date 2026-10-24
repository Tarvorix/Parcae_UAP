from dataclasses import dataclass, field
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import numpy as np
import numpy.typing as npt
from scipy.optimize import linear_sum_assignment

from ..models import DetectionCandidate


@dataclass
class TrackState:
    id: str
    state_vector: npt.NDArray[np.float64]
    covariance: npt.NDArray[np.float64]
    last_update: float
    last_observed: float
    logical_id: str = ""
    parent_id: str | None = None
    fragment_number: int = 0
    first_observed: float = 0
    observation_count: int = 0
    end_reason: str | None = None
    state: str = "tentative"
    observations: list[dict[str, Any]] = field(default_factory=list)
    fitted: list[dict[str, float]] = field(default_factory=list)
    associations: list[dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "track_id": self.id,
            "state": self.state,
            "logical_track_id": self.logical_id,
            "parent_fragment_id": self.parent_id,
            "fragment_number": self.fragment_number,
            "logical_start_seconds": self.first_observed,
            "cumulative_observed_frames": self.observation_count,
            "end_reason": self.end_reason,
            "observations": self.observations,
            "fitted_path": self.fitted,
            "associations": self.associations,
            "units": "pixels and media seconds",
            "unavailable": ["distance", "physical_speed", "sky_coordinates"],
        }


class Tracker:
    version = "kalman-hungarian-2"

    def __init__(
        self,
        max_gap: float = 0.75,
        gate_px: float = 30,
        confirm_hits: int = 4,
        max_history_points: int = 512,
    ) -> None:
        if max_gap <= 0 or gate_px <= 0 or confirm_hits < 2 or max_history_points < confirm_hits:
            raise ValueError("invalid tracker settings")
        self.max_gap, self.gate_px, self.confirm_hits = max_gap, gate_px, confirm_hits
        self.max_history_points = max_history_points
        self.tracks: list[TrackState] = []
        self.last_time: float | None = None

    def update(self, time_seconds: float, candidates: list[DetectionCandidate]) -> list[TrackState]:
        if self.last_time is not None and time_seconds < self.last_time:
            raise ValueError("tracker cannot join backward timeline; start a new session")
        self.last_time = time_seconds
        active = []
        for track in list(self.tracks):
            if track.state == "ended":
                continue
            if time_seconds - track.last_observed > self.max_gap:
                track.state = "ended"
                track.end_reason = "observation_gap"
                continue
            if len(track.observations) >= self.max_history_points:
                previous = track
                previous.state = "ended"
                previous.end_reason = "history_chunk_limit"
                track = TrackState(
                    id=uuid5(NAMESPACE_URL, previous.id + ":continuation").hex,
                    state_vector=previous.state_vector.copy(),
                    covariance=previous.covariance.copy(),
                    last_update=previous.last_update,
                    last_observed=previous.last_observed,
                    logical_id=previous.logical_id,
                    parent_id=previous.id,
                    fragment_number=previous.fragment_number + 1,
                    first_observed=previous.first_observed,
                    observation_count=previous.observation_count,
                    state="confirmed"
                    if previous.observation_count >= self.confirm_hits
                    else "tentative",
                )
                self.tracks.append(track)
            dt = time_seconds - track.last_update
            transition = np.array(
                [[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float64
            )
            track.state_vector = transition @ track.state_vector
            track.covariance = transition @ track.covariance @ transition.T + np.eye(4) * max(
                0.01, dt
            )
            track.last_update = time_seconds
            active.append(track)
        costs = np.full((len(active), len(candidates)), 1e9, dtype=np.float64)
        for ti, track in enumerate(active):
            for ci, candidate in enumerate(candidates):
                delta = np.array([candidate.x_px, candidate.y_px]) - track.state_vector[:2]
                distance = float(np.linalg.norm(delta))
                if distance <= self.gate_px:
                    costs[ti, ci] = distance
        pairs = list(zip(*linear_sum_assignment(costs), strict=True)) if costs.size else []
        matched_tracks = set()
        matched_candidates = set()
        for raw_ti, raw_ci in pairs:
            ti, ci = int(raw_ti), int(raw_ci)
            if costs[ti, ci] >= 1e9:
                continue
            track = active[ti]
            candidate = candidates[ci]
            observation = np.array([candidate.x_px, candidate.y_px])
            h = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float64)
            innovation = h @ track.covariance @ h.T + np.eye(2) * 0.5
            gain = track.covariance @ h.T @ np.linalg.inv(innovation)
            track.state_vector += gain @ (observation - h @ track.state_vector)
            track.covariance = (np.eye(4) - gain @ h) @ track.covariance
            ambiguous = int(np.sum(np.abs(costs[:, ci] - costs[ti, ci]) < 1)) > 1
            track.associations.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "method": "gated_hungarian",
                    "distance_px": float(costs[ti, ci]),
                    "ambiguous": ambiguous,
                    "reacquired": track.state == "temporarily_lost",
                }
            )
            self._observe(track, candidate, time_seconds)
            matched_tracks.add(ti)
            matched_candidates.add(ci)
        for ti, track in enumerate(active):
            if ti not in matched_tracks:
                track.state = "temporarily_lost"
        for ci, candidate in enumerate(candidates):
            if ci in matched_candidates:
                continue
            track = TrackState(
                uuid5(NAMESPACE_URL, candidate.candidate_id).hex,
                np.array([candidate.x_px, candidate.y_px, 0, 0], dtype=np.float64),
                np.diag([1.0, 1.0, 1000.0, 1000.0]),
                time_seconds,
                time_seconds,
            )
            track.logical_id = track.id
            track.first_observed = time_seconds
            self._observe(track, candidate, time_seconds)
            self.tracks.append(track)
        return self.tracks

    def drain_ended(self) -> list[TrackState]:
        """Caller persists every fragment before releasing it from working memory."""
        ended = [track for track in self.tracks if track.state == "ended"]
        self.tracks = [track for track in self.tracks if track.state != "ended"]
        return ended

    def parameters(self) -> dict[str, float | int]:
        return {
            "max_gap_seconds": self.max_gap,
            "gate_px": self.gate_px,
            "confirm_hits": self.confirm_hits,
            "max_history_points": self.max_history_points,
        }

    def _observe(
        self, track: TrackState, candidate: DetectionCandidate, time_seconds: float
    ) -> None:
        track.observations.append(
            {
                "candidate_id": candidate.candidate_id,
                "frame_id": candidate.frame_id,
                "time_seconds": time_seconds,
                "x_px": candidate.x_px,
                "y_px": candidate.y_px,
                "area_px": candidate.area_px,
            }
        )
        track.fitted.append(
            {
                "time_seconds": time_seconds,
                "x_px": float(track.state_vector[0]),
                "y_px": float(track.state_vector[1]),
            }
        )
        track.last_observed = time_seconds
        track.observation_count += 1
        track.state = "confirmed" if track.observation_count >= self.confirm_hits else "tentative"

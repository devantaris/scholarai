"""Scholar Pulse: Thread-Safe In-Memory State Store.

Provides thread-safe storage for students, roadmaps, events, concepts, interventions,
and the simulation clock, supporting instant deterministic resets for test isolation.
"""

from datetime import datetime, timedelta, timezone
import threading
from typing import Any, Callable, Optional, Union

from app.models import (
    CURATED_CONCEPTS,
    ContentConcept,
    Event,
    Intervention,
    RoadmapItem,
    Student,
)

BASE_SIM_NOW = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)


class Store:
    """Thread-safe in-memory store for Scholar Pulse."""

    def __init__(self, auto_seed: bool = True) -> None:
        self._lock = threading.RLock()
        self._sim_now: datetime = BASE_SIM_NOW
        self._students: dict[str, Student] = {}
        self._roadmaps: dict[str, list[RoadmapItem]] = {}
        self._events: dict[str, list[Event]] = {}
        self._concepts: dict[str, ContentConcept] = {}
        self._interventions: dict[str, list[Intervention]] = {}

        if auto_seed:
            self.reset()

    # -------------------------------------------------------------------------
    # Clock Operations
    # -------------------------------------------------------------------------
    def get_sim_now(self) -> datetime:
        with self._lock:
            return self._sim_now

    def set_sim_now(self, now: datetime) -> None:
        with self._lock:
            self._sim_now = now

    def advance_sim_now(self, days: int = 0, hours: int = 0, minutes: int = 0) -> datetime:
        with self._lock:
            self._sim_now += timedelta(days=days, hours=hours, minutes=minutes)
            return self._sim_now

    # -------------------------------------------------------------------------
    # Student Operations
    # -------------------------------------------------------------------------
    def get_student(self, student_id: str) -> Optional[Student]:
        with self._lock:
            student = self._students.get(student_id)
            return student.model_copy(deep=True) if student else None

    def get_students(self) -> list[Student]:
        with self._lock:
            return [s.model_copy(deep=True) for s in self._students.values()]

    def save_student(self, student: Student) -> None:
        with self._lock:
            self._students[student.id] = student.model_copy(deep=True)

    # -------------------------------------------------------------------------
    # Roadmap Operations
    # -------------------------------------------------------------------------
    def get_roadmap(self, student_id: str) -> list[RoadmapItem]:
        with self._lock:
            items = self._roadmaps.get(student_id, [])
            return [item.model_copy(deep=True) for item in items]

    def update_roadmap(self, student_id: str, roadmap: list[RoadmapItem]) -> None:
        with self._lock:
            self._roadmaps[student_id] = [item.model_copy(deep=True) for item in roadmap]

    def update_roadmap_item(self, student_id: str, item: RoadmapItem) -> None:
        with self._lock:
            items = self._roadmaps.setdefault(student_id, [])
            for idx, existing in enumerate(items):
                if existing.id == item.id:
                    items[idx] = item.model_copy(deep=True)
                    return
            items.append(item.model_copy(deep=True))

    # -------------------------------------------------------------------------
    # Event Operations
    # -------------------------------------------------------------------------
    def get_events(
        self,
        student_id: str,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit_days: Optional[int] = None,
    ) -> list[Event]:
        with self._lock:
            events = self._events.get(student_id, [])
            filtered = events

            cutoff = since
            if limit_days is not None:
                days_cutoff = self._sim_now - timedelta(days=limit_days)
                cutoff = max(cutoff, days_cutoff) if cutoff else days_cutoff

            if cutoff:
                filtered = [e for e in filtered if e.ts >= cutoff]
            if until:
                filtered = [e for e in filtered if e.ts <= until]

            return [e.model_copy(deep=True) for e in filtered]

    def add_event(self, event: Event) -> None:
        with self._lock:
            ev_copy = event.model_copy(deep=True)
            student_events = self._events.setdefault(event.student_id, [])
            student_events.append(ev_copy)
            student_events.sort(key=lambda e: e.ts)

    def add_events(self, events: list[Event]) -> None:
        with self._lock:
            for ev in events:
                self.add_event(ev)

    # -------------------------------------------------------------------------
    # Intervention Operations
    # -------------------------------------------------------------------------
    def get_interventions(self, student_id: Optional[str] = None) -> list[Intervention]:
        with self._lock:
            if student_id:
                intvs = self._interventions.get(student_id, [])
                return [i.model_copy(deep=True) for i in sorted(intvs, key=lambda x: x.ts, reverse=True)]
            
            all_intvs: list[Intervention] = []
            for student_intvs in self._interventions.values():
                all_intvs.extend(student_intvs)
            return [i.model_copy(deep=True) for i in sorted(all_intvs, key=lambda x: x.ts, reverse=True)]

    def get_all_interventions(self) -> list[Intervention]:
        return self.get_interventions(student_id=None)

    def get_intervention(self, intervention_id: str) -> Optional[Intervention]:
        with self._lock:
            for student_intvs in self._interventions.values():
                for intv in student_intvs:
                    if intv.id == intervention_id:
                        return intv.model_copy(deep=True)
            return None

    def save_intervention(self, intervention: Intervention) -> None:
        with self._lock:
            intv_copy = intervention.model_copy(deep=True)
            student_intvs = self._interventions.setdefault(intervention.student_id, [])
            student_intvs.append(intv_copy)
            student_intvs.sort(key=lambda x: x.ts, reverse=True)

    def update_intervention(self, intervention: Intervention) -> None:
        with self._lock:
            student_intvs = self._interventions.get(intervention.student_id, [])
            for idx, existing in enumerate(student_intvs):
                if existing.id == intervention.id:
                    student_intvs[idx] = intervention.model_copy(deep=True)
                    return
            self.save_intervention(intervention)

    # -------------------------------------------------------------------------
    # Concept Operations
    # -------------------------------------------------------------------------
    def get_concept(self, concept_id: str) -> Optional[ContentConcept]:
        with self._lock:
            concept = self._concepts.get(concept_id)
            return concept.model_copy(deep=True) if concept else None

    def get_concepts(self) -> list[ContentConcept]:
        with self._lock:
            return [c.model_copy(deep=True) for c in self._concepts.values()]

    def save_concept(self, concept: ContentConcept) -> None:
        with self._lock:
            self._concepts[concept.id] = concept.model_copy(deep=True)

    # -------------------------------------------------------------------------
    # Reset / Reseed Hook
    # -------------------------------------------------------------------------
    def reset(self) -> None:
        with self._lock:
            self._sim_now = BASE_SIM_NOW
            self._students.clear()
            self._roadmaps.clear()
            self._events.clear()
            self._concepts.clear()
            self._interventions.clear()

            from app.seed import seed_store
            seed_store(self)


# Global singleton instance expected by tests and API
store: Store = Store(auto_seed=True)

# Functional aliases
get_student = store.get_student
get_students = store.get_students
save_student = store.save_student
get_roadmap = store.get_roadmap
update_roadmap = store.update_roadmap
update_roadmap_item = store.update_roadmap_item
get_events = store.get_events
add_event = store.add_event
add_events = store.add_events
get_interventions = store.get_interventions
get_all_interventions = store.get_all_interventions
get_intervention = store.get_intervention
save_intervention = store.save_intervention
update_intervention = store.update_intervention
get_concept = store.get_concept
get_concepts = store.get_concepts
get_sim_now = store.get_sim_now
set_sim_now = store.set_sim_now
advance_sim_now = store.advance_sim_now
reset_store = store.reset

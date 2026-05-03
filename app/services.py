import os
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import cohere
from sqlalchemy.orm import Session

from . import models


class CohereService:
    def __init__(self) -> None:
        api_key = os.getenv("COHERE_API_KEY")
        self.enabled = bool(api_key)
        self.client = cohere.Client(api_key) if api_key else None

    def explain_plan(self, tasks_count: int, warnings: list[str]) -> str:
        if self.enabled and self.client:
            try:
                prompt = (
                    "Explain a student plan briefly in 1-2 sentences. "
                    f"Task count: {tasks_count}. Warnings: {warnings}."
                )
                response = self.client.generate(
                    model="command", prompt=prompt, max_tokens=80
                )
                return response.generations[0].text.strip()
            except Exception:
                pass
        if warnings:
            return (
                "This schedule prioritizes urgent and difficult tasks first, but your workload "
                "is high. Reduce daily load or adjust deadlines where possible."
            )
        return (
            "Closer deadlines are filled first; easier tasks with later deadlines are spread so you "
            "can focus on what is due soonest, then shift to the next block when slack allows."
        )


class SchedulingEngine:
    """Builds plans that respect deadlines, max 24h/day, and balanced daily chunks."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def generate_schedule(self) -> tuple[list[dict[str, Any]], list[str]]:
        profile = self._get_or_create_profile()

        tasks = (
            self.db.query(models.Task)
            .filter(
                models.Task.completed.is_(False), models.Task.remaining_duration > 0
            )
            .order_by(models.Task.deadline.asc(), models.Task.difficulty.desc())
            .all()
        )

        self.db.query(models.ScheduleItem).filter(
            models.ScheduleItem.status == "pending"
        ).delete()
        self.db.commit()

        warnings: list[str] = []

        max_deadline = date.today()
        if tasks:
            max_deadline = max(t.deadline for t in tasks)
        horizon_end = max_deadline + timedelta(days=21)

        remaining_by_date: dict[date, float] = defaultdict(float)
        d = date.today()
        while d <= horizon_end:
            remaining_by_date[d] = self._daily_capacity_for(d, profile)
            d += timedelta(days=1)

        # If a task is marked missed today, do not schedule it again for today.
        task_start_date: dict[int, date] = defaultdict(lambda: date.today())
        missed_today_task_ids = (
            self.db.query(models.ScheduleItem.task_id)
            .filter(
                models.ScheduleItem.status == "missed",
                models.ScheduleItem.date == date.today(),
            )
            .all()
        )
        for (task_id,) in missed_today_task_ids:
            task_start_date[int(task_id)] = date.today() + timedelta(days=1)

        for task in tasks:
            hours_left = float(task.remaining_duration)
            day = task_start_date.get(task.id, date.today())
            guard = 0
            while hours_left > 0.001:
                guard += 1
                if guard > 4000:
                    warnings.append("Scheduler stopped early due to iteration limits.")
                    break
                if day > task.deadline:
                    warnings.append(
                        f"Not enough calendar time before deadline for «{task.title}». "
                        "Increase daily capacity or extend the deadline."
                    )
                    break
                if day > horizon_end:
                    warnings.append(
                        f"Horizon overflow for «{task.title}». Increase daily capacity or move the deadline."
                    )
                    break
                cap_left = remaining_by_date.get(day, 0.0)
                if cap_left < 0.001:
                    day += timedelta(days=1)
                    continue

                span = max((task.deadline - day).days + 1, 1)
                ideal = hours_left / span
                ideal = max(ideal, min(hours_left, cap_left))
                chunk = min(hours_left, cap_left, max(ideal, 0.25))
                if chunk < 0.25:
                    chunk = min(hours_left, cap_left)
                if chunk < 0.001:
                    day += timedelta(days=1)
                    continue

                chunk = round(chunk * 4) / 4
                chunk = min(chunk, hours_left, cap_left)
                if chunk < 0.001:
                    day += timedelta(days=1)
                    continue

                self.db.add(
                    models.ScheduleItem(
                        task_id=task.id, date=day, duration=chunk, status="pending"
                    )
                )
                remaining_by_date[day] -= chunk
                hours_left -= chunk

                if remaining_by_date[day] < 0.001:
                    day += timedelta(days=1)

        self.db.commit()

        range_start = date.today() - timedelta(days=7)
        range_end = horizon_end
        schedule = self._serialize_schedule(range_start, range_end)
        return schedule, warnings

    def serialize_existing_plan(self) -> tuple[list[dict[str, Any]], list[str]]:
        """Return calendar view without rebuilding pending chunks (keeps layout after Done)."""
        tasks = self.db.query(models.Task).all()
        max_deadline = max((t.deadline for t in tasks), default=date.today())
        range_start = date.today() - timedelta(days=7)
        range_end = max_deadline + timedelta(days=21)
        schedule = self._serialize_schedule(range_start, range_end)
        return schedule, []

    def _serialize_schedule(
        self, range_start: date, range_end: date
    ) -> list[dict[str, Any]]:
        # Cleanup: remove schedule rows that reference deleted tasks (historical/orphan rows).
        # This prevents showing "Task #123" entries for tasks that no longer exist.
        orphan_q = self.db.query(models.ScheduleItem).filter(
            ~models.ScheduleItem.task_id.in_(self.db.query(models.Task.id))
        )
        if orphan_q.first() is not None:
            orphan_q.delete(synchronize_session=False)
            self.db.commit()

        rows = (
            self.db.query(models.ScheduleItem)
            .filter(
                models.ScheduleItem.date >= range_start,
                models.ScheduleItem.date <= range_end,
            )
            .order_by(models.ScheduleItem.date.asc(), models.ScheduleItem.id.asc())
            .all()
        )
        task_ids = {row.task_id for row in rows}
        task_map: dict[int, models.Task] = {}
        if task_ids:
            for t in (
                self.db.query(models.Task).filter(models.Task.id.in_(task_ids)).all()
            ):
                task_map[t.id] = t
        row_ids = [row.id for row in rows]
        undoable_ids: set[int] = set()
        if row_ids:
            undoable_ids = {
                sid
                for (sid,) in self.db.query(models.ActionLog.schedule_id)
                .filter(models.ActionLog.schedule_id.in_(row_ids))
                .all()
                if sid is not None
            }

        grouped: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            t = task_map.get(row.task_id)
            title = t.title if t else f"Task #{row.task_id}"
            tr = (
                float(t.remaining_duration)
                if t
                else 0.0
            )
            grouped[row.date].append(
                {
                    "id": row.id,
                    "task": title,
                    "assigned_duration": row.duration,
                    "completed_duration": float(
                        getattr(row, "completed_duration", 0.0) or 0.0
                    ),
                    "task_id": row.task_id,
                    "task_remaining_duration": tr,
                    "status": row.status,
                    "undoable": row.id in undoable_ids,
                }
            )

        return [
            {"date": day, "tasks": day_tasks}
            for day, day_tasks in sorted(grouped.items(), key=lambda x: x[0])
        ]

    def adapt_from_progress(self) -> None:
        return

    def _get_or_create_profile(self) -> models.UserProfile:
        profile = self.db.query(models.UserProfile).first()
        if not profile:
            profile = models.UserProfile()
            self.db.add(profile)
            self.db.commit()
            self.db.refresh(profile)
        return profile

    def _daily_capacity_for(self, day: date, profile: models.UserProfile) -> float:
        """
        Constraint-aware daily capacity.

        - If at least one explicit "available" slot exists for that day, ONLY those windows count (sum of durations).
        - If no availability is entered for that day, fall back to the default daily capacity (profile.daily_capacity).
        - "blocked" slots are no longer supported and are ignored for capacity calculation.
        """
        max_cap = min(float(getattr(profile, "max_capacity", 24.0) or 24.0), 24.0)
        base_fallback = min(float(profile.daily_capacity), max_cap)

        slots = (
            self.db.query(models.AvailabilitySlot)
            .filter(
                models.AvailabilitySlot.user_id == 1,
                models.AvailabilitySlot.date == day,
            )
            .all()
        )
        if not slots:
            raw = base_fallback
        else:
            def parse_hhmm(s: str) -> float:
                hh, mm = s.split(":")
                return int(hh) + int(mm) / 60.0

            available_hours = 0.0
            has_explicit_available = any(
                getattr(s, "type", None) == "available" for s in slots
            )

            for s in slots:
                try:
                    start = parse_hhmm(s.start_time)
                    end = parse_hhmm(s.end_time)
                except Exception:
                    continue
                if end <= start:
                    continue
                dur = max(0.0, min(end, 24.0) - max(start, 0.0))
                if getattr(s, "type", None) == "available":
                    available_hours += dur

            if has_explicit_available:
                raw = max(0.0, available_hours)
            else:
                # No entered availability → use the default.
                raw = base_fallback

        completed_hours = (
            self.db.query(models.ScheduleItem)
            .filter(
                models.ScheduleItem.date == day,
                models.ScheduleItem.status == "completed",
            )
            .with_entities(models.ScheduleItem.completed_duration)
            .all()
        )
        consumed = sum(float(h[0] or 0.0) for h in completed_hours)
        remaining = max(0.0, raw - consumed)
        return max(0.0, min(remaining, max_cap))

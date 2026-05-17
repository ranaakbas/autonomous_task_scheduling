"""
services.py — Gemini Function-Calling Agent tabanlı zamanlama motoru.

CohereService kaldırıldı.  Yerine GeminiAgentService geldi:
  • explain_plan()   → LLM ile plan açıklaması üretir (geriye dönük uyumluluk)

UserSchedulingEngine.generate_schedule() artık deterministik değil:
  • Gemini'ye context (görevler, kapasite, müsaitlik) JSON olarak iletilir.
  • Agent function-calling döngüsüyle şu tool'ları çağırır:
      - get_tasks            : aktif görevlerin listesi
      - get_daily_capacity   : belirli bir tarih için kalan kapasiteyi döndürür
      - create_schedule_item : DB'ye UserScheduleItem kaydeder
      - add_warning          : warnings listesine mesaj ekler
      - finish_scheduling    : döngüyü sonlandırır
  • LLM çağrıları başarısız olursa orijinal deterministik fallback devreye girer.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from google import genai
from google.genai import types
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Gemini Agent Service
# ─────────────────────────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-2.5-flash"

# Agent'ın kullanacağı tool tanımları (Gemini Function Declaration formatı)
_TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="get_tasks",
        description=(
            "Returns the list of all active (incomplete) tasks for the user. "
            "Each task includes: id, title, deadline (ISO date), remaining_duration (hours), "
            "difficulty (1-5), work_style ('intensive'|'balanced'|'deadline_focused'), "
            "daily_target_hours (only for balanced style)."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
    types.FunctionDeclaration(
        name="get_daily_capacity",
        description=(
            "Returns the remaining schedulable hours for a given date, "
            "considering availability slots and already-completed schedule items. "
            "Returns a float between 0 and 24."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "date": types.Schema(
                    type=types.Type.STRING,
                    description="Target date in ISO 8601 format (YYYY-MM-DD).",
                )
            },
            required=["date"],
        ),
    ),
    types.FunctionDeclaration(
        name="create_schedule_item",
        description=(
            "Creates a pending schedule item in the database, assigning `duration` hours "
            "of a task to a specific date. Call this for every (task, date, hours) triple "
            "you decide to schedule. Reduces that day's remaining capacity automatically."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "task_id": types.Schema(
                    type=types.Type.INTEGER,
                    description="The id of the task to schedule.",
                ),
                "date": types.Schema(
                    type=types.Type.STRING,
                    description="Date to schedule on (YYYY-MM-DD).",
                ),
                "duration": types.Schema(
                    type=types.Type.NUMBER,
                    description=(
                        "Hours to assign (rounded to nearest 0.25). "
                        "Must not exceed the task's remaining_duration or the day's remaining capacity."
                    ),
                ),
            },
            required=["task_id", "date", "duration"],
        ),
    ),
    types.FunctionDeclaration(
        name="add_warning",
        description=(
            "Adds a user-facing warning message to the scheduling result. "
            "Use this when a task cannot be fully scheduled before its deadline."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "message": types.Schema(
                    type=types.Type.STRING,
                    description="Warning text to show the user.",
                )
            },
            required=["message"],
        ),
    ),
    types.FunctionDeclaration(
        name="finish_scheduling",
        description=(
            "Call this when you have finished scheduling all tasks. "
            "This signals the agent loop to stop."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
]


class GeminiAgentService:
    """Gemini Function-Calling tabanlı zamanlama ajanı."""

    def __init__(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY")
        self.enabled = bool(api_key)
        if self.enabled:
            self.client = genai.Client(api_key=api_key)
        else:
            self.client = None
            logger.warning(
                "GEMINI_API_KEY not set — LLM scheduling disabled, fallback will be used."
            )

    def explain_plan(self, tasks_count: int, warnings: list[str]) -> str:
        """Plan hakkında kısa bir LLM açıklaması üretir (eski CohereService arayüzü)."""
        if not (self.enabled and self.client):
            return self._fallback_explanation(warnings)

        prompt = (
            "You are a helpful study planner assistant. "
            "Give a brief 1-2 sentence explanation of a task schedule to the student. "
            f"Number of scheduled task groups: {tasks_count}. "
            f"Warnings: {json.dumps(warnings) if warnings else 'none'}. "
            "Be encouraging but honest about any capacity issues."
        )
        try:
            response = self.client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(max_output_tokens=120),
            )
            return response.text.strip()
        except Exception as exc:
            logger.warning("Gemini explain_plan failed: %s", exc)
            return self._fallback_explanation(warnings)

    @staticmethod
    def _fallback_explanation(warnings: list[str]) -> str:
        if warnings:
            return (
                "This schedule prioritizes urgent and difficult tasks first, "
                "but your workload is high — consider reducing daily load or adjusting deadlines."
            )
        return (
            "Closer deadlines are filled first; easier tasks with later deadlines are "
            "spread so you can focus on what is due soonest."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Scheduling Engine — Gemini Agent + Deterministic Fallback
# ─────────────────────────────────────────────────────────────────────────────


class UserSchedulingEngine:
    """Per-user scheduling engine scoped by user_id."""

    def __init__(self, db: Session, user_id: int) -> None:
        self.db = db
        self.user_id = user_id
        self._gemini = GeminiAgentService()

        # Mutable state shared between agent tool calls and the loop
        self._remaining_by_date: dict[date, float] = {}
        self._warnings: list[str] = []
        self._done = False

    # ── Public API ────────────────────────────────────────────────────────────

    def generate_schedule(self) -> tuple[list[dict[str, Any]], list[str]]:
        """
        LLM agent ile plan oluşturur.
        Gemini devre dışıysa veya hata oluşursa deterministik fallback çalışır.
        """
        self._warnings = []
        self._done = False

        profile = self._get_or_create_profile()
        today = date.today()

        # Tüm pending schedule item'larını sil
        self.db.query(models.UserScheduleItem).filter(
            models.UserScheduleItem.user_id == self.user_id,
            models.UserScheduleItem.status == "pending",
        ).delete()
        self.db.commit()

        # Horizon hesapla
        all_tasks = self._fetch_active_tasks()
        max_deadline = max((t.deadline for t in all_tasks), default=today)
        latest_scheduled = (
            self.db.query(func.max(models.UserScheduleItem.date))
            .filter(models.UserScheduleItem.user_id == self.user_id)
            .scalar()
        )
        if latest_scheduled and latest_scheduled > max_deadline:
            max_deadline = latest_scheduled
        horizon_end = max_deadline + timedelta(days=21)

        # Günlük kapasiteleri önceden hesapla (agent tool'ları bu dict'i kullanır)
        self._remaining_by_date = {}
        d = today
        while d <= horizon_end:
            self._remaining_by_date[d] = self._daily_capacity_for(d, profile)
            d += timedelta(days=1)

        if self._gemini.enabled:
            try:
                self._run_agent(all_tasks, today, horizon_end)
                schedule = self._serialize(today - timedelta(days=7), horizon_end)
                return schedule, self._warnings
            except Exception as exc:
                logger.error(
                    "Gemini agent failed (%s) — falling back to deterministic scheduler.",
                    exc,
                )
                # Temizle ve fallback'e geç
                self.db.query(models.UserScheduleItem).filter(
                    models.UserScheduleItem.user_id == self.user_id,
                    models.UserScheduleItem.status == "pending",
                ).delete()
                self.db.commit()
                self._warnings = []

        # Deterministik fallback
        return self._deterministic_generate(profile, all_tasks, today, horizon_end)

    def serialize_existing(self) -> tuple[list[dict[str, Any]], list[str]]:
        tasks = (
            self.db.query(models.UserTask)
            .filter(models.UserTask.user_id == self.user_id)
            .all()
        )
        max_deadline = max((t.deadline for t in tasks), default=date.today())
        range_start = date.today() - timedelta(days=7)
        range_end = max_deadline + timedelta(days=21)
        return self._serialize(range_start, range_end), []

    # ── Gemini Agent Loop ─────────────────────────────────────────────────────

    def _run_agent(
        self,
        tasks: list[models.UserTask],
        today: date,
        horizon_end: date,
    ) -> None:
        """
        Gemini function-calling döngüsü.
        Model tool çağrısı yaparken → tool'ı çalıştır → sonucu geri gönder.
        Model finish_scheduling çağırdığında veya metin yanıtı verdiğinde dur.
        """
        system_prompt = self._build_system_prompt(tasks, today, horizon_end)
        user_message = self._build_user_message(tasks, today, horizon_end)

        # Konuşma geçmişi
        messages: list[types.Content] = [
            types.Content(role="user", parts=[types.Part(text=user_message)])
        ]

        max_iterations = 120  # sonsuz döngü güvencesi
        iteration = 0

        while not self._done and iteration < max_iterations:
            iteration += 1

            response = self._gemini.client.models.generate_content(
                model=GEMINI_MODEL,
                contents=messages,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    tools=[types.Tool(function_declarations=_TOOL_DECLARATIONS)],
                    temperature=0.1,  # deterministik planlama için düşük
                    max_output_tokens=4096,
                ),
            )

            candidate = response.candidates[0]

            # Modelin yanıtını geçmişe ekle
            messages.append(types.Content(role="model", parts=candidate.content.parts))

            # Tool çağrısı var mı?
            tool_calls = [
                p for p in candidate.content.parts if p.function_call is not None
            ]

            if not tool_calls:
                # Metin yanıtı → ajan bitirdi
                logger.debug("Gemini agent finished with text response.")
                break

            # Her tool çağrısını işle, sonuçları tek mesajda topla
            result_parts: list[types.Part] = []
            for part in tool_calls:
                fc = part.function_call
                result = self._dispatch_tool(fc.name, dict(fc.args or {}))
                result_parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name,
                            response={"result": result},
                        )
                    )
                )
                if self._done:
                    break

            messages.append(types.Content(role="user", parts=result_parts))

        self.db.commit()

    def _dispatch_tool(self, name: str, args: dict) -> Any:
        """Tool adına göre doğru metodu çağırır."""
        if name == "get_tasks":
            return self._tool_get_tasks()
        elif name == "get_daily_capacity":
            return self._tool_get_daily_capacity(args["date"])
        elif name == "create_schedule_item":
            return self._tool_create_schedule_item(
                int(args["task_id"]), args["date"], float(args["duration"])
            )
        elif name == "add_warning":
            return self._tool_add_warning(args["message"])
        elif name == "finish_scheduling":
            return self._tool_finish_scheduling()
        else:
            return {"error": f"Unknown tool: {name}"}

    # ── Tool Implementations ──────────────────────────────────────────────────

    def _tool_get_tasks(self) -> list[dict]:
        tasks = self._fetch_active_tasks()
        return [self._task_to_dict(t) for t in tasks]

    def _tool_get_daily_capacity(self, date_str: str) -> dict:
        try:
            d = date.fromisoformat(date_str)
        except ValueError:
            return {"error": f"Invalid date format: {date_str}"}
        capacity = self._remaining_by_date.get(d, 0.0)
        return {"date": date_str, "remaining_hours": round(capacity, 2)}

    def _tool_create_schedule_item(
        self, task_id: int, date_str: str, duration: float
    ) -> dict:
        try:
            d = date.fromisoformat(date_str)
        except ValueError:
            return {"error": f"Invalid date: {date_str}"}

        # 0.25 katına yuvarla
        duration = round(duration * 4) / 4
        if duration < 0.001:
            return {"error": "Duration too small (< 0.25h)"}

        cap = self._remaining_by_date.get(d, 0.0)
        if cap < 0.001:
            return {"error": f"No capacity left on {date_str} (remaining: {cap:.2f}h)"}

        # Kapasite aşımını kırp
        duration = min(duration, cap)

        self.db.add(
            models.UserScheduleItem(
                user_id=self.user_id,
                task_id=task_id,
                date=d,
                duration=duration,
                status="pending",
            )
        )
        self._remaining_by_date[d] = max(0.0, cap - duration)

        return {
            "status": "created",
            "task_id": task_id,
            "date": date_str,
            "duration": duration,
            "capacity_remaining": round(self._remaining_by_date[d], 2),
        }

    def _tool_add_warning(self, message: str) -> dict:
        self._warnings.append(message)
        return {"status": "warning added", "message": message}

    def _tool_finish_scheduling(self) -> dict:
        self._done = True
        return {"status": "scheduling complete"}

    # ── Prompt Builders ───────────────────────────────────────────────────────

    def _build_system_prompt(
        self, tasks: list[models.UserTask], today: date, horizon_end: date
    ) -> str:
        return f"""You are an expert task scheduling agent. Your job is to create an optimal
study/work schedule by calling the provided tools.

Today's date: {today.isoformat()}
Planning horizon: {today.isoformat()} to {horizon_end.isoformat()}

## Your Process
1. Call get_tasks() to see all active tasks.
2. For each task, decide which days to schedule it and how many hours per day.
3. For each (task, date, hours) decision, call create_schedule_item().
   - Always check remaining capacity with get_daily_capacity() if unsure.
4. If a task cannot be fully scheduled before its deadline, call add_warning().
5. When done scheduling ALL tasks, call finish_scheduling().

## Scheduling Rules
- NEVER exceed a day's remaining capacity (get_daily_capacity returns the current remaining).
- Round all durations to the nearest 0.25h (0.25, 0.5, 0.75, 1.0, ...).
- Minimum chunk size: 0.25h.

## Work Style Rules
- intensive: Fill days from today forward, packing as much as possible each day.
  Prioritize by deadline (earliest first), then difficulty (hardest first).
- balanced: Assign exactly daily_target_hours per day, one session per day for this task.
  Spread sessions from today to the deadline evenly.
- deadline_focused: Schedule from the deadline BACKWARD (latest days first).
  Reserve capacity close to the deadline. Prioritize easier tasks first.

## Capacity Management
After each create_schedule_item call the day's capacity is automatically reduced.
Always keep track of how many hours you've assigned so you don't over-schedule.

Be systematic: process one task at a time, finish it completely before moving on.
"""

    def _build_user_message(
        self, tasks: list[models.UserTask], today: date, horizon_end: date
    ) -> str:
        task_summaries = []
        for t in tasks:
            s = (
                f"- Task ID {t.id}: '{t.title}' | deadline: {t.deadline.isoformat()} | "
                f"remaining: {t.remaining_duration:.2f}h | difficulty: {t.difficulty}/5 | "
                f"style: {getattr(t, 'work_style', 'intensive') or 'intensive'}"
            )
            dth = getattr(t, "daily_target_hours", None)
            if dth:
                s += f" | daily_target: {dth}h"
            task_summaries.append(s)

        tasks_text = "\n".join(task_summaries) if task_summaries else "No active tasks."

        return (
            f"Please schedule the following tasks from {today.isoformat()} "
            f"to {horizon_end.isoformat()}:\n\n{tasks_text}\n\n"
            "Use the provided tools to build the schedule step by step. "
            "Call finish_scheduling() when you are done."
        )

    # ── Deterministic Fallback (orijinal algoritma) ───────────────────────────

    def _deterministic_generate(
        self,
        profile: models.UserProfileData,
        tasks: list[models.UserTask],
        today: date,
        horizon_end: date,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        warnings: list[str] = []

        # Kapasite dict'ini sıfırla (agent bozmuş olabilir)
        remaining_by_date: dict[date, float] = {}
        d = today
        while d <= horizon_end:
            remaining_by_date[d] = self._daily_capacity_for(d, profile)
            d += timedelta(days=1)

        non_df_tasks = sorted(
            [
                t
                for t in tasks
                if (getattr(t, "work_style", "intensive") or "intensive")
                != "deadline_focused"
            ],
            key=lambda t: (t.deadline, -t.difficulty),
        )
        df_tasks = sorted(
            [
                t
                for t in tasks
                if (getattr(t, "work_style", "intensive") or "intensive")
                == "deadline_focused"
            ],
            key=lambda t: (t.deadline, t.difficulty),
        )
        sorted_tasks = non_df_tasks + df_tasks

        # Missed görevlerin tarihleri
        missed_rows = (
            self.db.query(models.UserScheduleItem.task_id, models.UserScheduleItem.date)
            .filter(
                models.UserScheduleItem.user_id == self.user_id,
                models.UserScheduleItem.status == "missed",
                models.UserScheduleItem.date >= today,
            )
            .all()
        )
        skipped_dates_by_task: dict[int, set[date]] = defaultdict(set)
        for task_id, missed_day in missed_rows:
            skipped_dates_by_task[int(task_id)].add(missed_day)

        # deadline_focused ön-rezervasyon
        df_reserved: dict[int, dict[date, float]] = {}
        for task in df_tasks:
            hours_left = float(task.remaining_duration)
            skipped = skipped_dates_by_task.get(task.id, set())
            available_days = [
                d
                for d in (
                    today + timedelta(n)
                    for n in range((task.deadline - today).days + 1)
                )
                if d not in skipped and remaining_by_date.get(d, 0.0) > 0.001
            ]
            reserved: dict[date, float] = {}
            hours_rem = hours_left
            for day in reversed(available_days):
                if hours_rem <= 0.001:
                    break
                cap = remaining_by_date.get(day, 0.0)
                if cap < 0.001:
                    continue
                chunk = round(min(hours_rem, cap) * 4) / 4
                chunk = min(chunk, hours_rem, cap)
                if chunk < 0.001:
                    continue
                reserved[day] = chunk
                remaining_by_date[day] -= chunk
                hours_rem -= chunk
            df_reserved[task.id] = reserved

        balanced_claimed: set[tuple] = set()
        existing_balanced = (
            self.db.query(models.UserScheduleItem.date, models.UserScheduleItem.task_id)
            .filter(
                models.UserScheduleItem.user_id == self.user_id,
                models.UserScheduleItem.status == "completed",
            )
            .all()
        )
        for _date, _task_id in existing_balanced:
            task_obj = (
                self.db.query(models.UserTask)
                .filter(
                    models.UserTask.id == _task_id,
                    models.UserTask.user_id == self.user_id,
                )
                .first()
            )
            if (
                task_obj
                and (getattr(task_obj, "work_style", "intensive") or "intensive")
                == "balanced"
            ):
                balanced_claimed.add((_date, int(_task_id)))

        for task in sorted_tasks:
            hours_left = float(task.remaining_duration)
            work_style = getattr(task, "work_style", "intensive") or "intensive"
            skipped = skipped_dates_by_task.get(task.id, set())

            available_days = [
                d
                for d in (
                    today + timedelta(n)
                    for n in range((task.deadline - today).days + 1)
                )
                if d not in skipped and remaining_by_date.get(d, 0.0) > 0.001
            ]

            if not available_days:
                if hours_left > 0.001 and task.deadline >= today:
                    warnings.append(
                        f"Not enough calendar time before deadline for «{task.title}»."
                    )
                continue

            hours_remaining = hours_left

            if work_style == "intensive":
                for day in available_days:
                    if hours_remaining <= 0.001:
                        break
                    cap = remaining_by_date.get(day, 0.0)
                    if cap < 0.001:
                        continue
                    chunk = round(min(hours_remaining, cap) * 4) / 4
                    chunk = min(chunk, hours_remaining, cap)
                    if chunk < 0.001:
                        continue
                    self.db.add(
                        models.UserScheduleItem(
                            user_id=self.user_id,
                            task_id=task.id,
                            date=day,
                            duration=chunk,
                            status="pending",
                        )
                    )
                    remaining_by_date[day] -= chunk
                    hours_remaining -= chunk

            elif work_style == "balanced":
                daily_target = float(getattr(task, "daily_target_hours", None) or 0.0)
                if daily_target <= 0.001:
                    all_days = [
                        today + timedelta(n)
                        for n in range((task.deadline - today).days + 1)
                        if (today + timedelta(n)) not in skipped
                    ]
                    daily_target = (
                        max(round((hours_left / len(all_days)) * 4) / 4, 0.25)
                        if all_days
                        else 0.25
                    )

                d = today
                while hours_remaining > 0.001 and d <= task.deadline:
                    if d in skipped or (d, task.id) in balanced_claimed:
                        d += timedelta(days=1)
                        continue
                    cap = remaining_by_date.get(d, 0.0)
                    if cap < 0.001:
                        d += timedelta(days=1)
                        continue
                    chunk = round(min(hours_remaining, daily_target, cap) * 4) / 4
                    chunk = min(chunk, hours_remaining, cap)
                    if chunk < 0.001:
                        d += timedelta(days=1)
                        continue
                    self.db.add(
                        models.UserScheduleItem(
                            user_id=self.user_id,
                            task_id=task.id,
                            date=d,
                            duration=chunk,
                            status="pending",
                        )
                    )
                    remaining_by_date[d] -= chunk
                    hours_remaining -= chunk
                    balanced_claimed.add((d, task.id))
                    d += timedelta(days=1)

            else:  # deadline_focused
                reserved = df_reserved.get(task.id, {})
                for day in sorted(reserved.keys(), reverse=True):
                    chunk = reserved[day]
                    if chunk < 0.001:
                        continue
                    self.db.add(
                        models.UserScheduleItem(
                            user_id=self.user_id,
                            task_id=task.id,
                            date=day,
                            duration=chunk,
                            status="pending",
                        )
                    )
                    hours_remaining -= chunk

            if (
                hours_remaining > 0.001
                and work_style != "balanced"
                and task.deadline >= today
            ):
                warnings.append(
                    f"Not enough calendar time before deadline for «{task.title}»."
                )

        self.db.commit()
        range_start = today - timedelta(days=7)
        return self._serialize(range_start, horizon_end), warnings

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _fetch_active_tasks(self) -> list[models.UserTask]:
        return (
            self.db.query(models.UserTask)
            .filter(
                models.UserTask.user_id == self.user_id,
                models.UserTask.completed.is_(False),
                models.UserTask.remaining_duration > 0,
            )
            .all()
        )

    @staticmethod
    def _task_to_dict(t: models.UserTask) -> dict:
        return {
            "id": t.id,
            "title": t.title,
            "deadline": t.deadline.isoformat(),
            "remaining_duration": float(t.remaining_duration),
            "difficulty": t.difficulty,
            "work_style": getattr(t, "work_style", "intensive") or "intensive",
            "daily_target_hours": (
                float(t.daily_target_hours)
                if getattr(t, "daily_target_hours", None)
                else None
            ),
        }

    def _get_or_create_profile(self) -> models.UserProfileData:
        profile = (
            self.db.query(models.UserProfileData)
            .filter(models.UserProfileData.user_id == self.user_id)
            .first()
        )
        if not profile:
            profile = models.UserProfileData(user_id=self.user_id)
            self.db.add(profile)
            self.db.commit()
            self.db.refresh(profile)
        return profile

    def _daily_capacity_for(self, day: date, profile: models.UserProfileData) -> float:
        max_cap = min(float(getattr(profile, "max_capacity", 24.0) or 24.0), 24.0)
        base_fallback = min(float(profile.daily_capacity), max_cap)

        slots = (
            self.db.query(models.UserAvailabilitySlot)
            .filter(
                models.UserAvailabilitySlot.user_id == self.user_id,
                models.UserAvailabilitySlot.date == day,
            )
            .all()
        )

        def parse_hhmm(s: str) -> float:
            hh, mm = s.split(":")
            return int(hh) + int(mm) / 60.0

        if not slots:
            raw = base_fallback
        else:
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
            raw = available_hours if has_explicit_available else base_fallback

        completed_items = (
            self.db.query(models.UserScheduleItem)
            .filter(
                models.UserScheduleItem.user_id == self.user_id,
                models.UserScheduleItem.date == day,
                models.UserScheduleItem.status == "completed",
            )
            .all()
        )
        consumed = sum(float(ci.completed_duration or 0.0) for ci in completed_items)
        return max(0.0, min(max(0.0, raw - consumed), max_cap))

    def _serialize(self, range_start: date, range_end: date) -> list[dict[str, Any]]:
        # Orphan cleanup
        valid_ids = {
            t.id
            for t in self.db.query(models.UserTask.id)
            .filter(models.UserTask.user_id == self.user_id)
            .all()
        }
        orphans = self.db.query(models.UserScheduleItem).filter(
            models.UserScheduleItem.user_id == self.user_id,
            ~models.UserScheduleItem.task_id.in_(valid_ids),
        )
        if orphans.first():
            orphans.delete(synchronize_session=False)
            self.db.commit()

        rows = (
            self.db.query(models.UserScheduleItem)
            .filter(
                models.UserScheduleItem.user_id == self.user_id,
                models.UserScheduleItem.date >= range_start,
                models.UserScheduleItem.date <= range_end,
            )
            .order_by(
                models.UserScheduleItem.date.asc(), models.UserScheduleItem.id.asc()
            )
            .all()
        )
        task_ids = {r.task_id for r in rows}
        task_map: dict[int, models.UserTask] = {}
        if task_ids:
            for t in (
                self.db.query(models.UserTask)
                .filter(models.UserTask.id.in_(task_ids))
                .all()
            ):
                task_map[t.id] = t

        row_ids = [r.id for r in rows]
        undoable_ids: set[int] = set()
        if row_ids:
            undoable_ids = {
                sid
                for (sid,) in self.db.query(models.UserActionLog.schedule_id)
                .filter(
                    models.UserActionLog.user_id == self.user_id,
                    models.UserActionLog.schedule_id.in_(row_ids),
                )
                .all()
                if sid is not None
            }
        done_locked_task_ids = {
            tid
            for (tid,) in self.db.query(models.UserActionLog.task_id)
            .filter(
                models.UserActionLog.user_id == self.user_id,
                models.UserActionLog.action_type == "done",
            )
            .all()
            if tid is not None
        }

        grouped: dict[date, list] = defaultdict(list)
        for row in rows:
            t = task_map.get(row.task_id)
            title = t.title if t else f"Task #{row.task_id}"
            tr = float(t.remaining_duration) if t else 0.0
            t_work_style = getattr(t, "work_style", "intensive") if t else "intensive"
            t_daily_target = (
                float(t.daily_target_hours)
                if t and getattr(t, "daily_target_hours", None)
                else None
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
                    "task_done_locked": bool(row.task_id in done_locked_task_ids),
                    "daily_target_hours": (
                        t_daily_target if t_work_style == "balanced" else None
                    ),
                }
            )

        return [
            {"date": day, "tasks": day_tasks}
            for day, day_tasks in sorted(grouped.items(), key=lambda x: x[0])
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Legacy single-user SchedulingEngine (geriye dönük uyumluluk — kullanılmıyor)
# ─────────────────────────────────────────────────────────────────────────────


class SchedulingEngine:
    """Eski tek-kullanıcılı motor. main.py'daki import'ları kırmamak için bırakıldı."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def generate_schedule(self):
        raise NotImplementedError(
            "Legacy SchedulingEngine is deprecated. Use UserSchedulingEngine."
        )

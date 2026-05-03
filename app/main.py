from pathlib import Path
from datetime import date, datetime, timezone, timedelta
import json

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Cookie, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import models, schemas
from .database import Base, engine, get_db
from .services import CohereService, SchedulingEngine
from .auth import auth_router, get_current_user, get_current_user_optional


load_dotenv()
Base.metadata.create_all(bind=engine)


def ensure_schema_updates() -> None:
    with engine.begin() as conn:
        # ── Legacy single-user tables ──────────────────────────────────────────
        task_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(tasks)"))}
        if "total_duration" not in task_cols and "estimated_duration" in task_cols:
            conn.execute(
                text("ALTER TABLE tasks ADD COLUMN total_duration FLOAT DEFAULT 0")
            )
            conn.execute(
                text(
                    "UPDATE tasks SET total_duration = estimated_duration WHERE total_duration = 0"
                )
            )
        if "estimated_duration" in task_cols and "total_duration" in task_cols:
            conn.execute(
                text(
                    "UPDATE tasks SET estimated_duration = total_duration WHERE estimated_duration != total_duration"
                )
            )
        if "remaining_duration" not in task_cols:
            conn.execute(
                text("ALTER TABLE tasks ADD COLUMN remaining_duration FLOAT DEFAULT 0")
            )
            conn.execute(
                text(
                    "UPDATE tasks SET remaining_duration = total_duration WHERE remaining_duration = 0"
                )
            )
        if "remaining_duration" in task_cols and "total_duration" in task_cols:
            conn.execute(
                text(
                    "UPDATE tasks SET remaining_duration = total_duration WHERE remaining_duration > total_duration"
                )
            )

        profile_cols = {
            row[1] for row in conn.execute(text("PRAGMA table_info(user_profile)"))
        }
        if "max_capacity" not in profile_cols:
            conn.execute(
                text(
                    "ALTER TABLE user_profile ADD COLUMN max_capacity FLOAT DEFAULT 24.0"
                )
            )
        conn.execute(
            text(
                "UPDATE user_profile SET max_capacity = 24.0 WHERE max_capacity IS NULL OR max_capacity <= 0"
            )
        )

        schedule_cols = {
            row[1] for row in conn.execute(text("PRAGMA table_info(schedule_items)"))
        }
        if "status" not in schedule_cols:
            conn.execute(
                text(
                    "ALTER TABLE schedule_items ADD COLUMN status VARCHAR DEFAULT 'pending'"
                )
            )
        if "handled_at" not in schedule_cols:
            conn.execute(
                text("ALTER TABLE schedule_items ADD COLUMN handled_at DATETIME")
            )
        if "completed_duration" not in schedule_cols:
            conn.execute(
                text(
                    "ALTER TABLE schedule_items ADD COLUMN completed_duration FLOAT DEFAULT 0"
                )
            )
        conn.execute(
            text(
                "UPDATE schedule_items SET status = 'pending' WHERE status = 'planned'"
            )
        )
        conn.execute(
            text("UPDATE schedule_items SET status = 'completed' WHERE status = 'done'")
        )
        conn.execute(
            text(
                "UPDATE schedule_items SET status = 'completed' WHERE status = 'partial'"
            )
        )
        conn.execute(
            text("UPDATE schedule_items SET handled_at = NULL WHERE status = 'pending'")
        )
        conn.execute(
            text(
                "UPDATE schedule_items SET completed_duration = 0 WHERE completed_duration IS NULL"
            )
        )

        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS availability_slots ("
                "id INTEGER PRIMARY KEY,"
                "user_id INTEGER NOT NULL DEFAULT 1,"
                "date DATE NOT NULL,"
                "start_time VARCHAR NOT NULL,"
                "end_time VARCHAR NOT NULL,"
                "type VARCHAR NOT NULL"
                ")"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS action_logs ("
                "id INTEGER PRIMARY KEY,"
                "action_type VARCHAR NOT NULL,"
                "schedule_id INTEGER,"
                "task_id INTEGER,"
                "timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "expires_at DATETIME NOT NULL,"
                "previous_state TEXT NOT NULL"
                ")"
            )
        )

        # ── Multi-user tables (created by SQLAlchemy Base.metadata.create_all above) ──
        # Just run migrations for any missing columns in user_tasks
        try:
            ut_cols = {
                row[1] for row in conn.execute(text("PRAGMA table_info(user_tasks)"))
            }
            if ut_cols:
                if "estimated_duration" not in ut_cols:
                    conn.execute(
                        text(
                            "ALTER TABLE user_tasks ADD COLUMN estimated_duration FLOAT DEFAULT 0"
                        )
                    )
        except Exception:
            pass


ensure_schema_updates()

app = FastAPI(title="Autonomous Task Scheduling Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register auth routes
app.include_router(auth_router)

cohere_service = CohereService()
static_dir = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ─── Page routes ──────────────────────────────────────────────────────────────


@app.get("/")
def read_index(current_user: models.User | None = Depends(get_current_user_optional)):
    """Serve login page if not authenticated, planner if authenticated."""
    if current_user:
        return FileResponse(static_dir / "index.html")
    return FileResponse(static_dir / "login.html")


@app.get("/login")
def login_page():
    return FileResponse(static_dir / "login.html")


@app.get("/app")
def app_page(current_user: models.User = Depends(get_current_user)):
    return FileResponse(static_dir / "index.html")


# ─── Per-user task CRUD ───────────────────────────────────────────────────────


def _get_user_task_or_404(task_id: int, user_id: int, db: Session) -> models.UserTask:
    t = (
        db.query(models.UserTask)
        .filter(
            models.UserTask.id == task_id,
            models.UserTask.user_id == user_id,
        )
        .first()
    )
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t


@app.post("/tasks", response_model=schemas.TaskOut)
def create_task(
    task: schemas.TaskCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if task.deadline < date.today():
        raise HTTPException(
            status_code=400,
            detail="RED ALERT: You cannot create a task with a past deadline.",
        )
    payload = task.model_dump()
    payload["remaining_duration"] = payload["total_duration"]
    payload["estimated_duration"] = payload["total_duration"]
    payload["user_id"] = current_user.id
    db_task = models.UserTask(**payload)
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task


@app.get("/tasks", response_model=list[schemas.TaskOut])
def list_tasks(
    include_completed: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = (
        db.query(models.UserTask)
        .filter(models.UserTask.user_id == current_user.id)
        .order_by(models.UserTask.deadline.asc())
    )
    if not include_completed:
        query = query.filter(models.UserTask.completed.is_(False))
    return query.all()


@app.patch("/tasks/{task_id}", response_model=schemas.TaskOut)
def update_task(
    task_id: int,
    update: schemas.TaskUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    db_task = _get_user_task_or_404(task_id, current_user.id, db)
    patch = update.model_dump(exclude_none=True)
    patch.pop("remaining_duration", None)
    completed_in = patch.pop("completed", None)
    if update.deadline is not None and update.deadline < date.today():
        raise HTTPException(
            status_code=400,
            detail="RED ALERT: You cannot set a task deadline in the past.",
        )
    old_est = db_task.total_duration
    old_rem = db_task.remaining_duration
    for key, value in patch.items():
        setattr(db_task, key, value)
    if update.total_duration is not None and not db_task.completed:
        new_est = update.total_duration
        db_task.estimated_duration = new_est
        if new_est < old_est:
            db_task.remaining_duration = min(old_rem, new_est)
        else:
            db_task.remaining_duration = min(
                new_est, old_rem + max(0.0, new_est - old_est)
            )
    if completed_in is True:
        # Save previous remaining_duration so undo can restore it
        prev_remaining = float(db_task.remaining_duration)

        # Mark all pending schedule items for this task as "completed"
        # so they remain visible on the calendar as Done blocks
        pending_items = (
            db.query(models.UserScheduleItem)
            .filter(
                models.UserScheduleItem.user_id == current_user.id,
                models.UserScheduleItem.task_id == db_task.id,
                models.UserScheduleItem.status == "pending",
            )
            .all()
        )
        for item in pending_items:
            item.status = "completed"
            item.completed_duration = item.duration
            item.handled_at = datetime.now(timezone.utc)
            db.add(item)

        db_task.remaining_duration = 0.0
        db_task.completed = True
        db.add(
            models.UserActionLog(
                user_id=current_user.id,
                action_type="done",
                task_id=db_task.id,
                expires_at=datetime.utcnow() + timedelta(days=3650),
                previous_state=json.dumps(
                    {"previous_remaining_duration": prev_remaining}
                ),
            )
        )
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task


@app.delete("/tasks/{task_id}")
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    db_task = _get_user_task_or_404(task_id, current_user.id, db)
    db.query(models.UserScheduleItem).filter(
        models.UserScheduleItem.task_id == task_id,
        models.UserScheduleItem.user_id == current_user.id,
    ).delete()
    db.delete(db_task)
    db.commit()
    return {"status": "deleted"}


@app.post("/tasks/{task_id}/undo-complete")
def undo_task_complete(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    log = (
        db.query(models.UserActionLog)
        .filter(
            models.UserActionLog.user_id == current_user.id,
            models.UserActionLog.task_id == task_id,
            models.UserActionLog.action_type == "done",
        )
        .order_by(models.UserActionLog.id.desc())
        .first()
    )
    if not log:
        raise HTTPException(status_code=404, detail="No undo entry found")

    prev = json.loads(log.previous_state)
    task = _get_user_task_or_404(task_id, current_user.id, db)

    # Restore task state
    task.completed = False
    task.remaining_duration = prev.get(
        "previous_remaining_duration", task.total_duration
    )

    # Revert this task's "completed-by-done-button" schedule items back to pending
    done_items = (
        db.query(models.UserScheduleItem)
        .filter(
            models.UserScheduleItem.user_id == current_user.id,
            models.UserScheduleItem.task_id == task_id,
            models.UserScheduleItem.status == "completed",
        )
        .all()
    )
    for item in done_items:
        item.status = "pending"
        item.completed_duration = 0.0
        item.handled_at = None
        db.add(item)

    db.delete(log)
    db.add(task)
    db.commit()
    return {"status": "undone"}


# ─── Schedule ─────────────────────────────────────────────────────────────────


class UserSchedulingEngine:
    """Per-user scheduling engine that scopes all queries by user_id."""

    def __init__(self, db: Session, user_id: int) -> None:
        self.db = db
        self.user_id = user_id

    def _get_or_create_profile(self) -> models.UserProfileData:
        profile = db_p = (
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
        from collections import defaultdict

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

        completed_hours = (
            self.db.query(models.UserScheduleItem)
            .filter(
                models.UserScheduleItem.user_id == self.user_id,
                models.UserScheduleItem.date == day,
                models.UserScheduleItem.status == "completed",
            )
            .with_entities(models.UserScheduleItem.completed_duration)
            .all()
        )
        consumed = sum(float(h[0] or 0.0) for h in completed_hours)
        return max(0.0, min(max(0.0, raw - consumed), max_cap))

    def generate_schedule(self):
        from collections import defaultdict

        profile = self._get_or_create_profile()
        tasks = (
            self.db.query(models.UserTask)
            .filter(
                models.UserTask.user_id == self.user_id,
                models.UserTask.completed.is_(False),
                models.UserTask.remaining_duration > 0,
            )
            .order_by(models.UserTask.deadline.asc(), models.UserTask.difficulty.desc())
            .all()
        )

        self.db.query(models.UserScheduleItem).filter(
            models.UserScheduleItem.user_id == self.user_id,
            models.UserScheduleItem.status == "pending",
        ).delete()
        self.db.commit()

        warnings = []
        max_deadline = date.today()
        if tasks:
            max_deadline = max(t.deadline for t in tasks)
        horizon_end = max_deadline + timedelta(days=21)

        remaining_by_date: dict = defaultdict(float)
        d = date.today()
        while d <= horizon_end:
            remaining_by_date[d] = self._daily_capacity_for(d, profile)
            d += timedelta(days=1)

        # If a task is marked missed on a date, do not schedule it again on that
        # same date. This applies for today and future dates in the planning horizon.
        missed_rows = (
            self.db.query(models.UserScheduleItem.task_id, models.UserScheduleItem.date)
            .filter(
                models.UserScheduleItem.user_id == self.user_id,
                models.UserScheduleItem.status == "missed",
                models.UserScheduleItem.date >= date.today(),
            )
            .all()
        )
        skipped_dates_by_task: dict[int, set[date]] = defaultdict(set)
        for task_id, missed_day in missed_rows:
            skipped_dates_by_task[int(task_id)].add(missed_day)

        for task in tasks:
            hours_left = float(task.remaining_duration)
            day = date.today()
            guard = 0
            while hours_left > 0.001:
                guard += 1
                if guard > 4000:
                    warnings.append("Scheduler stopped early due to iteration limits.")
                    break
                if day > task.deadline:
                    warnings.append(
                        f"Not enough calendar time before deadline for «{task.title}»."
                    )
                    break
                if day > horizon_end:
                    warnings.append(f"Horizon overflow for «{task.title}».")
                    break
                if day in skipped_dates_by_task.get(task.id, set()):
                    day += timedelta(days=1)
                    continue
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
                    models.UserScheduleItem(
                        user_id=self.user_id,
                        task_id=task.id,
                        date=day,
                        duration=chunk,
                        status="pending",
                    )
                )
                remaining_by_date[day] -= chunk
                hours_left -= chunk
                if remaining_by_date[day] < 0.001:
                    day += timedelta(days=1)

        self.db.commit()
        range_start = date.today() - timedelta(days=7)
        range_end = horizon_end
        return self._serialize(range_start, range_end), warnings

    def serialize_existing(self):
        tasks = (
            self.db.query(models.UserTask)
            .filter(models.UserTask.user_id == self.user_id)
            .all()
        )
        max_deadline = max((t.deadline for t in tasks), default=date.today())
        range_start = date.today() - timedelta(days=7)
        range_end = max_deadline + timedelta(days=21)
        return self._serialize(range_start, range_end), []

    def _serialize(self, range_start: date, range_end: date):
        from collections import defaultdict

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
        task_map = {}
        if task_ids:
            for t in (
                self.db.query(models.UserTask)
                .filter(models.UserTask.id.in_(task_ids))
                .all()
            ):
                task_map[t.id] = t

        row_ids = [r.id for r in rows]
        undoable_ids = set()
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

        grouped = defaultdict(list)
        for row in rows:
            t = task_map.get(row.task_id)
            title = t.title if t else f"Task #{row.task_id}"
            tr = float(t.remaining_duration) if t else 0.0
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


@app.get("/plan", response_model=schemas.PlanResponse)
def get_plan(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    eng = UserSchedulingEngine(db, current_user.id)
    schedule, warnings = eng.generate_schedule()
    explanation = cohere_service.explain_plan(len(schedule), warnings)
    return {"schedule": schedule, "warnings": warnings, "explanation": explanation}


@app.post("/reschedule", response_model=schemas.PlanResponse)
def reschedule(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return get_plan(db=db, current_user=current_user)


# ─── Schedule item updates ─────────────────────────────────────────────────────


@app.patch("/schedule-items/{item_id}", response_model=schemas.PlanResponse)
def update_schedule_item(
    item_id: int,
    update: schemas.ScheduleItemStatusUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    item = (
        db.query(models.UserScheduleItem)
        .filter(
            models.UserScheduleItem.id == item_id,
            models.UserScheduleItem.user_id == current_user.id,
        )
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Schedule item not found")
    if item.status != "pending":
        raise HTTPException(status_code=400, detail="Schedule item already handled")

    task = _get_user_task_or_404(item.task_id, current_user.id, db)
    completed_hours = float(update.completed_hours)

    rows = (
        db.query(models.UserScheduleItem)
        .filter(models.UserScheduleItem.user_id == current_user.id)
        .order_by(models.UserScheduleItem.id.asc())
        .all()
    )
    schedule_snapshot = [
        {
            "id": r.id,
            "task_id": r.task_id,
            "date": r.date.isoformat(),
            "duration": float(r.duration),
            "completed_duration": float(getattr(r, "completed_duration", 0.0) or 0.0),
            "status": r.status,
            "handled_at": r.handled_at.isoformat() if r.handled_at else None,
        }
        for r in rows
    ]
    prev_state = {
        "task": {
            "id": task.id,
            "remaining_duration": float(task.remaining_duration),
            "completed": bool(task.completed),
        },
        "schedule_item": {
            "id": item.id,
            "status": item.status,
            "completed_duration": float(
                getattr(item, "completed_duration", 0.0) or 0.0
            ),
            "handled_at": item.handled_at.isoformat() if item.handled_at else None,
        },
        "schedule_snapshot": schedule_snapshot,
    }

    item.completed_duration = completed_hours
    if completed_hours <= 1e-9:
        item.status = "missed"
    else:
        item.status = "completed"
        task.remaining_duration = max(
            0.0, float(task.remaining_duration) - completed_hours
        )

    if task.remaining_duration <= 1e-9:
        task.remaining_duration = 0.0
        task.completed = True

    item.handled_at = datetime.now(timezone.utc)
    db.add(task)
    db.add(item)
    db.add(
        models.UserActionLog(
            user_id=current_user.id,
            action_type=item.status,
            schedule_id=item.id,
            task_id=task.id,
            expires_at=datetime.utcnow() + timedelta(days=3650),
            previous_state=json.dumps(prev_state),
        )
    )
    db.commit()
    return get_plan(db=db, current_user=current_user)


@app.post("/schedule-items/{item_id}/undo")
def undo_schedule_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    log = (
        db.query(models.UserActionLog)
        .filter(
            models.UserActionLog.user_id == current_user.id,
            models.UserActionLog.schedule_id == item_id,
        )
        .order_by(models.UserActionLog.id.desc())
        .first()
    )
    if not log:
        raise HTTPException(status_code=404, detail="No undo entry found")
    prev = json.loads(log.previous_state)
    task = _get_user_task_or_404(prev["task"]["id"], current_user.id, db)
    task.remaining_duration = prev["task"]["remaining_duration"]
    task.completed = prev["task"]["completed"]

    snapshot = prev.get("schedule_snapshot", [])
    if snapshot:
        db.query(models.UserScheduleItem).filter(
            models.UserScheduleItem.user_id == current_user.id
        ).delete()
        for row in snapshot:
            db.add(
                models.UserScheduleItem(
                    user_id=current_user.id,
                    task_id=row["task_id"],
                    date=date.fromisoformat(row["date"]),
                    duration=row["duration"],
                    completed_duration=row["completed_duration"],
                    status=row["status"],
                    handled_at=(
                        datetime.fromisoformat(row["handled_at"])
                        if row["handled_at"]
                        else None
                    ),
                )
            )
    db.delete(log)
    db.add(task)
    db.commit()
    return get_plan(db=db, current_user=current_user)


# ─── Availability ─────────────────────────────────────────────────────────────


@app.post("/availability", response_model=schemas.AvailabilitySlotOut)
def create_availability(
    slot: schemas.AvailabilitySlotCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    payload = slot.model_dump()
    if payload.get("type") != "available":
        raise HTTPException(
            status_code=400, detail="Only 'available' slots are supported"
        )

    hours = payload.pop("available_hours", None)
    start_time = payload.get("start_time")
    end_time = payload.get("end_time")

    if hours is not None:
        h = float(hours)
        if h < 0 or h > 24:
            raise HTTPException(
                status_code=400, detail="available_hours must be between 0 and 24"
            )
        total_minutes = int(round(h * 60))
        hh = total_minutes // 60
        mm = total_minutes % 60
        payload["start_time"] = "00:00"
        payload["end_time"] = f"{hh:02d}:{mm:02d}"
    else:
        if not start_time or not end_time:
            raise HTTPException(
                status_code=400,
                detail="Provide either available_hours or both start_time and end_time",
            )

    payload.pop("available_hours", None)
    db_slot = models.UserAvailabilitySlot(**payload, user_id=current_user.id)
    db.add(db_slot)
    db.commit()
    db.refresh(db_slot)
    return _serialize_availability_slot(db_slot)


def _serialize_availability_slot(slot) -> dict:
    def parse_hhmm(s: str) -> float:
        hh, mm = str(s).split(":")
        return int(hh) + int(mm) / 60.0

    hours = None
    try:
        start = parse_hhmm(slot.start_time)
        end = parse_hhmm(slot.end_time)
        hours = max(0.0, end - start)
    except Exception:
        hours = None

    return {
        "id": slot.id,
        "date": slot.date,
        "start_time": slot.start_time,
        "end_time": slot.end_time,
        "type": slot.type,
        "available_hours": hours,
    }


@app.get("/availability", response_model=list[schemas.AvailabilitySlotOut])
def list_availability(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    q = db.query(models.UserAvailabilitySlot).filter(
        models.UserAvailabilitySlot.user_id == current_user.id,
        models.UserAvailabilitySlot.type == "available",
    )
    if start:
        q = q.filter(models.UserAvailabilitySlot.date >= start.date())
    if end:
        q = q.filter(models.UserAvailabilitySlot.date <= end.date())
    slots = q.order_by(
        models.UserAvailabilitySlot.date.asc(),
        models.UserAvailabilitySlot.start_time.asc(),
    ).all()
    return [_serialize_availability_slot(s) for s in slots]


@app.delete("/availability/{slot_id}")
def delete_availability(
    slot_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    slot = (
        db.query(models.UserAvailabilitySlot)
        .filter(
            models.UserAvailabilitySlot.id == slot_id,
            models.UserAvailabilitySlot.user_id == current_user.id,
        )
        .first()
    )
    if not slot:
        raise HTTPException(status_code=404, detail="Availability slot not found")
    db.delete(slot)
    db.commit()
    return {"status": "deleted"}


# ─── Profile ──────────────────────────────────────────────────────────────────


@app.put("/profile", response_model=schemas.UserProfileOut)
def update_profile(
    profile: schemas.UserProfileIn,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    current = (
        db.query(models.UserProfileData)
        .filter(models.UserProfileData.user_id == current_user.id)
        .first()
    )
    if not current:
        current = models.UserProfileData(user_id=current_user.id)
    current.daily_capacity = profile.daily_capacity
    current.max_capacity = 24.0
    db.add(current)
    db.commit()
    db.refresh(current)
    return current


@app.get("/profile", response_model=schemas.UserProfileOut)
def get_profile(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    current = (
        db.query(models.UserProfileData)
        .filter(models.UserProfileData.user_id == current_user.id)
        .first()
    )
    if not current:
        current = models.UserProfileData(user_id=current_user.id)
        db.add(current)
        db.commit()
        db.refresh(current)
    return current

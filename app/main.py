from pathlib import Path
from datetime import datetime, timezone, timedelta
import json

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import models, schemas
from .database import Base, engine, get_db
from .services import CohereService, SchedulingEngine


load_dotenv()
Base.metadata.create_all(bind=engine)


def ensure_schema_updates() -> None:
    with engine.begin() as conn:
        task_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(tasks)"))}
        if "total_duration" not in task_cols and "estimated_duration" in task_cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN total_duration FLOAT DEFAULT 0"))
            conn.execute(text("UPDATE tasks SET total_duration = estimated_duration WHERE total_duration = 0"))
        if "estimated_duration" in task_cols and "total_duration" in task_cols:
            conn.execute(text("UPDATE tasks SET estimated_duration = total_duration WHERE estimated_duration != total_duration"))
        if "remaining_duration" not in task_cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN remaining_duration FLOAT DEFAULT 0"))
            conn.execute(text("UPDATE tasks SET remaining_duration = total_duration WHERE remaining_duration = 0"))
        if "remaining_duration" in task_cols and "total_duration" in task_cols:
            conn.execute(text("UPDATE tasks SET remaining_duration = total_duration WHERE remaining_duration > total_duration"))

        profile_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(user_profile)"))}
        if "max_capacity" not in profile_cols:
            conn.execute(text("ALTER TABLE user_profile ADD COLUMN max_capacity FLOAT DEFAULT 24.0"))
        conn.execute(text("UPDATE user_profile SET max_capacity = 24.0 WHERE max_capacity IS NULL OR max_capacity <= 0"))

        schedule_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(schedule_items)"))}
        if "status" not in schedule_cols:
            conn.execute(text("ALTER TABLE schedule_items ADD COLUMN status VARCHAR DEFAULT 'pending'"))
        if "handled_at" not in schedule_cols:
            conn.execute(text("ALTER TABLE schedule_items ADD COLUMN handled_at DATETIME"))
        if "completed_duration" not in schedule_cols:
            conn.execute(text("ALTER TABLE schedule_items ADD COLUMN completed_duration FLOAT DEFAULT 0"))
        conn.execute(text("UPDATE schedule_items SET status = 'pending' WHERE status = 'planned'"))
        conn.execute(text("UPDATE schedule_items SET status = 'completed' WHERE status = 'done'"))
        conn.execute(text("UPDATE schedule_items SET handled_at = NULL WHERE status = 'pending'"))
        conn.execute(text("UPDATE schedule_items SET completed_duration = 0 WHERE completed_duration IS NULL"))

        # New tables (create_all already handles it, but sqlite may need explicit creation
        # when existing db was created before models were added).
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


ensure_schema_updates()

app = FastAPI(title="Autonomous Task Scheduling Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

cohere_service = CohereService()
static_dir = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def read_index():
    return FileResponse(static_dir / "index.html")


@app.post("/tasks", response_model=schemas.TaskOut)
def create_task(task: schemas.TaskCreate, db: Session = Depends(get_db)):
    payload = task.model_dump()
    payload["remaining_duration"] = payload["total_duration"]
    payload["estimated_duration"] = payload["total_duration"]
    db_task = models.Task(**payload)
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task


@app.get("/tasks", response_model=list[schemas.TaskOut])
def list_tasks(include_completed: bool = Query(default=False), db: Session = Depends(get_db)):
    query = db.query(models.Task).order_by(models.Task.deadline.asc())
    if not include_completed:
        query = query.filter(models.Task.completed.is_(False))
    return query.all()


@app.patch("/tasks/{task_id}", response_model=schemas.TaskOut)
def update_task(task_id: int, update: schemas.TaskUpdate, db: Session = Depends(get_db)):
    db_task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    patch = update.model_dump(exclude_none=True)
    patch.pop("remaining_duration", None)
    completed_in = patch.pop("completed", None)
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
            db_task.remaining_duration = min(new_est, old_rem + max(0.0, new_est - old_est))
    if completed_in is True:
        db_task.completed = True
        db_task.remaining_duration = 0.0
    elif completed_in is False:
        db_task.completed = False
    if update.remaining_duration is not None and not db_task.completed:
        db_task.remaining_duration = update.remaining_duration
    db.add(db_task)
    db.commit()
    db.refresh(db_task)
    return db_task


@app.delete("/tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    db_task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")
    # Cascade delete: schedule entries + logs that reference this task.
    db.query(models.ScheduleItem).filter(models.ScheduleItem.task_id == task_id).delete()
    db.query(models.ActionLog).filter(models.ActionLog.task_id == task_id).delete()
    db.delete(db_task)
    db.commit()
    return {"status": "deleted"}


@app.post("/schedule-items/{item_id}/undo", response_model=schemas.PlanResponse)
def undo_schedule_item(item_id: int, db: Session = Depends(get_db)):
    log = (
        db.query(models.ActionLog)
        .filter(models.ActionLog.schedule_id == item_id)
        .order_by(models.ActionLog.id.desc())
        .first()
    )
    if not log:
        raise HTTPException(status_code=404, detail="No undo action found")
    # Undo is always allowed; no time window enforcement.

    try:
        prev = json.loads(log.previous_state)
    except Exception:
        raise HTTPException(status_code=500, detail="Corrupted undo state")

    # Restore task state
    if prev.get("task"):
        tstate = prev["task"]
        task = db.query(models.Task).filter(models.Task.id == tstate["id"]).first()
        if task:
            task.remaining_duration = float(tstate["remaining_duration"])
            task.completed = bool(tstate["completed"])
            db.add(task)

    # Restore schedule snapshot if present, else restore single item
    if prev.get("schedule_snapshot") is not None:
        db.query(models.ScheduleItem).delete()
        for row in prev["schedule_snapshot"]:
            db.add(
                models.ScheduleItem(
                    id=row["id"],
                    task_id=row["task_id"],
                    date=datetime.fromisoformat(row["date"]).date(),
                    duration=float(row["duration"]),
                    completed_duration=float(row.get("completed_duration", 0.0)),
                    status=row["status"],
                    handled_at=datetime.fromisoformat(row["handled_at"]) if row.get("handled_at") else None,
                )
            )
    elif prev.get("schedule_item"):
        s = prev["schedule_item"]
        item = db.query(models.ScheduleItem).filter(models.ScheduleItem.id == s["id"]).first()
        if item:
            item.status = s["status"]
            item.completed_duration = float(s.get("completed_duration", 0.0))
            item.handled_at = datetime.fromisoformat(s["handled_at"]) if s.get("handled_at") else None
            db.add(item)

    db.delete(log)
    db.commit()

    engine_instance = SchedulingEngine(db)
    schedule, warnings = engine_instance.serialize_existing_plan()
    explanation = cohere_service.explain_plan(sum(len(d["tasks"]) for d in schedule), warnings)
    return {"schedule": schedule, "warnings": warnings, "explanation": explanation}


@app.patch("/schedule-items/{item_id}", response_model=schemas.PlanResponse)
def update_schedule_item(item_id: int, update: schemas.ScheduleItemStatusUpdate, db: Session = Depends(get_db)):
    item = db.query(models.ScheduleItem).filter(models.ScheduleItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Schedule item not found")
    if item.status != "pending":
        raise HTTPException(status_code=400, detail="Schedule item already handled")

    task = db.query(models.Task).filter(models.Task.id == item.task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    assigned = float(item.duration)
    completed_hours = float(update.completed_hours)
    if completed_hours < 0 or completed_hours - assigned > 1e-9:
        raise HTTPException(status_code=400, detail="completed_hours must be between 0 and assigned duration")

    # Snapshot for event-based undo
    schedule_snapshot = None
    # For partial/missed we regenerate the schedule, so we capture a snapshot to restore.
    will_reschedule = completed_hours < assigned
    if will_reschedule:
        rows = db.query(models.ScheduleItem).order_by(models.ScheduleItem.id.asc()).all()
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
            "completed_duration": float(getattr(item, "completed_duration", 0.0) or 0.0),
            "handled_at": item.handled_at.isoformat() if item.handled_at else None,
        },
    }
    if schedule_snapshot is not None:
        prev_state["schedule_snapshot"] = schedule_snapshot

    # Apply update
    item.completed_duration = completed_hours
    if completed_hours <= 1e-9:
        item.status = "missed"
    elif abs(completed_hours - assigned) <= 1e-9:
        item.status = "completed"
        task.remaining_duration = max(0.0, float(task.remaining_duration) - completed_hours)
    else:
        item.status = "partial"
        task.remaining_duration = max(0.0, float(task.remaining_duration) - completed_hours)

    if task.remaining_duration <= 1e-9:
        task.remaining_duration = 0.0
        task.completed = True

    item.handled_at = datetime.now(timezone.utc)

    db.add(task)
    db.add(item)
    db.add(
        models.ActionLog(
            action_type=item.status,
            schedule_id=item.id,
            task_id=task.id,
            # Keep a non-null value for DB schema compatibility; undo doesn't enforce it.
            expires_at=datetime.utcnow() + timedelta(days=3650),
            previous_state=json.dumps(prev_state),
        )
    )
    db.commit()

    if item.status in {"missed", "partial"}:
        return get_plan(db)

    engine_instance = SchedulingEngine(db)
    schedule, _ = engine_instance.serialize_existing_plan()
    explanation = cohere_service.explain_plan(sum(len(d["tasks"]) for d in schedule), [])
    return {"schedule": schedule, "warnings": [], "explanation": explanation}


@app.post("/availability", response_model=schemas.AvailabilitySlotOut)
def create_availability(slot: schemas.AvailabilitySlotCreate, db: Session = Depends(get_db)):
    payload = slot.model_dump()
    # Hard safety: blocked is removed, keep only available
    if payload.get("type") != "available":
        raise HTTPException(status_code=400, detail="Only 'available' slots are supported")

    hours = payload.pop("available_hours", None)
    start_time = payload.get("start_time")
    end_time = payload.get("end_time")

    if hours is not None:
        h = float(hours)
        if h <= 0 or h > 24:
            raise HTTPException(status_code=400, detail="available_hours must be between 0 and 24")
        # Store as 00:00 -> HH:MM window; we only need daily totals.
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

    # Not stored in DB schema
    payload.pop("available_hours", None)
    db_slot = models.AvailabilitySlot(**payload, user_id=1)
    db.add(db_slot)
    db.commit()
    db.refresh(db_slot)
    return db_slot


@app.get("/availability", response_model=list[schemas.AvailabilitySlotOut])
def list_availability(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
):
    # Only "available" is supported; keep legacy blocked rows out of the API response.
    q = (
        db.query(models.AvailabilitySlot)
        .filter(models.AvailabilitySlot.user_id == 1, models.AvailabilitySlot.type == "available")
    )
    if start:
        q = q.filter(models.AvailabilitySlot.date >= start.date())
    if end:
        q = q.filter(models.AvailabilitySlot.date <= end.date())
    return q.order_by(models.AvailabilitySlot.date.asc(), models.AvailabilitySlot.start_time.asc()).all()


@app.delete("/availability/{slot_id}")
def delete_availability(slot_id: int, db: Session = Depends(get_db)):
    slot = db.query(models.AvailabilitySlot).filter(models.AvailabilitySlot.id == slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Availability slot not found")
    db.delete(slot)
    db.commit()
    return {"status": "deleted"}


@app.get("/plan", response_model=schemas.PlanResponse)
def get_plan(db: Session = Depends(get_db)):
    engine_instance = SchedulingEngine(db)
    engine_instance.adapt_from_progress()
    schedule, warnings = engine_instance.generate_schedule()
    explanation = cohere_service.explain_plan(len(schedule), warnings)
    return {"schedule": schedule, "warnings": warnings, "explanation": explanation}


@app.post("/reschedule", response_model=schemas.PlanResponse)
def reschedule(db: Session = Depends(get_db)):
    return get_plan(db)


@app.put("/profile", response_model=schemas.UserProfileOut)
def update_profile(profile: schemas.UserProfileIn, db: Session = Depends(get_db)):
    current = db.query(models.UserProfile).first()
    if not current:
        current = models.UserProfile()
    current.daily_capacity = profile.daily_capacity
    current.max_capacity = 24.0
    db.add(current)
    db.commit()
    db.refresh(current)
    return current


@app.get("/profile", response_model=schemas.UserProfileOut)
def get_profile(db: Session = Depends(get_db)):
    current = db.query(models.UserProfile).first()
    if not current:
        current = models.UserProfile()
        db.add(current)
        db.commit()
        db.refresh(current)
    return current

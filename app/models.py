from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.sql import func

from .database import Base


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    deadline = Column(Date, nullable=False)
    # Backward-compat column for existing SQLite schema constraints.
    estimated_duration = Column(Float, nullable=False, default=0.0)
    total_duration = Column(Float, nullable=False)
    remaining_duration = Column(Float, nullable=False, default=0.0)
    difficulty = Column(Integer, nullable=False)
    completed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserProfile(Base):
    __tablename__ = "user_profile"

    id = Column(Integer, primary_key=True, index=True)
    daily_capacity = Column(Float, default=4.0, nullable=False)
    max_capacity = Column(Float, default=24.0, nullable=False)


class ScheduleItem(Base):
    __tablename__ = "schedule_items"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, nullable=False)
    date = Column(Date, nullable=False, index=True)
    # Backward-compat: old column name in sqlite schema was "duration".
    # We keep it and treat it as assigned_duration.
    duration = Column(Float, nullable=False)
    completed_duration = Column(Float, nullable=False, default=0.0)
    status = Column(String, nullable=False, default="pending")  # pending|partial|completed|missed
    handled_at = Column(DateTime(timezone=True), nullable=True)


class AvailabilitySlot(Base):
    __tablename__ = "availability_slots"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, default=1, index=True)
    date = Column(Date, nullable=False, index=True)
    start_time = Column(String, nullable=False)  # "HH:MM"
    end_time = Column(String, nullable=False)  # "HH:MM"
    type = Column(String, nullable=False)  # blocked|available


class ActionLog(Base):
    __tablename__ = "action_logs"

    id = Column(Integer, primary_key=True, index=True)
    action_type = Column(String, nullable=False)  # done|missed|delete
    schedule_id = Column(Integer, nullable=True, index=True)
    task_id = Column(Integer, nullable=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    previous_state = Column(Text, nullable=False)  # JSON payload

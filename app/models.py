from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.sql import func

from .database import Base


# ─── Original models (kept for backward compat) ───────────────────────────────


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    deadline = Column(Date, nullable=False)
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
    duration = Column(Float, nullable=False)
    completed_duration = Column(Float, nullable=False, default=0.0)
    status = Column(String, nullable=False, default="pending")
    handled_at = Column(DateTime(timezone=True), nullable=True)


class AvailabilitySlot(Base):
    __tablename__ = "availability_slots"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, default=1, index=True)
    date = Column(Date, nullable=False, index=True)
    start_time = Column(String, nullable=False)
    end_time = Column(String, nullable=False)
    type = Column(String, nullable=False)


class ActionLog(Base):
    __tablename__ = "action_logs"

    id = Column(Integer, primary_key=True, index=True)
    action_type = Column(String, nullable=False)
    schedule_id = Column(Integer, nullable=True, index=True)
    task_id = Column(Integer, nullable=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    previous_state = Column(Text, nullable=False)


# ─── Auth / multi-user models ──────────────────────────────────────────────────


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, unique=True, index=True)
    username = Column(String, nullable=False, unique=True, index=True)
    hashed_password = Column(String, nullable=False)
    theme = Column(String, nullable=False, default="parchment")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token = Column(String, nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)


class UserTask(Base):
    __tablename__ = "user_tasks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    deadline = Column(Date, nullable=False)
    estimated_duration = Column(Float, nullable=False, default=0.0)
    total_duration = Column(Float, nullable=False)
    remaining_duration = Column(Float, nullable=False, default=0.0)
    difficulty = Column(Integer, nullable=False)
    completed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserScheduleItem(Base):
    __tablename__ = "user_schedule_items"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    task_id = Column(Integer, nullable=False)
    date = Column(Date, nullable=False, index=True)
    duration = Column(Float, nullable=False)
    completed_duration = Column(Float, nullable=False, default=0.0)
    status = Column(String, nullable=False, default="pending")
    handled_at = Column(DateTime(timezone=True), nullable=True)


class UserAvailabilitySlot(Base):
    __tablename__ = "user_availability_slots"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    start_time = Column(String, nullable=False)
    end_time = Column(String, nullable=False)
    type = Column(String, nullable=False)


class UserActionLog(Base):
    __tablename__ = "user_action_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action_type = Column(String, nullable=False)
    schedule_id = Column(Integer, nullable=True, index=True)
    task_id = Column(Integer, nullable=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    previous_state = Column(Text, nullable=False)


class UserProfileData(Base):
    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True
    )
    daily_capacity = Column(Float, default=4.0, nullable=False)
    max_capacity = Column(Float, default=24.0, nullable=False)

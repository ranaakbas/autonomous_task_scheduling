from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


class TaskBase(BaseModel):
    title: str
    deadline: date
    total_duration: float = Field(gt=0)
    difficulty: int = Field(ge=1, le=5)


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    deadline: Optional[date] = None
    total_duration: Optional[float] = Field(default=None, gt=0)
    difficulty: Optional[int] = Field(default=None, ge=1, le=5)
    completed: Optional[bool] = None
    remaining_duration: Optional[float] = Field(default=None, ge=0)


class TaskOut(TaskBase):
    id: int
    completed: bool
    remaining_duration: float

    class Config:
        from_attributes = True


class UserProfileIn(BaseModel):
    daily_capacity: float = Field(gt=0, le=24)


class UserProfileOut(UserProfileIn):
    max_capacity: float

    class Config:
        from_attributes = True


class ScheduleTask(BaseModel):
    id: int
    task: str
    assigned_duration: float
    completed_duration: float
    task_id: int
    status: str


class ScheduleDay(BaseModel):
    date: date
    tasks: list[ScheduleTask]


class PlanResponse(BaseModel):
    schedule: list[ScheduleDay]
    warnings: list[str]
    explanation: str


class ScheduleItemStatusUpdate(BaseModel):
    completed_hours: float = Field(ge=0)


class AvailabilitySlotBase(BaseModel):
    date: date
    # Legacy storage uses start/end times, but UI uses direct hour amount.
    start_time: Optional[str] = None  # HH:MM
    end_time: Optional[str] = None  # HH:MM
    available_hours: Optional[float] = Field(default=None, ge=0, le=24)
    # "blocked" is removed: only explicit available windows are supported.
    type: Literal["available"] = "available"


class AvailabilitySlotCreate(AvailabilitySlotBase):
    pass


class AvailabilitySlotOut(AvailabilitySlotBase):
    id: int

    class Config:
        from_attributes = True

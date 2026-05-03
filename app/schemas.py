from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

# Work style options for task scheduling
WorkStyle = Literal["intensive", "balanced", "deadline_focused"]

# Upper bound for hours logged in one schedule-item completion (typo guard).
MAX_COMPLETED_HOURS_PER_SESSION = 24.0


class TaskBase(BaseModel):
    title: str
    deadline: date
    # For intensive / deadline_focused: total hours for the whole task (required).
    # For balanced: optional — derived from daily_target_hours * remaining days at creation.
    total_duration: Optional[float] = Field(default=None, gt=0)
    difficulty: int = Field(ge=1, le=5)
    work_style: WorkStyle = "intensive"
    # For "balanced" work style only: hours the user wants to work on this task each day.
    daily_target_hours: Optional[float] = Field(default=None, gt=0, le=24)

    @model_validator(mode="after")
    def check_duration_fields(self) -> "TaskBase":
        if self.work_style == "balanced":
            if self.daily_target_hours is None:
                raise ValueError(
                    "daily_target_hours is required for balanced work style"
                )
        else:
            if self.total_duration is None:
                raise ValueError(
                    "total_duration is required for intensive and deadline_focused work styles"
                )
        return self


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    deadline: Optional[date] = None
    total_duration: Optional[float] = Field(default=None, gt=0)
    difficulty: Optional[int] = Field(default=None, ge=1, le=5)
    completed: Optional[bool] = None
    remaining_duration: Optional[float] = Field(default=None, ge=0)
    work_style: Optional[WorkStyle] = None
    daily_target_hours: Optional[float] = Field(default=None, gt=0, le=24)


class TaskOut(TaskBase):
    id: int
    completed: bool
    remaining_duration: float
    work_style: WorkStyle = "intensive"
    daily_target_hours: Optional[float] = None

    class Config:
        from_attributes = True

    @model_validator(mode="after")
    def check_duration_fields(self) -> "TaskOut":
        # Skip validation for output model — DB may have legacy rows
        return self


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
    task_remaining_duration: float = Field(
        default=0.0,
        description="Hours left on the task (Task.remaining_duration) when plan was built.",
    )
    status: str
    undoable: bool = False
    # For balanced tasks: the daily target so the frontend knows the Done threshold
    daily_target_hours: Optional[float] = None


class ScheduleDay(BaseModel):
    date: date
    tasks: list[ScheduleTask]


class PlanResponse(BaseModel):
    schedule: list[ScheduleDay]
    warnings: list[str]
    explanation: str


class ScheduleItemStatusUpdate(BaseModel):
    completed_hours: float = Field(ge=0, le=MAX_COMPLETED_HOURS_PER_SESSION)


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

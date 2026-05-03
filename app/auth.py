"""
auth.py — Drop this file into your app package (same folder as main.py).
Then in main.py add:
    from .auth import auth_router, get_current_user
    app.include_router(auth_router)

Also add these two lines to ensure_schema_updates() or call them once at startup:
    Base.metadata.create_all(bind=engine)   # already there
    # That will create all new tables automatically via the new models.
"""

import hashlib
import os
import secrets
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from .database import get_db
from . import models

auth_router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_TTL_DAYS = 30
TOKEN_BYTES = 32


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _hash_password(password: str) -> str:
    """SHA-256 hash with per-user salt prepended (salt:hash format)."""
    salt = secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{digest}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split(":", 1)
        return hashlib.sha256((salt + password).encode()).hexdigest() == digest
    except Exception:
        return False


def _create_session(db: Session, user_id: int) -> str:
    token = secrets.token_hex(TOKEN_BYTES)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    db.add(models.Session(user_id=user_id, token=token, expires_at=expires))
    db.commit()
    return token


def get_current_user(
    session_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> models.User:
    """FastAPI dependency — raises 401 if not authenticated."""
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    sess = (
        db.query(models.Session).filter(models.Session.token == session_token).first()
    )
    if not sess:
        raise HTTPException(status_code=401, detail="Invalid session")
    if sess.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        db.delete(sess)
        db.commit()
        raise HTTPException(status_code=401, detail="Session expired")
    user = db.query(models.User).filter(models.User.id == sess.user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def get_current_user_optional(
    session_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> models.User | None:
    """Like get_current_user but returns None instead of raising."""
    if not session_token:
        return None
    try:
        return get_current_user(session_token=session_token, db=db)
    except HTTPException:
        return None


# ─── Schemas ─────────────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def username_valid(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Username must be at least 3 characters")
        if len(v) > 30:
            raise ValueError("Username must be at most 30 characters")
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username may only contain letters, digits, - and _")
        return v

    @field_validator("password")
    @classmethod
    def password_valid(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters")
        return v

    @field_validator("email")
    @classmethod
    def email_valid(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email address")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    username: str
    theme: str

    class Config:
        from_attributes = True


# ─── Routes ──────────────────────────────────────────────────────────────────


@auth_router.post("/register", response_model=UserOut)
def register(req: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    # Check duplicates
    if db.query(models.User).filter(models.User.email == req.email).first():
        raise HTTPException(
            status_code=409, detail="An account with this email already exists"
        )
    if db.query(models.User).filter(models.User.username == req.username).first():
        raise HTTPException(status_code=409, detail="This username is already taken")

    user = models.User(
        email=req.email,
        username=req.username,
        hashed_password=_hash_password(req.password),
        theme="parchment",  # Default theme for new users
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Create default profile for the new user
    profile = models.UserProfileData(
        user_id=user.id,
        daily_capacity=4.0,
        max_capacity=24.0,
    )
    db.add(profile)
    db.commit()

    token = _create_session(db, user.id)
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_TTL_DAYS * 86400,
    )
    return user


@auth_router.post("/login", response_model=UserOut)
def login(req: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = (
        db.query(models.User)
        .filter(models.User.email == req.email.strip().lower())
        .first()
    )
    if not user or not _verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    token = _create_session(db, user.id)
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_TTL_DAYS * 86400,
    )
    return user


@auth_router.post("/logout")
def logout(
    response: Response,
    session_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if session_token:
        sess = (
            db.query(models.Session)
            .filter(models.Session.token == session_token)
            .first()
        )
        if sess:
            db.delete(sess)
            db.commit()
    response.delete_cookie("session_token")
    return {"status": "logged out"}


@auth_router.get("/me", response_model=UserOut)
def me(current_user: models.User = Depends(get_current_user)):
    return current_user


@auth_router.patch("/me/theme")
def update_theme(
    body: dict,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    allowed = {
        "terminal",
        "parchment",
        "midnight",
        "forest",
        "rose",
        "obsidian",
        "sky",
        "sage",
    }
    theme = body.get("theme", "")
    if theme not in allowed:
        raise HTTPException(status_code=400, detail="Invalid theme")
    current_user.theme = theme
    db.add(current_user)
    db.commit()
    return {"theme": theme}

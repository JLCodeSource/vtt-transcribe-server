"""Database models for user management and job history."""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, false, func, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vtt_server.database import Base


class User(Base):
    """User account model."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(default=False, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(default=False, nullable=False, server_default=false())
    # Retention default for this user's jobs: "keep" | "delete" | "hold" (+ days). NULL means the platform default.
    retention_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notify_job_ready: Mapped[bool] = mapped_column(default=True, nullable=False, server_default=true())
    default_options: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: preferred engine / diarize / language
    oauth_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 'google', 'github', 'microsoft', or None
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    api_keys: Mapped[list["APIKey"]] = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")
    jobs: Mapped[list["TranscriptionJob"]] = relationship(
        "TranscriptionJob", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username}, email={self.email})>"


class APIKey(Base):
    """User API keys for external services (OpenAI, HuggingFace)."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    service: Mapped[str] = mapped_column(String(50), nullable=False)  # 'openai', 'huggingface'
    encrypted_key: Mapped[str] = mapped_column(String(500), nullable=False)
    key_name: Mapped[str | None] = mapped_column(String(100), nullable=True)  # User-friendly name
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="api_keys")

    def __repr__(self) -> str:
        return f"<APIKey(id={self.id}, user_id={self.user_id}, service={self.service})>"


class TranscriptionJob(Base):
    """Transcription job history and results."""

    __tablename__ = "transcription_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    job_id: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending (queued), processing, completed, failed, cancelled
    step: Mapped[str | None] = mapped_column(String(20), nullable=True)  # queued, transcribing, diarizing, ...
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0..1 overall
    retention_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    options: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: engine, diarize, num_speakers, translate_to
    state: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: result, segments, speaker names, audio path
    secret: Mapped[str | None] = mapped_column(Text, nullable=True)  # encrypted per-job API key; cleared when the job ends
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    translated_to: Mapped[str | None] = mapped_column(String(10), nullable=True)
    with_diarization: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="jobs")

    def __repr__(self) -> str:
        return f"<TranscriptionJob(id={self.id}, job_id={self.job_id}, status={self.status})>"

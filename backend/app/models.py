import os
from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./tacho.db")

def get_engine():
    global DATABASE_URL
    connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
    try:
        if DATABASE_URL.startswith("sqlite:///"):
            db_file_path = DATABASE_URL.replace("sqlite:///", "")
            dir_name = os.path.dirname(db_file_path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
        eng = create_engine(DATABASE_URL, connect_args=connect_args)
        # Test connection
        with eng.connect() as conn:
            pass
        return eng
    except Exception as e:
        # Fallback to local working directory SQLite if /data is not accessible
        fallback_url = "sqlite:///./tacho.db"
        DATABASE_URL = fallback_url
        return create_engine(fallback_url, connect_args={"check_same_thread": False})

engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(120), unique=True, index=True, nullable=True)
    hashed_password = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    activities = relationship("Activity", back_populates="user", cascade="all, delete-orphan")


class Activity(Base):
    __tablename__ = "activities"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    type = Column(String(20), nullable=False)  # DRIVE, WORK, POA, REST
    start_time = Column(DateTime, nullable=False, index=True)
    end_time = Column(DateTime, nullable=True, index=True)  # Null if currently ongoing
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="activities")


def init_db():
    Base.metadata.create_all(bind=engine)


# ================= Pydantic Schemas =================

class ActivityBase(BaseModel):
    type: str  # DRIVE, WORK, POA, REST
    start_time: datetime
    end_time: Optional[datetime] = None
    note: Optional[str] = None

    @field_serializer("start_time", "end_time")
    def serialize_datetime(self, dt: Optional[datetime]) -> Optional[str]:
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class ActivityCreate(ActivityBase):
    pass


class ActivityUpdate(BaseModel):
    type: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    note: Optional[str] = None


class ActivityOut(ActivityBase):
    id: int
    user_id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at")
    def serialize_created_at(self, dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()


class SwitchActivityRequest(BaseModel):
    type: str
    note: Optional[str] = None
    timestamp: Optional[datetime] = None


class UserCreate(BaseModel):
    username: str
    password: str
    email: Optional[str] = None


class UserLogin(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str
    username: str
    user_id: int


class UserOut(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

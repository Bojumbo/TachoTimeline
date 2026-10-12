import os
import logging
import traceback
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.models import (
    init_db, Activity, User, ActivityCreate, ActivityUpdate, ActivityOut,
    SwitchActivityRequest, UserCreate, UserLogin, Token, UserOut
)
from app.auth import (
    get_db, get_current_user, get_current_user_or_guest,
    verify_password, get_password_hash, create_access_token
)
from app.rules import calculate_tachograph_status

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tacho")

# Initialize DB tables safely on startup
try:
    init_db()
except Exception as e:
    logger.error(f"Initial DB creation warning: {e}")

app = FastAPI(
    title="TachoSmart / TruckerTimeline API",
    description="Tachograph calculation engine complying with EU Regulation 561/2006 & Mobility Package",
    version="1.0.0"
)

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global exception on {request.method} {request.url}: {exc}")
    logger.error(traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={"detail": f"Помилка сервера: {str(exc)}"}
    )


# ================= Helper to normalize datetimes for SQLite =================

def to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# ================= Auth Endpoints =================

@app.post("/api/auth/register", response_model=UserOut)
def register(user_in: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.username == user_in.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    user = User(
        username=user_in.username,
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        created_at=to_naive_utc(datetime.now(timezone.utc))
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post("/api/auth/login", response_model=Token)
def login(login_data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == login_data.username).first()
    if not user or not verify_password(login_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(data={"sub": user.username})
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user.username,
        "user_id": user.id
    }


@app.post("/api/auth/guest", response_model=Token)
def guest_login(db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == "driver_guest").first()
    if not user:
        user = User(
            username="driver_guest",
            email="driver@tachotimeline.eu",
            hashed_password=get_password_hash("tachoguest123"),
            created_at=to_naive_utc(datetime.now(timezone.utc))
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    token = create_access_token(data={"sub": user.username})
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user.username,
        "user_id": user.id
    }


@app.get("/api/auth/me", response_model=UserOut)
def get_me(current_user: User = Depends(get_current_user_or_guest)):
    return current_user


# ================= Activity Endpoints =================

@app.get("/api/activities", response_model=List[ActivityOut])
def list_activities(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_or_guest)
):
    query = db.query(Activity).filter(Activity.user_id == current_user.id)
    if start_date:
        query = query.filter((Activity.end_time >= to_naive_utc(start_date)) | (Activity.end_time.is_(None)))
    if end_date:
        query = query.filter(Activity.start_time <= to_naive_utc(end_date))
    return query.order_by(Activity.start_time.asc()).all()


@app.post("/api/activities", response_model=ActivityOut)
def create_activity(
    activity_in: ActivityCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_or_guest)
):
    activity = Activity(
        user_id=current_user.id,
        type=activity_in.type.upper(),
        start_time=to_naive_utc(activity_in.start_time),
        end_time=to_naive_utc(activity_in.end_time),
        note=activity_in.note,
        created_at=to_naive_utc(datetime.now(timezone.utc))
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


@app.put("/api/activities/{activity_id}", response_model=ActivityOut)
def update_activity(
    activity_id: int,
    activity_in: ActivityUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_or_guest)
):
    act = db.query(Activity).filter(Activity.id == activity_id, Activity.user_id == current_user.id).first()
    if not act:
        raise HTTPException(status_code=404, detail="Activity not found")
    
    if activity_in.type is not None:
        act.type = activity_in.type.upper()
    if activity_in.start_time is not None:
        act.start_time = to_naive_utc(activity_in.start_time)
    if activity_in.end_time is not None:
        act.end_time = to_naive_utc(activity_in.end_time)
    if activity_in.note is not None:
        act.note = activity_in.note
        
    db.commit()
    db.refresh(act)
    return act


@app.delete("/api/activities/{activity_id}")
def delete_activity(
    activity_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_or_guest)
):
    act = db.query(Activity).filter(Activity.id == activity_id, Activity.user_id == current_user.id).first()
    if not act:
        raise HTTPException(status_code=404, detail="Activity not found")
    db.delete(act)
    db.commit()
    return {"message": "Activity deleted successfully"}


@app.post("/api/activities/switch")
def switch_activity(
    req: SwitchActivityRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_or_guest)
):
    ts = to_naive_utc(req.timestamp or datetime.now(timezone.utc))

    # Find open activity
    ongoing = db.query(Activity).filter(
        Activity.user_id == current_user.id,
        Activity.end_time.is_(None)
    ).first()

    if ongoing:
        if ongoing.type == req.type.upper():
            return {"status": "unchanged", "activity_id": ongoing.id}
        ongoing.end_time = ts
        db.add(ongoing)

    # Create new ongoing activity
    new_act = Activity(
        user_id=current_user.id,
        type=req.type.upper(),
        start_time=ts,
        end_time=None,
        note=req.note,
        created_at=to_naive_utc(datetime.now(timezone.utc))
    )
    db.add(new_act)
    db.commit()
    db.refresh(new_act)

    return {"status": "switched", "new_activity": new_act.id, "type": new_act.type}


@app.get("/api/tachograph/status")
def get_tachograph_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_or_guest)
):
    activities = db.query(Activity).filter(Activity.user_id == current_user.id).order_by(Activity.start_time.asc()).all()
    status_report = calculate_tachograph_status(activities)
    return status_report


@app.post("/api/activities/seed-demo")
def seed_demo_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_or_guest)
):
    # Delete existing activities for this user
    db.query(Activity).filter(Activity.user_id == current_user.id).delete()

    now = datetime.now(timezone.utc)
    today_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now < today_6am + timedelta(hours=8):
        base_time = now - timedelta(hours=8)
    else:
        base_time = today_6am

    demo_schedule = [
        {"type": "WORK", "start": base_time, "end": base_time + timedelta(minutes=30), "note": "Щоденний огляд тягача (Daily Walkaround)"},
        {"type": "DRIVE", "start": base_time + timedelta(minutes=30), "end": base_time + timedelta(hours=4, minutes=45), "note": "Маршрут Київ - Житомир"},
        {"type": "REST", "start": base_time + timedelta(hours=4, minutes=45), "end": base_time + timedelta(hours=5, minutes=30), "note": "Обов'язкова пауза 45 хв"},
        {"type": "DRIVE", "start": base_time + timedelta(hours=5, minutes=30), "end": base_time + timedelta(hours=8), "note": "Маршрут Житомир - Рівне"},
        {"type": "WORK", "start": base_time + timedelta(hours=8), "end": base_time + timedelta(hours=8, minutes=45), "note": "Розвантаження логістичний центр"},
        {"type": "DRIVE", "start": base_time + timedelta(hours=8, minutes=45), "end": base_time + timedelta(hours=10, minutes=15), "note": "Переїзд до TIR-паркінгу"},
        {"type": "REST", "start": base_time + timedelta(hours=10, minutes=15), "end": None, "note": "Щоденний відпочинок (пауза)"}
    ]

    for item in demo_schedule:
        act = Activity(
            user_id=current_user.id,
            type=item["type"],
            start_time=to_naive_utc(item["start"]),
            end_time=to_naive_utc(item["end"]) if item["end"] else None,
            note=item["note"],
            created_at=to_naive_utc(datetime.now(timezone.utc))
        )
        db.add(act)

    db.commit()
    return {"message": "Demo trucker data seeded successfully", "activities_count": len(demo_schedule)}


@app.post("/api/activities/clear")
def clear_activities(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_or_guest)
):
    db.query(Activity).filter(Activity.user_id == current_user.id).delete()
    db.commit()
    return {"message": "All activities cleared"}


# Mount static files if frontend folder exists alongside backend
frontend_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend")
if os.path.exists(frontend_path):
    app.mount("/static", StaticFiles(directory=frontend_path), name="static")

    @app.get("/")
    def serve_frontend():
        return FileResponse(os.path.join(frontend_path, "index.html"))

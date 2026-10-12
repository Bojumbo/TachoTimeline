"""
TachoTimeline - EU Regulation 561/2006 + Mobility Package Calculation Engine
Tracks:
1. Continuous driving streak (max 4h30m, 45m or 15m+30m break).
2. Daily driving (max 9h standard, 10h extended max 2x/week).
3. Daily shift & 24h window (13h shift for 11h regular rest, 15h shift for 9h reduced rest max 3x/week).
4. Weekly limits (56h calendar week, 90h two-week, 144h period until weekly rest).
"""
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

MAX_CONTINUOUS_DRIVE = timedelta(hours=4, minutes=30)  # 270 min
STANDARD_DAILY_DRIVE = timedelta(hours=9)             # 540 min
EXTENDED_DAILY_DRIVE = timedelta(hours=10)            # 600 min
MAX_SHIFT_REGULAR = timedelta(hours=13)               # 780 min (24 - 11h rest)
MAX_SHIFT_REDUCED = timedelta(hours=15)               # 900 min (24 - 9h rest)
WEEKLY_DRIVE_LIMIT = timedelta(hours=56)              # 3360 min
TWO_WEEK_DRIVE_LIMIT = timedelta(hours=90)            # 5400 min
MAX_PERIOD_BEFORE_WEEKLY_REST = timedelta(hours=144)  # 6 * 24h


def normalize_activity(act: Any, current_time: datetime) -> Dict[str, Any]:
    """Helper to convert ORM model or dict to standard format with UTC datetimes."""
    if isinstance(act, dict):
        act_type = act.get("type", "REST").upper()
        start = act.get("start") or act.get("start_time")
        end = act.get("end") or act.get("end_time")
        act_id = act.get("id")
        note = act.get("note", "")
    else:
        act_type = getattr(act, "type", "REST").upper()
        start = getattr(act, "start_time", None)
        end = getattr(act, "end_time", None)
        act_id = getattr(act, "id", None)
        note = getattr(act, "note", "")

    # Normalize datetimes to aware UTC
    if isinstance(start, str):
        start = datetime.fromisoformat(start.replace("Z", "+00:00"))
    if isinstance(end, str):
        end = datetime.fromisoformat(end.replace("Z", "+00:00"))

    if start and start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end and end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    # If activity is ongoing, end is current time
    is_ongoing = (end is None)
    if is_ongoing:
        end = current_time

    return {
        "id": act_id,
        "type": act_type,
        "start": start,
        "end": end,
        "is_ongoing": is_ongoing,
        "note": note
    }


def calculate_tachograph_status(activities: List[Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Computes real-time compliance with EU 561/2006.
    activities: List of activities sorted chronologically.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # Normalize and sort activities by start time
    norm_acts: List[Dict[str, Any]] = []
    for act in activities:
        item = normalize_activity(act, now)
        if item["start"] and item["end"] and item["end"] >= item["start"]:
            norm_acts.append(item)

    norm_acts.sort(key=lambda x: x["start"])

    # 1. CONTINUOUS DRIVING CALCULATION (Art. 7)
    # A break of 45 min or split (15 min followed by 30 min) resets continuous driving.
    # Breaks < 15 min do not count.
    # 30 followed by 15 is invalid.
    current_drive_streak = timedelta()
    split_15_taken = False
    last_reset_time = norm_acts[0]["start"] if norm_acts else now

    for act in norm_acts:
        duration = act["end"] - act["start"]

        if act["type"] == "DRIVE":
            current_drive_streak += duration

        elif act["type"] in ("REST", "POA"):  # In certain conditions, POA during multi-manning counts, but for single driver only REST counts as break
            if act["type"] == "REST":
                if duration >= timedelta(minutes=45):
                    # Qualifying 45+ min break -> complete reset
                    current_drive_streak = timedelta()
                    split_15_taken = False
                    last_reset_time = act["end"]
                elif duration >= timedelta(minutes=30) and split_15_taken:
                    # Second part of split (>= 30 min) -> complete reset
                    current_drive_streak = timedelta()
                    split_15_taken = False
                    last_reset_time = act["end"]
                elif duration >= timedelta(minutes=15) and not split_15_taken:
                    # First part of split (>= 15 min)
                    split_15_taken = True

    current_drive_streak_min = int(current_drive_streak.total_seconds() // 60)
    remaining_drive = max(timedelta(), MAX_CONTINUOUS_DRIVE - current_drive_streak)
    drive_left_before_pause_min = int(remaining_drive.total_seconds() // 60)
    required_pause_duration = 30 if split_15_taken else 45

    is_continuous_violation = current_drive_streak > MAX_CONTINUOUS_DRIVE
    is_continuous_warning = (drive_left_before_pause_min <= 30 and not is_continuous_violation)

    # 2. IDENTIFY DAILY SHIFTS & REST PERIODS (Art. 8)
    # A daily rest is a rest >= 9h (or split: >=3h + >=9h).
    # A weekly rest is a rest >= 24h (reduced) or >= 45h (regular).
    # A shift starts after a daily or weekly rest with the first non-rest activity.
    shifts = []
    current_shift_acts: List[Dict[str, Any]] = []
    last_shift_start: Optional[datetime] = None
    daily_rests = []
    weekly_rests = []

    for i, act in enumerate(norm_acts):
        duration = act["end"] - act["start"]

        if act["type"] == "REST":
            if duration >= timedelta(hours=24):
                weekly_rests.append(act)
                # Ends the current shift if one was open
                if current_shift_acts:
                    shifts.append({
                        "start": current_shift_acts[0]["start"],
                        "end": act["start"],
                        "activities": list(current_shift_acts),
                    })
                    current_shift_acts = []
                continue
            elif duration >= timedelta(hours=9):
                daily_rests.append(act)
                # Ends the current shift
                if current_shift_acts:
                    shifts.append({
                        "start": current_shift_acts[0]["start"],
                        "end": act["start"],
                        "activities": list(current_shift_acts),
                    })
                    current_shift_acts = []
                continue

        # If not a long rest, belongs to the current shift
        current_shift_acts.append(act)

    # The ongoing / current shift is the last open shift
    current_shift = None
    shift_start_time = None
    shift_elapsed = timedelta()
    daily_drive_time = timedelta()

    if current_shift_acts:
        current_shift = current_shift_acts
        shift_start_time = current_shift_acts[0]["start"]
        shift_elapsed = now - shift_start_time
        for act in current_shift_acts:
            if act["type"] == "DRIVE":
                daily_drive_time += (act["end"] - act["start"])

    # 3. DAILY DRIVING LIMITS & EXTENSIONS (Art. 6)
    # Standard: 9h (540m). Extended: 10h (600m), max 2 times per week.
    # Calculate how many completed shifts this week used > 9h driving:
    extensions_used_this_week = 0
    reduced_rests_used_this_week = 0

    # Determine start of current week (Monday 00:00 UTC)
    start_of_week = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_prev_week = start_of_week - timedelta(days=7)

    for s in shifts:
        if s["start"] >= start_of_week:
            s_drive = sum((a["end"] - a["start"] for a in s["activities"] if a["type"] == "DRIVE"), timedelta())
            if s_drive > STANDARD_DAILY_DRIVE:
                extensions_used_this_week += 1

    for r in daily_rests:
        if r["start"] >= start_of_week:
            r_dur = r["end"] - r["start"]
            if timedelta(hours=9) <= r_dur < timedelta(hours=11):
                reduced_rests_used_this_week += 1

    daily_drive_min = int(daily_drive_time.total_seconds() // 60)
    extensions_remaining = max(0, 2 - extensions_used_this_week)
    can_extend_today = extensions_remaining > 0 or daily_drive_time > STANDARD_DAILY_DRIVE

    effective_daily_max = EXTENDED_DAILY_DRIVE if can_extend_today else STANDARD_DAILY_DRIVE
    daily_drive_left = max(timedelta(), effective_daily_max - daily_drive_time)
    daily_drive_left_min = int(daily_drive_left.total_seconds() // 60)

    # 4. SHIFT WINDOW DEADLINES (13h vs 15h)
    shift_elapsed_min = int(shift_elapsed.total_seconds() // 60)
    shift_left_13h_min = max(0, int((MAX_SHIFT_REGULAR - shift_elapsed).total_seconds() // 60))
    shift_left_15h_min = max(0, int((MAX_SHIFT_REDUCED - shift_elapsed).total_seconds() // 60))

    reduced_rests_remaining = max(0, 3 - reduced_rests_used_this_week)
    allowed_shift_hours = 15 if reduced_rests_remaining > 0 else 13
    allowed_shift_delta = MAX_SHIFT_REDUCED if allowed_shift_hours == 15 else MAX_SHIFT_REGULAR
    current_shift_left_min = max(0, int((allowed_shift_delta - shift_elapsed).total_seconds() // 60))

    # 5. WEEKLY & BI-WEEKLY TOTALS (Art. 6 & Art. 8)
    week_drive = timedelta()
    prev_week_drive = timedelta()

    for act in norm_acts:
        if act["type"] == "DRIVE":
            dur = act["end"] - act["start"]
            if act["start"] >= start_of_week:
                week_drive += dur
            elif act["start"] >= start_of_prev_week:
                prev_week_drive += dur

    two_week_drive = week_drive + prev_week_drive
    week_drive_hours = round(week_drive.total_seconds() / 3600, 2)
    two_week_drive_hours = round(two_week_drive.total_seconds() / 3600, 2)
    week_drive_left_hours = max(0.0, round((WEEKLY_DRIVE_LIMIT - week_drive).total_seconds() / 3600, 2))
    two_week_drive_left_hours = max(0.0, round((TWO_WEEK_DRIVE_LIMIT - two_week_drive).total_seconds() / 3600, 2))

    # Time until mandatory weekly rest (144h rule)
    last_weekly_rest_end = weekly_rests[-1]["end"] if weekly_rests else (start_of_week - timedelta(hours=45))
    weekly_rest_deadline = last_weekly_rest_end + MAX_PERIOD_BEFORE_WEEKLY_REST
    time_until_weekly_rest = max(timedelta(), weekly_rest_deadline - now)
    hours_until_weekly_rest = round(time_until_weekly_rest.total_seconds() / 3600, 1)

    # Current Activity
    current_activity = None
    if norm_acts:
        last_act = norm_acts[-1]
        if last_act["is_ongoing"] or (now - last_act["end"] < timedelta(minutes=1)):
            current_activity = {
                "id": last_act["id"],
                "type": last_act["type"],
                "start": last_act["start"].isoformat(),
                "duration_min": int((now - last_act["start"]).total_seconds() // 60),
                "is_ongoing": last_act["is_ongoing"],
                "note": last_act["note"]
            }

    # Warnings collection
    warnings = []
    if is_continuous_violation:
        warnings.append({
            "code": "CONTINUOUS_DRIVE_EXCEEDED",
            "level": "danger",
            "message": "Перевищено ліміт безперервного кермування (4:30 год)! Негайно зупиніться на відпочинок."
        })
    elif is_continuous_warning:
        warnings.append({
            "code": "CONTINUOUS_DRIVE_NEAR",
            "level": "warning",
            "message": f"До обов'язкової паузи залишилось менше {drive_left_before_pause_min} хв!"
        })

    if daily_drive_time > effective_daily_max:
        warnings.append({
            "code": "DAILY_DRIVE_EXCEEDED",
            "level": "danger",
            "message": f"Перевищено щоденний ліміт кермування ({int(effective_daily_max.total_seconds()//3600)} год)!"
        })
    elif daily_drive_left_min <= 30 and daily_drive_left_min > 0:
        warnings.append({
            "code": "DAILY_DRIVE_NEAR",
            "level": "warning",
            "message": f"Залишок щоденного кермування: {daily_drive_left_min} хв."
        })

    if shift_start_time and shift_elapsed > allowed_shift_delta:
        warnings.append({
            "code": "SHIFT_EXCEEDED",
            "level": "danger",
            "message": f"Зміна перевищила ліміт {allowed_shift_hours} год! Обов'язковий щоденний відпочинок!"
        })
    elif shift_start_time and current_shift_left_min <= 45 and current_shift_left_min > 0:
        warnings.append({
            "code": "SHIFT_NEAR",
            "level": "warning",
            "message": f"До завершення робочої зміни залишилось {current_shift_left_min} хв!"
        })

    return {
        # 1. Continuous Driving
        "current_drive_streak_min": current_drive_streak_min,
        "drive_left_before_pause_min": drive_left_before_pause_min,
        "split_15_taken": split_15_taken,
        "must_take_pause_minutes": required_pause_duration,
        "is_continuous_violation": is_continuous_violation,
        "is_continuous_warning": is_continuous_warning,
        # 2. Daily Driving
        "daily_drive_min": daily_drive_min,
        "daily_drive_left_min": daily_drive_left_min,
        "effective_daily_max_hours": 10 if can_extend_today else 9,
        "extensions_used_this_week": extensions_used_this_week,
        "extensions_remaining": extensions_remaining,
        "can_extend_today": can_extend_today,
        # 3. Daily Rest & Shift
        "shift_started_at": shift_start_time.isoformat() if shift_start_time else None,
        "shift_elapsed_min": shift_elapsed_min,
        "shift_left_13h_min": shift_left_13h_min,
        "shift_left_15h_min": shift_left_15h_min,
        "current_shift_left_min": current_shift_left_min,
        "allowed_shift_hours": allowed_shift_hours,
        "reduced_rests_used_this_week": reduced_rests_used_this_week,
        "reduced_rests_remaining": reduced_rests_remaining,
        # 4. Weekly Limits
        "week_drive_hours": week_drive_hours,
        "week_drive_left_hours": week_drive_left_hours,
        "two_week_drive_hours": two_week_drive_hours,
        "two_week_drive_left_hours": two_week_drive_left_hours,
        "hours_until_weekly_rest": hours_until_weekly_rest,
        # 5. Current Activity & Warnings
        "current_activity": current_activity,
        "warnings": warnings,
        "server_time": now.isoformat()
    }

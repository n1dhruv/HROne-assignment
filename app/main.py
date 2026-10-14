"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator
from pymongo import MongoClient
from datetime import date as dt
from typing import Literal
from pymongo.errors import DuplicateKeyError
from decimal import Decimal , ROUND_HALF_UP

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")

@app.on_event("startup")
def create_indexes():
    db.employees.create_index("emp_code", unique=True)
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str, shift_end: str | None = None) -> int:
    if punch_in.tzinfo is None:
        punch_in = punch_in.replace(tzinfo=timezone.utc)
    ist = punch_in.astimezone(IST).replace(microsecond=0)
    h, m = map(int, shift_start.split(":"))
    start = ist.replace(hour=h, minute=m, second=0, microsecond=0)
    if shift_end is not None and shift_end <= shift_start:
        eh, em = map(int, shift_end.split(":"))
        if (ist.hour, ist.minute, ist.second) < (eh, em, 0):
            start -= timedelta(days=1)
    diff_sec = (ist - start).total_seconds()
    if diff_sec <= 10*60:
        return 0
    return int(diff_sec // 60)


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = int((punch_out - punch_in).total_seconds())
    return float((Decimal(seconds)/Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def compute_overtime(punch_out: datetime, shift_end: str, date_str: str, shift_start:str | None = None) -> int:
    if punch_out.tzinfo is None:
        punch_out = punch_out.replace(tzinfo=timezone.utc)
    punch_ist = punch_out.astimezone(IST).replace(microsecond=0)
    y , mo , d = map(int ,date_str.split("-"))
    eh , em = map(int, shift_end.split(":"))
    shift_end_ist = datetime(y , mo, d , eh , em , 0 , tzinfo=IST)
    if shift_start is not None and shift_start >= shift_end:
        shift_end_ist += timedelta(days=1)
    mins =int((punch_ist - shift_end_ist).total_seconds() // 60)
    return mins if mins >=30 else 0

def attandance_date(punch_utc: datetime, shift_start: str, shift_end: str) -> str:
    ist = punch_utc.astimezone(IST)
    if shift_end <= shift_start:
        eh , em = map(int, shift_end.split(":"))
        if(ist.hour , ist.minute, ist.second) < (eh, em, 0):
            return (ist.date() - timedelta(days=1)).isoformat()
    return ist.date().isoformat()

def dt_to_ms(dt: datetime)-> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo = timezone.utc)
    return int(dt.timestamp() * 1000)
    

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r'^EMP\d{4,6}$')
    name: str = Field(min_length= 1 ,max_length=100)
    email: str = Field(max_length= 120 , pattern= r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
    department: str = Field(min_length= 1, max_length=50)
    shift_start: str = Field(default = "09:30", pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    shift_end: str = Field (default ="18:30", pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    joined_on: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    
    @field_validator("joined_on")
    @classmethod
    def validate_joined_date(cls, v):
        dt.fromisoformat(v)
        return v
    
    @model_validator(mode="after")
    def check_shift_val(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift values should be different")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = Field(default=None, ge=100000000000, le=4102444800000)
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "mongodb not reachable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now()
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    q = {}
    if department:
        q["department"] = department
    skip = (page-1) * page_size 
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).skip(skip).limit(page_size).sort("emp_code", 1))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404 , "unknown employeeee")
    if body.punched_at is not None:
        truncted_ms = (body.punched_at // 1000) * 1000
        ts = datetime.fromtimestamp(truncted_ms / 1000 , tz = timezone.utc)
    else:
        ts = datetime.now(timezone.utc).replace(microsecond=0)
    
    d = attandance_date(ts, emp["shift_start"], emp["shift_end"])
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"], emp["shift_end"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    doc.pop("_id", None)
    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": dt_to_ms(doc["punch_in"]),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": doc["late_minutes"],
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1 , le=100),
):
    if date_from is not None:
        dt.fromisoformat(date_from)
    if date_to is not None:
            dt.fromisoformat(date_to)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422 , "date_from can not be greater than date_to")
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    total = db.attendance_logs.count_documents(q)
    cur = db.attendance_logs.find(q, {"_id" : 0}).sort([("date", -1), ("emp_code", 1)]).skip((page - 1) * page_size).limit(page_size)
    items = []
    for d in cur:
        d.pop("_id", None); d.pop("id", None)
        d["punch_in"] = None if d.get("punch_in") is None else dt_to_ms(d["punch_in"])
        d["punch_out"] = None if d.get("punch_out") is None else dt_to_ms(d["punch_out"])
        d["half_day"] = d.get("half_day", False) or False
        d["history"] = d.get("history", []) or []
        d["late_minutes"] = d.get("late_minutes", 0) or 0
        d["overtime_minutes"] = d.get("overtime_minutes", 0) or 0
        items.append(d)
    return {"items": items, "total": total, "page": page, "page_size": page_size}



# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #

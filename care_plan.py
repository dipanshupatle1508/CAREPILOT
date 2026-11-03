"""
SHARED CARE PLAN: the single source of truth every agent reads from and writes to.
SQLite here for zero-setup demos; the SQL is plain enough to port to PostgreSQL.
"""
import json
import sqlite3
from datetime import datetime, timedelta

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients(id TEXT PRIMARY KEY, profile TEXT);
CREATE TABLE IF NOT EXISTS medications(id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT, name TEXT,
    dose TEXT, times TEXT, notes TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS dose_log(id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT, ts TEXT, name TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS vitals(id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT, ts TEXT, systolic REAL,
    diastolic REAL, glucose REAL, spo2 REAL, heart_rate REAL, temp_c REAL);
CREATE TABLE IF NOT EXISTS symptoms(id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT, ts TEXT, symptom TEXT,
    symptom_en TEXT, severity REAL, duration TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS risk_history(id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT, ts TEXT, level TEXT,
    score REAL, reasons TEXT, red_flags TEXT);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT, ts TEXT,
    audience TEXT, level TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS summaries(id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT, ts TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT, ts TEXT, agent TEXT,
    type TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS friendships(id INTEGER PRIMARY KEY AUTOINCREMENT, a TEXT, b TEXT, status TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS water_log(id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT, ts TEXT, ml REAL);
"""

VITAL_KEYS = ("systolic", "diastolic", "glucose", "spo2", "heart_rate", "temp_c")


def _conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _q(sql, params=()):
    with _conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def _x(sql, params=()):
    with _conn() as c:
        c.execute(sql, params)


def _num(v):
    try:
        return None if v in (None, "", "null") else float(v)
    except (TypeError, ValueError):
        return None


# ---- profile ---------------------------------------------------------------
def upsert_patient(pid: str, profile: dict):
    _x("INSERT OR REPLACE INTO patients(id, profile) VALUES (?, ?)", (pid, json.dumps(profile, ensure_ascii=False)))


def get_profile(pid: str) -> dict:
    rows = _q("SELECT profile FROM patients WHERE id=?", (pid,))
    return json.loads(rows[0]["profile"]) if rows else {}


def update_profile(pid: str, **fields):
    p = get_profile(pid)
    p.update({k: v for k, v in fields.items() if v})
    upsert_patient(pid, p)


# ---- medicines ---------------------------------------------------------------
def add_medication(pid, name, dose="", times=None, notes=""):
    _x("UPDATE medications SET active=0 WHERE patient_id=? AND lower(name)=lower(?)", (pid, name))
    _x("INSERT INTO medications(patient_id,name,dose,times,notes) VALUES (?,?,?,?,?)",
       (pid, name, dose, json.dumps(times or []), notes))


def remove_medication(pid, name):
    _x("UPDATE medications SET active=0 WHERE patient_id=? AND lower(name)=lower(?)", (pid, name))


def log_dose(pid, name, status, ts=None):
    _x("INSERT INTO dose_log(patient_id,ts,name,status) VALUES (?,?,?,?)", (pid, ts or _now(), name, status))


def adherence(pid, last_n=20):
    """% of recent scheduled doses actually taken (taken / [taken+skipped+missed]); None if no data yet."""
    rows = _q("SELECT status FROM dose_log WHERE patient_id=? ORDER BY id DESC LIMIT ?", (pid, last_n))
    if not rows:
        return None
    return round(100 * sum(1 for r in rows if r["status"] == "taken") / len(rows))


def missed_doses(pid, now=None, grace_min=60, since=None):
    """Doses scheduled today that are >= grace_min overdue with no taken/skipped/missed entry logged.
    `since`: ignore doses scheduled before this datetime (e.g. when monitoring started), so starting the
    app at 3 pm does not instantly flag the 8 am dose as missed."""
    now = now or datetime.now()
    missed = []
    for m in _q("SELECT name,dose,times FROM medications WHERE patient_id=? AND active=1", (pid,)):
        for t in json.loads(m["times"] or "[]"):
            h, mi = (int(x) for x in t.split(":"))
            sched = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            if now - sched < timedelta(minutes=grace_min):
                continue                                   # not due yet / still within grace period
            if since is not None and sched < since:
                continue                                   # happened before we started watching
            since = (sched - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
            handled = _q("SELECT 1 FROM dose_log WHERE patient_id=? AND lower(name)=lower(?) AND ts>=? LIMIT 1",
                         (pid, m["name"], since))
            if not handled:
                missed.append({"name": m["name"], "dose": m["dose"], "scheduled": t})
    return missed


def due_medications(pid, hhmm: str) -> list:
    meds = _q("SELECT name,dose,times FROM medications WHERE patient_id=? AND active=1", (pid,))
    return [m for m in meds if hhmm in json.loads(m["times"] or "[]")]


# ---- measurements & symptoms -----------------------------------------------------
def add_vital(pid, **kw):
    vals = {k: _num(kw.get(k)) for k in VITAL_KEYS}
    if all(v is None for v in vals.values()):
        return
    _x("INSERT INTO vitals(patient_id,ts,systolic,diastolic,glucose,spo2,heart_rate,temp_c) VALUES (?,?,?,?,?,?,?,?)",
       (pid, _now(), *[vals[k] for k in VITAL_KEYS]))


def add_symptom(pid, symptom, symptom_en="", severity=None, duration="", notes=""):
    _x("INSERT INTO symptoms(patient_id,ts,symptom,symptom_en,severity,duration,notes) VALUES (?,?,?,?,?,?,?)",
       (pid, _now(), symptom, symptom_en or symptom, _num(severity), duration or "", notes or ""))


# ---- risk / notifications / summaries / events -------------------------------------
def save_risk(pid, level, score, reasons, red_flags):
    _x("INSERT INTO risk_history(patient_id,ts,level,score,reasons,red_flags) VALUES (?,?,?,?,?,?)",
       (pid, _now(), level, score, json.dumps(reasons, ensure_ascii=False), json.dumps(red_flags, ensure_ascii=False)))


def add_notification(pid, audience, level, message):
    _x("INSERT INTO notifications(patient_id,ts,audience,level,message) VALUES (?,?,?,?,?)",
       (pid, _now(), audience, level, message))


def save_summary(pid, text):
    _x("INSERT INTO summaries(patient_id,ts,text) VALUES (?,?,?)", (pid, _now(), text))


def log_event(pid, agent, type_, payload):
    _x("INSERT INTO events(patient_id,ts,agent,type,payload) VALUES (?,?,?,?,?)",
       (pid, _now(), agent, type_, json.dumps(payload, ensure_ascii=False)))


def recent_chat(pid, n=6) -> list:
    rows = _q("SELECT payload FROM events WHERE patient_id=? AND type='chat' ORDER BY id DESC LIMIT ?", (pid, n))
    return [json.loads(r["payload"]) for r in reversed(rows)]


def notifications(pid, limit=10) -> list:
    return _q("SELECT ts,audience,level,message FROM notifications WHERE patient_id=? ORDER BY id DESC LIMIT ?",
              (pid, limit))


# ---- the snapshot every agent reads --------------------------------------------------
def snapshot(pid: str) -> dict:
    return {
        "profile": get_profile(pid),
        "medications": _q("SELECT name,dose,times,notes FROM medications WHERE patient_id=? AND active=1", (pid,)),
        "medication_adherence_pct": adherence(pid),
        "recent_doses": _q("SELECT ts,name,status FROM dose_log WHERE patient_id=? ORDER BY id DESC LIMIT 10", (pid,)),
        "recent_vitals": _q("SELECT * FROM vitals WHERE patient_id=? ORDER BY id DESC LIMIT 5", (pid,)),
        "recent_symptoms": _q("SELECT ts,symptom_en,severity,duration,notes FROM symptoms WHERE patient_id=? "
                              "ORDER BY id DESC LIMIT 10", (pid,)),
        "recent_risk": _q("SELECT ts,level,score,reasons FROM risk_history WHERE patient_id=? ORDER BY id DESC LIMIT 3",
                          (pid,)),
        "last_summary": (_q("SELECT ts,text FROM summaries WHERE patient_id=? ORDER BY id DESC LIMIT 1", (pid,)) or [None])[0],
    }


# ---- dashboard helpers -------------------------------------------------------------------
def vitals_history(pid, n=20) -> list:
    rows = _q("SELECT ts,systolic,diastolic,glucose,spo2,heart_rate,temp_c FROM vitals WHERE patient_id=? "
              "ORDER BY id DESC LIMIT ?", (pid, n))
    return list(reversed(rows))


def risk_history(pid, n=15) -> list:
    rows = _q("SELECT ts,level,score,reasons,red_flags FROM risk_history WHERE patient_id=? ORDER BY id DESC LIMIT ?",
              (pid, n))
    for r in rows:
        r["reasons"] = json.loads(r["reasons"] or "[]")
        r["red_flags"] = json.loads(r["red_flags"] or "[]")
    return rows


def latest_event(pid, agent, type_):
    rows = _q("SELECT ts,payload FROM events WHERE patient_id=? AND agent=? AND type=? ORDER BY id DESC LIMIT 1",
              (pid, agent, type_))
    return {"ts": rows[0]["ts"], **json.loads(rows[0]["payload"])} if rows else None


def reset_all():
    """Clear every table (used by the dashboard's Reset button). Rows are deleted rather than the file,
    because deleting an open SQLite file fails on Windows."""
    with _conn() as c:
        for t in ("patients", "medications", "dose_log", "vitals", "symptoms", "risk_history",
                  "notifications", "summaries", "events", "friendships", "water_log"):
            c.execute(f"DELETE FROM {t}")


# ---- water, daily profile and friends ------------------------------------------------
DEFAULT_WATER_GOAL_ML = 2000


def log_water(pid, ml):
    ml = _num(ml)
    if ml and 0 < ml <= 2000:
        _x("INSERT INTO water_log(patient_id,ts,ml) VALUES (?,?,?)", (pid, _now(), ml))
        return True
    return False


def water_today(pid) -> int:
    start = datetime.now().strftime("%Y-%m-%d 00:00:00")
    rows = _q("SELECT COALESCE(SUM(ml),0) AS t FROM water_log WHERE patient_id=? AND ts>=?", (pid, start))
    return int(rows[0]["t"])


def daily_profile(pid, now=None, grace_min=60) -> dict:
    """What a friend is allowed to see: today's medicine status, water vs goal, last check-in. Nothing else."""
    now = now or datetime.now()
    day_end = now.replace(hour=23, minute=59, second=59).strftime("%Y-%m-%d %H:%M:%S")
    meds = []
    for m in _q("SELECT name,dose,times FROM medications WHERE patient_id=? AND active=1", (pid,)):
        slots = sorted(json.loads(m["times"] or "[]"))
        for i, t in enumerate(slots):
            h, mi = (int(x) for x in t.split(":"))
            sched = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            lo = (sched - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
            hi = day_end
            if i + 1 < len(slots):                       # a dose logged for the next slot is not this slot's dose
                h2, m2 = (int(x) for x in slots[i + 1].split(":"))
                hi = (now.replace(hour=h2, minute=m2, second=0, microsecond=0)
                      - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
            row = _q("SELECT status FROM dose_log WHERE patient_id=? AND lower(name)=lower(?) AND ts>=? AND ts<? "
                     "ORDER BY id DESC LIMIT 1", (pid, m["name"], lo, hi))
            if row:
                status = "taken" if row[0]["status"] == "taken" else "missed"
            elif now - sched >= timedelta(minutes=grace_min):
                status = "missed"
            else:
                status = "pending"
            meds.append({"name": f"{m['name']} {m['dose'] or ''} ({t})".replace("  ", " "), "status": status,
                         "_t": t})
    meds.sort(key=lambda x: x["_t"])
    for x in meds:
        x.pop("_t")
    last = _q("SELECT ts FROM events WHERE patient_id=? AND type='chat' ORDER BY id DESC LIMIT 1", (pid,))
    goal = get_profile(pid).get("water_goal_ml") or DEFAULT_WATER_GOAL_ML
    return {"medicines": meds, "water_ml": water_today(pid), "water_goal_ml": goal,
            "last_checkin": last[0]["ts"] if last else None}


def find_patient(identifier):
    """Match an exact patient ID (any case) or a phone number stored in the profile."""
    ident = str(identifier or "").strip()
    if not ident:
        return None
    digits = "".join(ch for ch in ident if ch.isdigit() or ch == "+")
    for r in _q("SELECT id, profile FROM patients"):
        phone = "".join(ch for ch in str(json.loads(r["profile"] or "{}").get("phone", "")) if ch.isdigit() or ch == "+")
        if r["id"].lower() == ident.lower() or (phone and phone == digits):
            return r["id"]
    return None


def _name(pid):
    return get_profile(pid).get("name") or pid


def request_friend(pid, identifier) -> dict:
    other = find_patient(identifier)
    if not other:
        return {"ok": False, "message": "No patient found with that ID or phone number."}
    if other == pid:
        return {"ok": False, "message": "That is your own ID."}
    rows = _q("SELECT id,a,status FROM friendships WHERE (a=? AND b=?) OR (a=? AND b=?)", (pid, other, other, pid))
    if rows:
        r = rows[0]
        if r["status"] == "accepted":
            return {"ok": True, "message": f"You and {_name(other)} are already friends."}
        if r["a"] == pid:
            return {"ok": True, "message": f"You already sent {_name(other)} a request."}
        _x("UPDATE friendships SET status='accepted' WHERE id=?", (r["id"],))   # they had asked you first
        return {"ok": True, "message": f"You are now friends with {_name(other)}."}
    _x("INSERT INTO friendships(a,b,status,ts) VALUES (?,?,?,?)", (pid, other, "pending", _now()))
    return {"ok": True, "message": f"Request sent to {_name(other)}."}


def respond_friend(pid, req_id, accept) -> bool:
    rows = _q("SELECT id FROM friendships WHERE id=? AND b=? AND status='pending'", (req_id, pid))
    if not rows:
        return False
    if accept:
        _x("UPDATE friendships SET status='accepted' WHERE id=?", (req_id,))
    else:
        _x("DELETE FROM friendships WHERE id=?", (req_id,))
    return True


def friends_overview(pid) -> dict:
    friends, incoming, outgoing = [], [], []
    for r in _q("SELECT id,a,b,status FROM friendships WHERE a=? OR b=? ORDER BY id", (pid, pid)):
        other = r["b"] if r["a"] == pid else r["a"]
        if r["status"] == "accepted":
            p = get_profile(other)
            friends.append({"id": other, "name": p.get("name") or other, "age": p.get("age"),
                            "daily": daily_profile(other)})
        elif r["b"] == pid:
            incoming.append({"id": r["id"], "name": _name(other)})
        else:
            outgoing.append({"id": r["id"], "name": _name(other)})
    return {"me": {"id": pid, "name": _name(pid)}, "friends": friends, "incoming": incoming, "outgoing": outgoing}

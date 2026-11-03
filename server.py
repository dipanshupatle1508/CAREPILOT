"""
Web dashboard server (standard library only, so nothing extra to install).

    python server.py                 -> open http://127.0.0.1:8000
    python server.py --port 9000
    MOCK_LLM=1 python server.py      -> dashboard with fake AI replies (offline rehearsal)

It only listens on 127.0.0.1 (your own computer) because the data is health data.
"""
import argparse
import json
import threading
import traceback
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import care_plan
import config
import main as demo            # reuses the demo patient (PID, MUMBAI, seed_demo_patient)

INDEX = Path(__file__).parent / "static" / "index.html"
LOCK = threading.Lock()        # one agent run at a time (SQLite + graph state stay consistent)
APP = None                     # compiled LangGraph, built once at startup
STARTED = datetime.now()       # the scheduler only watches doses scheduled after this moment


# ---------------------------------------------------------------------------
# data for the dashboard
# ---------------------------------------------------------------------------
def state_payload(pid=None) -> dict:
    pid = pid or demo.PID
    snap = care_plan.snapshot(pid)
    now = datetime.now()
    meds = [{**m, "times": json.loads(m["times"] or "[]")} for m in snap["medications"]]
    risk_hist = care_plan.risk_history(pid, 15)
    return {
        "profile": snap["profile"],
        "medications": meds,
        "adherence": snap["medication_adherence_pct"],
        "recent_doses": snap["recent_doses"],
        "vitals": care_plan.vitals_history(pid, 20),
        "symptoms": snap["recent_symptoms"],
        "risk_now": risk_hist[0] if risk_hist else None,
        "risk_history": risk_hist,
        "notifications": care_plan.notifications(pid, 20),
        "summary": snap["last_summary"],
        "emergency": care_plan.latest_event(pid, "emergency", "navigation"),
        "wellness": care_plan.latest_event(pid, "wellness", "tips"),
        "due_now": care_plan.due_medications(pid, now.strftime("%H:%M")),
        "server_time": now.strftime("%H:%M:%S"),
        "mock_ai": config.MOCK_LLM,
        "simulated_availability": config.SIMULATE_AVAILABILITY,
        "default_location": list(demo.MUMBAI),
    }


def _pid(data, query=""):
    """Which patient is asking. Defaults to the demo patient, so the existing dashboard is unchanged."""
    q = parse_qs(query)
    return str(data.get("pid") or (q.get("pid") or [""])[0] or demo.PID)


def _known(pid):
    if not care_plan.get_profile(pid):
        raise LookupError("unknown patient")
    return pid


def run_chat(message: str, lat=None, lon=None, pid=None) -> dict:
    from graph import run_turn
    location = (float(lat), float(lon)) if lat is not None and lon is not None else demo.MUMBAI
    with LOCK:
        r = run_turn(APP, pid or demo.PID, message, location)
    keys = ("final_reply", "reply", "risk", "trace", "emergency", "clinician_summary",
            "caregiver_message", "wellness_tips", "extracted")
    return {k: r.get(k) for k in keys}


def scheduler_loop(stop: threading.Event):
    """Background watcher: if a dose is overdue and nothing was logged, tell the caregiver (once per dose)."""
    from agents import missed_dose_alert
    while not stop.wait(30):
        try:
            with LOCK:
                missed = care_plan.missed_doses(demo.PID, since=STARTED)
                if missed:
                    missed_dose_alert(demo.PID, missed)
        except Exception as e:                    # never let the watcher thread die silently
            print("[scheduler]", type(e).__name__, e)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):           # quieter console
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/state":
                self._json(state_payload(_known(_pid({}, urlparse(self.path).query))))
            elif path in ("/api/friends", "/api/daily"):
                pid = _pid({}, urlparse(self.path).query)
                if not care_plan.get_profile(pid):
                    return self._json({"error": "unknown patient"}, 404)
                self._json(care_plan.friends_overview(pid) if path == "/api/friends" else care_plan.daily_profile(pid))
            else:
                self._json({"error": "not found"}, 404)
        except LookupError as e:
            self._json({"error": str(e)}, 404)
        except Exception as e:
            traceback.print_exc()          # show the full error in the terminal too, not only in the browser
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
            path = self.path.split("?")[0]

            pid = _known(_pid(data, urlparse(self.path).query)) if path in ("/api/chat", "/api/dose", "/api/demo/missed") else None
            if path == "/api/chat":
                message = str(data.get("message", "")).strip()[:2000]
                if not message:
                    return self._json({"error": "empty message"}, 400)
                self._json(run_chat(message, data.get("lat"), data.get("lon"), pid))

            elif path == "/api/dose":
                status = data.get("status")
                name = str(data.get("name", "")).strip()
                if status not in ("taken", "skipped") or not name:
                    return self._json({"error": "need name and status taken|skipped"}, 400)
                with LOCK:
                    care_plan.log_dose(pid, name, status)
                self._json(state_payload(pid))

            elif path == "/api/demo/missed":
                from agents import missed_dose_alert
                meds = care_plan.snapshot(pid)["medications"]
                if not meds:
                    return self._json({"error": "no medicines on record"}, 400)
                with LOCK:
                    missed_dose_alert(pid, [{"name": meds[0]["name"], "dose": meds[0]["dose"],
                                                  "scheduled": "earlier today"}])
                self._json(state_payload(pid))

            elif path in ("/api/friends/request", "/api/friends/respond", "/api/water"):
                pid = _pid(data)
                if not care_plan.get_profile(pid):
                    return self._json({"error": "unknown patient"}, 404)
                with LOCK:
                    if path == "/api/friends/request":
                        r = care_plan.request_friend(pid, data.get("identifier"))
                        return self._json(r, 200 if r["ok"] else 400)
                    if path == "/api/friends/respond":
                        if not care_plan.respond_friend(pid, data.get("id"), bool(data.get("accept"))):
                            return self._json({"error": "no such pending request"}, 400)
                        return self._json(care_plan.friends_overview(pid))
                    if not care_plan.log_water(pid, data.get("ml")):
                        return self._json({"error": "ml must be between 1 and 2000"}, 400)
                    self._json(care_plan.daily_profile(pid))

            elif path == "/api/reset":
                with LOCK:
                    care_plan.reset_all()
                    demo.seed_demo_patient()
                self._json(state_payload())

            else:
                self._json({"error": "not found"}, 404)
        except LookupError as e:
            self._json({"error": str(e)}, 404)
        except Exception as e:
            traceback.print_exc()          # show the full error in the terminal too, not only in the browser
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def main():
    global APP
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if not care_plan.get_profile(demo.PID):
        demo.seed_demo_patient()

    from graph import build_graph                 # fails here, with a clear message, if langgraph is missing
    APP = build_graph()

    stop = threading.Event()
    threading.Thread(target=scheduler_loop, args=(stop,), daemon=True).start()

    url = f"http://127.0.0.1:{args.port}"
    print(f"Dashboard running at {url}   (Ctrl+C to stop)")
    if config.MOCK_LLM:
        print("NOTE: MOCK_LLM=1, so replies are fake. Unset it to use the real models.")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


if __name__ == "__main__":
    main()

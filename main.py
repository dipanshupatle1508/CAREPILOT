"""
Run the demo:        python main.py
Chat interactively:  python main.py --chat           (add --audio file.wav to send a voice note instead)
Fresh database:      python main.py --reset
Personalisation:     python main.py --compare        (same symptom, two different patients; needs no API)
Simulated bed data:  SIMULATE_AVAILABILITY=1 python main.py   (clearly labelled DEMO data)
"""
import argparse
import os
import threading
from datetime import datetime

import care_plan
import config

PID = "ramesh-001"
MUMBAI = (19.0760, 72.8777)   # patient's GPS fix (Mumbai) for the hospital search


def transcribe_audio(path):
    """Voice input is OPTIONAL. It needs stt.py next to this file plus `pip install faster-whisper`.
    Loaded with importlib so the editor does not flag a missing optional module, and so a missing
    file or library gives a clear message instead of a crash."""
    import importlib
    try:
        stt = importlib.import_module("stt")
        return stt.transcribe(path)
    except ModuleNotFoundError as e:
        print(f"Voice input is unavailable: missing module '{e.name}'.")
        if e.name == "stt":
            print("  -> Put stt.py in the same folder as main.py.")
        else:
            print("  -> Run: pip install faster-whisper   (may not support very new Python versions; "
                  "Python 3.12 or 3.13 is safest for this one)")
        return None


def seed_demo_patient():
    care_plan.upsert_patient(PID, {
        "name": "SHARMA JI", "age": 76, "language": "hi",
        "chronic_conditions": ["type 2 diabetes", "hypertension", "coronary artery disease"],
        "allergies": ["penicillin"], "location_text": "Shankar nagar, Nagpur, India",
        "caregiver": {"name": "Priya", "relation": "daughter", "language": "en", "phone": "MOBILE NO."},
        "care_team": [{"name": "Dr. Mehta (demo)", "specialty": "General Physician"},
                      {"name": "Dr. Rao (demo)", "specialty": "Cardiologist"}],
    })
    care_plan.add_medication(PID, "Metformin", "500 mg", ["08:00", "20:00"])
    care_plan.add_medication(PID, "Amlodipine", "5 mg", ["09:00"])
    care_plan.add_medication(PID, "Aspirin", "75 mg", ["21:00"])


def reminder_loop(stop: threading.Event):
    fired = set()
    started = datetime.now()   # only watch doses scheduled from now on
    while not stop.is_set():
        now = datetime.now().strftime("%H:%M")
        for m in care_plan.due_medications(PID, now):
            key = (datetime.now().date(), m["name"], now)
            if key not in fired:
                fired.add(key)
                print(f"\n   MEDICINE REMINDER: time to take {m['name']} {m['dose']}\nYou: ", end="", flush=True)
        missed = care_plan.missed_doses(PID, since=started)
        if missed:                                    # overdue + nothing logged -> proactively tell the caregiver
            from agents import missed_dose_alert
            missed_dose_alert(PID, missed)
        stop.wait(20)


def show(app_result):
    print("\n   --- workflow trace ---")
    for step in app_result["trace"]:
        print("   ->", step)
    r = app_result["risk"]
    print(f"\n   RISK: {r['level']} (score {r['score']}) | " + "; ".join(r["reasons"]))
    print("\n   PATIENT SEES:\n" + "\n".join("   " + l for l in app_result["final_reply"].splitlines()))
    em = app_result.get("emergency")
    if em:
        rec = em["recommendation"]
        print(f"\n   EMERGENCY NAVIGATOR  (risk confidence {r['confidence']}; = input completeness)")
        print(f"   urgency   : {rec['urgency']}")
        print(f"   transport : {rec['transport']}")
        print(f"   go to     : {rec['facility_type']}")
        print(f"   specialist: {rec['specialty']} ({rec['specialty_reason']})")
        if em.get("own_doctor"):
            print(f"   your own  : {em['own_doctor']['name']}")
        print(f"   because   : {'; '.join(rec['personalized_because'])}")
        for f in em["facilities"]:
            print(f"   - {f['name']} ({f['distance_km']} km, {f['estimated_type']}) | {f['availability']['status']}")
        print(f"   call      : {rec['contacts']['emergency']} (emergency), {rec['contacts']['ambulance']}")
    if app_result.get("clinician_summary"):
        print("\n   CLINICIAN SUMMARY:\n" + "\n".join("   " + l for l in app_result["clinician_summary"].splitlines()))
    if app_result.get("caregiver_message"):
        print("\n   CAREGIVER MESSAGE:\n   " + app_result["caregiver_message"])


DEMO = [
    ("1) Routine check-in (expect LOW)", "Good morning! I took my Metformin. Feeling fine today."),
    ("2) Mild symptom + high BP (expect MEDIUM/HIGH)", "मुझे सुबह से सिरदर्द है, BP 165/102 आया है और sugar 210 है।"),
    ("3) Emergency (expect CRITICAL + hospital + cardiologist)", "मुझे सीने में तेज दर्द हो रहा है, 30 मिनट से, BP 190/115"),
]


def compare():
    """Same symptom, different patients -> different recommendations. Pure rules, runs without any API."""
    from navigator import build_recommendation
    from risk import assess
    symptom = [{"symptom": "chest pain", "symptom_en": "chest pain", "severity": 7}]
    patients = {
        "Patient A: age 30, healthy": {"age": 30, "chronic_conditions": []},
        "Patient B: age 75, heart disease + high BP": {
            "age": 75, "chronic_conditions": ["coronary artery disease", "hypertension"]},
    }
    print("Same symptom for both: chest pain, severity 7/10\n")
    for name, prof in patients.items():
        risk = assess(prof, {}, symptom, {})
        rec = build_recommendation(prof, symptom, risk)
        print(f"{name}\n   risk      : {risk['level']}\n   urgency   : {rec['urgency']}\n   transport : {rec['transport']}"
              f"\n   go to     : {rec['facility_type']}\n   specialist: {rec['specialty']} ({rec['specialty_reason']})\n")
    print("Note: chest pain is treated as urgent for EVERYONE (downplaying it is unsafe); personalisation changes"
          " transport, destination type and specialist.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat", action="store_true")
    ap.add_argument("--audio")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args()

    if args.compare:
        compare()
        return

    if args.reset and os.path.exists(config.DB_PATH):
        os.remove(config.DB_PATH)
    if not care_plan.get_profile(PID):
        seed_demo_patient()

    from graph import build_graph, run_turn   # imported here so --help works without langgraph
    app = build_graph()

    if args.audio:
        text = transcribe_audio(args.audio)
        if text is None:
            return
        print("Transcribed:", text)
        show(run_turn(app, PID, text, MUMBAI))
        return

    if args.chat:
        stop = threading.Event()
        threading.Thread(target=reminder_loop, args=(stop,), daemon=True).start()
        print("Type 'quit' to exit.")
        try:
            while True:
                msg = input("You: ").strip()
                if msg.lower() in ("quit", "exit"):
                    break
                if msg:
                    show(run_turn(app, PID, msg, MUMBAI))
        finally:
            stop.set()
        return

    for title, msg in DEMO:
        print("\n" + "=" * 78 + f"\n{title}\nPatient: {msg}\n" + "=" * 78)
        show(run_turn(app, PID, msg, MUMBAI))


if __name__ == "__main__":
    main()

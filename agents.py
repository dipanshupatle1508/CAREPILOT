"""
The five agents + the risk scorer. Each agent is a plain function: (state) -> partial state update.
Every agent READS the shared care plan and WRITES its results back to it.
"""
import json
from datetime import datetime

import care_plan
import config
import memory
import navigator
import tools
from config import FAST_MODEL, REASONING_MODEL
from llm import ask, ask_json
from risk import assess

LANG_NAMES = {"hi": "Hindi", "mr": "Marathi", "en": "English", "ta": "Tamil", "bn": "Bengali", "gu": "Gujarati"}


def _lang(code: str) -> str:
    return LANG_NAMES.get((code or "en").lower(), code or "English")


def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str, indent=1)


# =============================================================================
# 1. PATIENT AGENT: talks to the patient, extracts structured data, remembers
# =============================================================================
PATIENT_PROMPT = """You are a warm, patient health companion for an elderly person. Reply in the SAME language the
patient writes in (Hindi, Marathi, English, Hinglish...). Use short, simple sentences and no medical jargon.

Your jobs: (1) medicine reminders and dose tracking, (2) symptom tracking, (3) multilingual conversation,
(4) use the care-plan snapshot and past memories as ground truth. Never invent facts.

Rules:
- You are NOT a doctor: never diagnose, prescribe, or change doses.
- Ask at most ONE short follow-up question (e.g. severity 1-10 or how long).
- If the message describes chest pain, trouble breathing, stroke signs, fainting, severe bleeding or confusion,
  tell them calmly to call emergency services (112 in India) right away. Say this first.
- Extract numbers ONLY if the patient actually stated them. Never guess vitals.

Return ONLY valid JSON, no other text:
{
 "reply": "<message to the patient in their language>",
 "language": "<ISO code such as hi, mr, en>",
 "symptoms": [{"symptom": "<as said>", "symptom_en": "<English name>", "severity": <1-10 or null>, "duration": "", "notes": ""}],
 "vitals": {"systolic": null, "diastolic": null, "glucose": null, "spo2": null, "heart_rate": null, "temp_c": null},
 "add_medications": [{"name": "", "dose": "", "times": ["HH:MM"], "notes": ""}],
 "dose_log": [{"name": "", "status": "taken|skipped"}],
 "facts": ["<allergy, condition, preference, family detail worth remembering>"]
}"""


def patient_agent(state):
    pid, msg = state["patient_id"], state["user_message"]
    snap = care_plan.snapshot(pid)
    memories = memory.search(pid, msg)
    user = (f"NOW: {datetime.now():%A %Y-%m-%d %H:%M}\n\nCARE PLAN SNAPSHOT:\n{_j(snap)}\n\n"
            f"RELEVANT PAST MEMORIES:\n{_j(memories)}\n\nRECENT CHAT:\n{_j(care_plan.recent_chat(pid))}\n\n"
            f"PATIENT MESSAGE: {msg}")
    data = ask_json(FAST_MODEL, PATIENT_PROMPT, user, role="patient")
    if not data.get("reply"):
        data = {"reply": "Sorry, I did not catch that. Could you say it again?", "symptoms": [], "vitals": {}}

    # ---- write what we learned into the shared care plan + long-term memory ----
    care_plan.update_profile(pid, language=data.get("language"))
    vitals = {k: v for k, v in (data.get("vitals") or {}).items() if v not in (None, "")}
    care_plan.add_vital(pid, **vitals)
    symptoms = data.get("symptoms") or []
    for s in symptoms:
        care_plan.add_symptom(pid, s.get("symptom", ""), s.get("symptom_en", ""), s.get("severity"),
                              s.get("duration", ""), s.get("notes", ""))
    for m in data.get("add_medications") or []:
        if m.get("name"):
            care_plan.add_medication(pid, m["name"], m.get("dose", ""), m.get("times"), m.get("notes", ""))
    for d in data.get("dose_log") or []:
        if d.get("name"):
            care_plan.log_dose(pid, d["name"], d.get("status", "taken"))
    for fact in data.get("facts") or []:
        memory.add(pid, fact, kind="fact")
    memory.add(pid, f"Patient said: {msg}", kind="chat")
    care_plan.log_event(pid, "patient", "chat", {"user": msg, "assistant": data["reply"]})

    return {"reply": data["reply"],
            "extracted": {"symptoms": symptoms, "vitals": vitals, "language": data.get("language") or "en"},
            "trace": ["patient_agent: recorded event"]}


# =============================================================================
# 2. RISK SCORING AGENT: rule-based, explainable
# =============================================================================
def risk_agent(state):
    pid, ex = state["patient_id"], state["extracted"]
    snap = care_plan.snapshot(pid)
    result = assess(snap["profile"], ex["vitals"], ex["symptoms"], snap)
    care_plan.save_risk(pid, result["level"], result["score"], result["reasons"], result["red_flags"])
    return {"risk": result, "trace": [f"risk_agent: {result['level']} (score {result['score']})"]}


# =============================================================================
# 3. EMERGENCY CARE NAVIGATOR: personalised hospital + specialist recommendation
# =============================================================================
EMERGENCY_PROMPT = """You write a short, calm message to an elderly patient (max 5 sentences) in the given language.
Use ONLY the facilities, specialist and contact numbers provided. Never invent hospital names or phone numbers.
Start with the URGENCY instruction exactly as given. Then name the first facility and its distance.
NEVER say that beds, ICU or a doctor are available: availability is not verified.
If the patient's own doctor is listed, mention them by name."""


def _emergency_for_others(state):
    """Emergency data without the patient-facing message (caregiver/clinician write their own text)."""
    em = dict(state.get("emergency") or {})
    em.pop("message", None)
    return em


def emergency_agent(state):
    pid, risk = state["patient_id"], state["risk"]
    profile = care_plan.get_profile(pid)
    symptoms = state["extracted"]["symptoms"]

    lat, lon = state.get("location") or tools.geocode(profile.get("location_text", "")) or (None, None)
    rec = navigator.build_recommendation(profile, symptoms, risk)

    hospitals = tools.find_hospitals(lat, lon, limit=8) if lat is not None else tools.SAMPLE_HOSPITALS
    facilities = navigator.rank_facilities(hospitals, rec)[:3]
    for f in facilities:
        f["availability"] = navigator.availability(f)

    own_doctor = navigator.care_team_match(profile, rec["specialty"])
    nearby = tools.find_specialists(lat, lon, rec["specialty"]) if lat is not None else []
    if lat is not None:
        tools.write_map_html("hospital_map.html", lat, lon, facilities)

    data = {"recommendation": rec, "facilities": facilities, "own_doctor": own_doctor, "nearby_specialists": nearby}
    user = (f"LANGUAGE: {_lang(profile.get('language'))}\nURGENCY: {rec['urgency']}\n"
            f"TRANSPORT: {rec['transport']}\nFACILITY TYPE NEEDED: {rec['facility_type']}\n"
            f"SPECIALIST: {rec['specialty']} ({rec['specialty_reason']})\n"
            f"PATIENT'S OWN DOCTOR FOR THIS: {_j(own_doctor)}\nCONTACT NUMBERS: {_j(rec['contacts'])}\n"
            f"NEAREST FACILITIES: {_j([{k: f[k] for k in ('name', 'distance_km')} for f in facilities])}")
    message = ask(FAST_MODEL, EMERGENCY_PROMPT, user, role="emergency")
    data["message"] = message
    care_plan.log_event(pid, "emergency", "navigation", {k: v for k, v in data.items() if k != "message"})
    return {"emergency": data,
            "trace": [f"emergency_agent: {rec['specialty']}; {len(facilities)} facilities ranked; transport={rec['transport']}"]}


# =============================================================================
# 4. CLINICIAN AGENT: doctor summary + escalation (uses the reasoning model)
# =============================================================================
CLINICIAN_PROMPT = """You are a clinical documentation assistant preparing a hand-off for the treating doctor.
Write a concise SBAR summary (Situation, Background, Assessment, Recommendation) in English from the data below.
Include: trend observations (e.g. repeated symptoms, BP/sugar direction), medication adherence, and the risk
reasons. Do NOT diagnose definitively; phrase the assessment as "possible / consistent with".
Mention medication adherence % if it is below 80. End with one line: ESCALATION: <none | routine review | same-day | immediate> and why. Max 200 words."""


def clinician_agent(state):
    pid, risk = state["patient_id"], state["risk"]
    snap = care_plan.snapshot(pid)
    user = (f"TODAY: {datetime.now():%Y-%m-%d %H:%M}\nCARE PLAN:\n{_j(snap)}\n\nTHIS EVENT: {state['user_message']}\n"
            f"RISK: {_j(risk)}\nEMERGENCY NAVIGATION: {_j(_emergency_for_others(state))}")
    summary = ask(REASONING_MODEL, CLINICIAN_PROMPT, user, role="clinician")
    care_plan.save_summary(pid, summary)
    if risk["level"] in ("HIGH", "CRITICAL"):
        care_plan.add_notification(pid, "clinician", risk["level"], summary)
        tools.send_notification("clinician", f"[{risk['level']}] {summary}")
    return {"clinician_summary": summary, "trace": ["clinician_agent: SBAR summary saved"]}


# =============================================================================
# 5. CAREGIVER AGENT: calm, clear family updates
# =============================================================================
CAREGIVER_PROMPT = """You write a short update (max 4 sentences) to a family caregiver about their elderly relative,
in the caregiver's language. Say what happened, how serious it looks, and what the caregiver should do now.
Be calm and factual: no diagnosis, no blame, no medical jargon. If CRITICAL, start with the action to take.
Only mention hospitals that appear in the data."""


def caregiver_agent(state):
    pid, risk = state["patient_id"], state["risk"]
    profile = care_plan.get_profile(pid)
    cg = profile.get("caregiver", {})
    user = (f"CAREGIVER: {cg.get('name', 'Family member')} ({cg.get('relation', '')}); "
            f"LANGUAGE: {_lang(cg.get('language'))}\nPATIENT: {profile.get('name')}, age {profile.get('age')}\n"
            f"WHAT THE PATIENT SAID: {state['user_message']}\nRISK: {_j(risk)}\n"
            f"EMERGENCY INFO (hospital availability is NOT verified): {_j(_emergency_for_others(state))}")
    message = ask(FAST_MODEL, CAREGIVER_PROMPT, user, role="caregiver")
    care_plan.add_notification(pid, "caregiver", risk["level"], message)
    tools.send_notification(f"caregiver {cg.get('name', '')}", message)
    return {"caregiver_message": message, "trace": ["caregiver_agent: family notified"]}


def missed_dose_alert(pid, missed):
    """Proactive: called by the scheduler when a dose is overdue; tells the caregiver (no patient message involved)."""
    profile = care_plan.get_profile(pid)
    cg = profile.get("caregiver", {})
    user = (f"CAREGIVER: {cg.get('name', 'Family member')} ({cg.get('relation', '')}); LANGUAGE: {_lang(cg.get('language'))}\n"
            f"PATIENT: {profile.get('name')}\nOVERDUE MEDICINES (no dose logged): {_j(missed)}\n"
            f"ADHERENCE: {care_plan.adherence(pid)}%\nTask: gently ask the caregiver to check in with the patient. "
            f"Do not alarm. Max 3 sentences.")
    msg = ask(FAST_MODEL, CAREGIVER_PROMPT, user, role="caregiver")
    care_plan.add_notification(pid, "caregiver", "MEDIUM", msg)
    tools.send_notification(f"caregiver {cg.get('name', '')}", msg)
    for m in missed:                       # log so we alert only once per dose, and adherence reflects it
        care_plan.log_dose(pid, m["name"], "missed")
    return msg


# =============================================================================
# 6. WELLNESS AGENT: routine, walking, hydration, social engagement
# =============================================================================
WELLNESS_PROMPT = """You are a gentle wellness coach for an elderly patient. Give exactly 3 very short tips
(one line each) in the patient's language for today: movement/walking, hydration, and social connection.
Adapt to their conditions and recent vitals (e.g. shorter walks if BP is high; sugar-friendly snack if diabetic).
If they reported any symptom, recommend rest instead of exercise. No medical claims."""


def wellness_agent(state):
    pid = state["patient_id"]
    snap = care_plan.snapshot(pid)
    user = (f"LANGUAGE: {_lang(snap['profile'].get('language'))}\nTIME: {datetime.now():%A %H:%M}\n"
            f"PROFILE: {_j(snap['profile'])}\nRECENT VITALS: {_j(snap['recent_vitals'][:2])}\n"
            f"SYMPTOMS TODAY: {_j(state['extracted']['symptoms'])}\nRISK: {state['risk']['level']}")
    tips = ask(FAST_MODEL, WELLNESS_PROMPT, user, role="wellness")
    care_plan.log_event(pid, "wellness", "tips", {"tips": tips})
    return {"wellness_tips": tips, "trace": ["wellness_agent: daily tips generated"]}


# =============================================================================
# Final step: assemble what the PATIENT sees
# =============================================================================
def finalize(state):
    parts = [state["reply"]]
    if state.get("emergency"):
        parts.append(state["emergency"]["message"])
    if state.get("wellness_tips"):
        parts.append(state["wellness_tips"])
    return {"final_reply": "\n\n".join(parts)}

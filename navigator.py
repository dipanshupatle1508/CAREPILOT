"""
PERSONALIZED EMERGENCY RECOMMENDATION ENGINE (pure rules, no LLM, so it is explainable and testable).

   current symptoms + patient history + chronic diseases + vitals + age + location  ->  recommendation

Same symptom, different patient, different outcome: destination type, transport, urgency and specialist
all change with who the patient is.
"""
import hashlib
import re

import config

# ---------------------------------------------------------------------------
# Specialist choice
# ---------------------------------------------------------------------------
def pick_specialist(profile: dict, symptoms: list, risk: dict) -> dict:
    text = " ".join(f"{s.get('symptom_en', '')} {s.get('symptom', '')}" for s in symptoms).lower()
    flags = risk.get("red_flags", [])
    conditions = " ".join(profile.get("chronic_conditions", [])).lower()
    reasons = " ".join(risk.get("reasons", [])).lower()

    if "chest pain" in flags or "chest" in text or "palpitation" in text:
        return {"specialty": "Cardiologist",
                "why": "chest symptoms" + (" with known heart disease" if "heart" in conditions or "coronary" in conditions else "")}
    if "stroke signs" in flags or "sudden confusion" in flags or "dizz" in text or "numb" in text:
        return {"specialty": "Neurologist", "why": "possible neurological symptoms"}
    if "breathing difficulty" in flags or "spo2" in reasons or "cough" in text:
        return {"specialty": "Pulmonologist", "why": "breathing / oxygen concern"}
    severe_sugar = any(w in reasons for w in ("hypoglycaemia", "very high"))
    if severe_sugar or ("diabet" in conditions and ("thirst" in text or "blurred" in text)):
        return {"specialty": "Endocrinologist / Diabetologist", "why": "blood sugar emergency" if severe_sugar
                else "diabetes-related symptoms"}
    if "bp" in reasons and ("hypertens" in conditions or "bp" in reasons and "markedly" in reasons):
        extra = " (blood sugar is also high)" if "glucose" in reasons else ""
        return {"specialty": "Cardiologist / Physician", "why": "uncontrolled blood pressure" + extra}
    if "glucose" in reasons:
        return {"specialty": "Endocrinologist / Diabetologist", "why": "blood sugar abnormality"}
    if (profile.get("age") or 0) >= 65:
        return {"specialty": "Geriatrician / General Physician", "why": "age-appropriate comprehensive review"}
    return {"specialty": "General Physician", "why": "no specific system implicated"}


UNIT_FOR = {
    "Cardiologist": "cardiac-capable emergency centre",
    "Neurologist": "stroke-capable emergency centre",
    "Pulmonologist": "emergency centre with oxygen / ICU support",
    "Endocrinologist / Diabetologist": "emergency department able to stabilise blood sugar",
}


# ---------------------------------------------------------------------------
# The personalised recommendation
# ---------------------------------------------------------------------------
def build_recommendation(profile: dict, symptoms: list, risk: dict) -> dict:
    age = profile.get("age") or 0
    conditions = profile.get("chronic_conditions", [])
    level = risk["level"]
    spec = pick_specialist(profile, symptoms, risk)
    specialty = spec["specialty"]
    vulnerable = age >= 60 or bool(conditions)

    why = []
    if age:
        why.append(f"age {age}")
    if conditions:
        why.append("chronic conditions: " + ", ".join(conditions))
    if risk.get("red_flags"):
        why.append("red-flag symptoms: " + ", ".join(risk["red_flags"]))
    if risk.get("critical"):
        why.append("critical readings: " + ", ".join(risk["critical"]))

    if level == "CRITICAL" and vulnerable:
        urgency = "Call an ambulance NOW. Do not travel alone or drive."
        transport = "ambulance"
        facility = UNIT_FOR.get(specialty, "full-service emergency department")
    elif level == "CRITICAL":
        urgency = "Go to the nearest emergency department NOW, accompanied by someone."
        transport = "accompanied by a family member or friend; call an ambulance if it worsens"
        facility = "nearest emergency department"
    elif level == "HIGH":
        urgency = "See a doctor TODAY. Go to an emergency department immediately if symptoms worsen."
        transport = "accompanied by family (taxi or own vehicle)"
        facility = f"hospital with {specialty.lower()} services (OPD today; emergency department if worsening)"
    elif level == "MEDIUM":
        urgency = "Book a doctor visit within 1-2 days and keep monitoring."
        transport = "routine"
        facility = f"{specialty.lower()} or primary-care clinic"
    else:
        urgency, transport, facility = "No action needed; continue routine care.", "routine", "none"

    return {"level": level, "urgency": urgency, "transport": transport, "facility_type": facility,
            "specialty": specialty, "specialty_reason": spec["why"], "personalized_because": why, "age": age,
            "contacts": config.EMERGENCY_CONTACTS}


# ---------------------------------------------------------------------------
# Facility ranking: distance + facility type + specialty match (all heuristic, labelled as estimates)
# ---------------------------------------------------------------------------
CAPABLE_WORDS = ("emergency", "trauma", "multi", "super", "medical college", "institute", "general", "civil")
CLINIC_WORDS = ("clinic", "nursing home", "dispensary", "polyclinic", "diagnostic")
SPECIALTY_WORDS = {
    "Cardiologist": ("cardi", "heart"), "Cardiologist / Physician": ("cardi", "heart"), "Neurologist": ("neuro", "brain"),
    "Pulmonologist": ("pulmo", "chest", "lung", "respir"),
    "Endocrinologist / Diabetologist": ("diabet", "endocrin"),
}


# Hospitals whose NAME says they serve a narrow group: poor choice for a general adult emergency.
NARROW_RE = re.compile(r"\b(child|pediatric|paediatric|maternity|women|eye|dental|skin|ivf|fertility|veterinary|animal)",
                       re.IGNORECASE)


def rank_facilities(hospitals: list, rec: dict) -> list:
    spec_words = SPECIALTY_WORDS.get(rec["specialty"], ())
    adult = (rec.get("age") or 0) >= 18
    ranked = []
    for h in hospitals:
        name = h["name"].lower()
        score, notes = h.get("distance_km", 99), []
        narrow = bool(NARROW_RE.search(name)) and rec["level"] != "LOW" and (adult or not rec.get("age"))
        capable = any(w in name for w in CAPABLE_WORDS) and not narrow
        clinic = any(w in name for w in CLINIC_WORDS)
        if narrow:
            score += 10; notes.append("name suggests a children's / women's / single-specialty hospital; "
                                      "less suitable for a general adult emergency")
        if rec["level"] in ("CRITICAL", "HIGH") and capable:
            score -= 2; notes.append("name suggests a larger / emergency-capable hospital")
        if rec["level"] == "CRITICAL" and clinic:
            score += 5; notes.append("name suggests a small clinic; less suitable for emergencies")
        if any(w in name for w in spec_words):
            score -= 3; notes.append(f"name suggests {rec['specialty'].lower()} services")
        ranked.append({**h, "rank_score": round(score, 1), "match_notes": notes,
                       "estimated_type": "narrow-specialty (check suitability)" if narrow else
                       ("clinic-type" if clinic else ("likely emergency-capable" if capable else "unclassified"))})
    ranked.sort(key=lambda x: x["rank_score"])
    return ranked


def availability(h: dict) -> dict:
    """Bed/ICU availability. We have NO verified live source, so by default we say so."""
    if not config.SIMULATE_AVAILABILITY:
        return {"status": "Not verified. Call ahead (112 / hospital front desk).", "simulated": False}
    x = int(hashlib.md5(h["name"].encode()).hexdigest(), 16)
    er = ["accepting patients", "busy"][x % 2]
    icu = ["beds likely available", "limited"][(x >> 3) % 2]
    return {"status": f"SIMULATED DEMO DATA (not real): ER {er}; ICU {icu}", "simulated": True}


def care_team_match(profile: dict, specialty: str):
    """The patient's own treating doctors come first: continuity of care beats a stranger."""
    tokens = [t.strip().lower()[:5] for t in specialty.replace("/", " ").split() if len(t.strip()) > 3]
    for doc in profile.get("care_team", []):
        if any(t in doc.get("specialty", "").lower() for t in tokens):
            return doc
    return None

"""
RISK SCORING AGENT: deliberately rule-based (no LLM), so every decision is explainable and testable.
Combines: vitals + symptoms + age + chronic diseases + history (repeats, medication adherence).
Output levels: LOW / MEDIUM / HIGH / CRITICAL, with a data-completeness "confidence".
Thresholds are simplified for a demo; a clinician should review them before any real use.
"""

RED_FLAGS = {
    "chest pain": ["chest pain", "chest tightness", "chest pressure", "सीने में दर्द", "सीने में जकड़न"],
    "breathing difficulty": ["shortness of breath", "difficulty breathing", "breathless", "can't breathe",
                             "सांस लेने में तकलीफ", "साँस लेने में तकलीफ", "सांस फूल"],
    "stroke signs": ["slurred speech", "face drooping", "facial droop", "arm weakness", "sudden weakness",
                     "one side weak", "लकवा"],
    "loss of consciousness": ["fainted", "unconscious", "passed out", "blackout", "बेहोश"],
    "severe bleeding": ["severe bleeding", "vomiting blood", "blood in stool", "coughing blood"],
    "sudden confusion": ["sudden confusion", "very confused", "not responding"],
}

LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def assess(profile: dict, vitals: dict, symptoms: list, history: dict) -> dict:
    score, reasons, red, critical = 0, [], [], []
    conditions = " ".join(profile.get("chronic_conditions", [])).lower()
    age = profile.get("age") or 0

    sbp, dbp = _f(vitals.get("systolic")), _f(vitals.get("diastolic"))
    glu, spo2 = _f(vitals.get("glucose")), _f(vitals.get("spo2"))
    hr, temp = _f(vitals.get("heart_rate")), _f(vitals.get("temp_c"))

    # --- vitals ---------------------------------------------------------------
    if sbp is not None and dbp is not None:
        if sbp >= 180 or dbp >= 120:
            score += 5; reasons.append(f"BP {sbp:.0f}/{dbp:.0f} is in the hypertensive-crisis range")
        elif sbp >= 160 or dbp >= 100:
            score += 3; reasons.append(f"BP {sbp:.0f}/{dbp:.0f} is markedly high")
        elif sbp >= 140 or dbp >= 90:
            score += 1; reasons.append(f"BP {sbp:.0f}/{dbp:.0f} is above target")
        elif sbp < 90:
            score += 4; reasons.append(f"BP {sbp:.0f}/{dbp:.0f} is low (possible shock/dehydration)")
            critical.append("very low blood pressure")
        if "hypertens" in conditions and sbp >= 160:
            score += 1; reasons.append("known hypertension with uncontrolled reading")
    if glu is not None:
        if glu < 54:
            score += 6; reasons.append(f"glucose {glu:.0f} mg/dL: severe hypoglycaemia")
            critical.append("severe hypoglycaemia")
        elif glu < 70:
            score += 4; reasons.append(f"glucose {glu:.0f} mg/dL: hypoglycaemia")
        elif glu > 300:
            score += 4; reasons.append(f"glucose {glu:.0f} mg/dL: very high")
        elif glu > 180:
            score += 2; reasons.append(f"glucose {glu:.0f} mg/dL: high")
        if "diabet" in conditions and glu > 250:
            score += 1; reasons.append("known diabetes with very high sugar")
    if spo2 is not None:
        if spo2 < 90:
            score += 6; reasons.append(f"SpO2 {spo2:.0f}% is critically low")
            critical.append("critically low oxygen")
        elif spo2 < 94:
            score += 3; reasons.append(f"SpO2 {spo2:.0f}% is low")
    if hr is not None:
        if hr > 120 or hr < 45:
            score += 3; reasons.append(f"heart rate {hr:.0f} bpm is abnormal")
        elif hr > 100:
            score += 1; reasons.append(f"heart rate {hr:.0f} bpm is elevated")
    if temp is not None:
        if temp >= 39:
            score += 3; reasons.append(f"temperature {temp:.1f} C: high fever")
        elif temp >= 38:
            score += 1; reasons.append(f"temperature {temp:.1f} C: fever")

    # --- symptoms ---------------------------------------------------------------
    for s in symptoms:
        text = f"{s.get('symptom', '')} {s.get('symptom_en', '')} {s.get('notes', '')}".lower()
        for flag, keywords in RED_FLAGS.items():
            if any(k in text for k in keywords) and flag not in red:
                red.append(flag)
                reasons.append(f"red-flag symptom: {flag}")
        sev = _f(s.get("severity"))
        if sev is not None and sev >= 8:
            score += 2; reasons.append(f"{s.get('symptom_en') or s.get('symptom')} reported as severe ({sev:.0f}/10)")
        elif sev is not None and sev >= 5:
            score += 1
        elif s.get("symptom_en") or s.get("symptom"):
            score += 1
    if "heart" in conditions or "cardiac" in conditions:
        if "chest pain" in red or (sbp or 0) >= 160:
            score += 1; reasons.append("known heart disease raises concern")

    # --- history ---------------------------------------------------------------
    adh = history.get("medication_adherence_pct")
    if adh is not None and adh < 60:
        score += 2; reasons.append(f"medication adherence is only {adh}%")
    elif adh is not None and adh < 80:
        score += 1; reasons.append(f"medication adherence is {adh}%")
    past = [x.get("symptom_en") for x in history.get("recent_symptoms", [])]
    for s in symptoms:
        name = s.get("symptom_en")
        if name and past.count(name) >= 3:
            score += 1; reasons.append(f"'{name}' has been reported repeatedly")
    last5 = history.get("recent_doses", [])[:5]
    if sum(1 for d in last5 if d.get("status") == "skipped") >= 2:
        score += 1; reasons.append("multiple recently skipped doses")

    # --- age modifier (only amplifies an existing concern) --------------------------
    if age >= 75 and score > 0:
        score += 1; reasons.append(f"age {age}: higher vulnerability")

    # --- level -----------------------------------------------------------------------
    # A red-flag symptom or one critical vital is an emergency on its own. Stacked moderate findings
    # alone need a much higher score, so "headache + high-ish BP + high-ish sugar" lands on HIGH, not CRITICAL.
    if red or critical or score >= 10:
        level = "CRITICAL"
    elif score >= 5:
        level = "HIGH"
    elif score >= 2:
        level = "MEDIUM"
    else:
        level = "LOW"
    # "confidence" = how much of the key input data we actually had (data completeness), NOT a
    # statistical probability. More inputs available -> more trustworthy score.
    have = [sbp is not None and dbp is not None, glu is not None, spo2 is not None, hr is not None,
            bool(symptoms), adh is not None, bool(age), bool(conditions.strip())]
    confidence = round(sum(have) / len(have), 2)
    return {"level": level, "score": score, "confidence": confidence, "reasons": reasons,
            "red_flags": red, "critical": critical}

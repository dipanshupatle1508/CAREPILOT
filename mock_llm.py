"""Offline stand-in for the LLMs, used only when MOCK_LLM=1 (to test the graph wiring)."""
import json
import re


def mock_reply(role: str, user: str) -> str:
    if role == "patient":
        msg = user.split("PATIENT MESSAGE:", 1)[-1].strip()
        low = msg.lower()
        out = {"reply": f"[mock] I noted: {msg}", "language": "hi" if re.search(r"[\u0900-\u097F]", msg) else "en",
               "symptoms": [], "vitals": {}, "add_medications": [], "dose_log": [], "facts": []}
        bp = re.search(r"(\d{2,3})\s*/\s*(\d{2,3})", msg)
        if bp:
            out["vitals"].update(systolic=int(bp.group(1)), diastolic=int(bp.group(2)))
        sugar = re.search(r"(?:sugar|glucose)\D{0,10}(\d{2,3})", low)
        if sugar:
            out["vitals"]["glucose"] = int(sugar.group(1))
        if "chest" in low or "सीने" in msg:
            out["symptoms"].append({"symptom": "chest pain", "symptom_en": "chest pain", "severity": 8,
                                    "duration": "30 minutes", "notes": ""})
        if "headache" in low or "सिरदर्द" in msg:
            out["symptoms"].append({"symptom": "headache", "symptom_en": "headache", "severity": 5,
                                    "duration": "since morning", "notes": ""})
        if "took" in low and "metformin" in low:
            out["dose_log"].append({"name": "Metformin", "status": "taken"})
        return json.dumps(out, ensure_ascii=False)
    return f"[mock {role} output]"

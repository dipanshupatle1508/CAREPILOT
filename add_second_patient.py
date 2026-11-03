"""Run once from the project folder:  python add_second_patient.py
Adds a second demo patient so you can test Friends. Safe to run again."""
import care_plan

care_plan.upsert_patient("sunita-002", {"name": "Sunita Joshi", "age": 72, "language": "hi"})
if not any(m["name"] == "Telmisartan" for m in care_plan.snapshot("sunita-002")["medications"]):
    care_plan.add_medication("sunita-002", "Telmisartan", "40 mg", ["07:00", "19:00"])
print("Done. Second patient added: sunita-002 (Sunita Joshi)")

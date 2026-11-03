Friends + water update for carePILOT.AI

Replace in your project folder:
  care_plan.py   (new tables: friendships, water_log + friend/water functions; reset also clears them)
  server.py      (new routes only: GET /api/friends, GET /api/daily, POST /api/friends/request,
                  POST /api/friends/respond, POST /api/water; all existing routes unchanged)
  index.html  -> put it at static/index.html

Existing databases upgrade by themselves (CREATE TABLE IF NOT EXISTS). No existing table is changed.

Switching patient: Settings -> Switch patient -> type a patient ID (e.g. sunita-002) -> Switch.
The whole dashboard (profile, medicines, chat, water, friends) then belongs to that patient.

Trying it with two patients (same server, two browser tabs):
  1) Add a second patient once, e.g. in a Python shell in the project folder:
       import care_plan
       care_plan.upsert_patient("sunita-002", {"name": "Sunita Joshi", "age": 72, "phone": "+91XXXXXXXXXX"})
       care_plan.add_medication("sunita-002", "Telmisartan", "40 mg", ["07:00", "19:00"])
  2) Tab 1: http://127.0.0.1:8000/            (Ramesh)   -> Friends -> add "sunita-002"
  3) Tab 2: http://127.0.0.1:8000/?pid=sunita-002         -> Friends -> Accept
  Then each can see the other's daily profile (medicines today, water vs goal, last check-in).

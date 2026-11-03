# Agentic Healthcare Ecosystem: LangGraph project

## Run the dashboard (recommended)
```bash
pip install langgraph openai requests     # chromadb is optional (see requirements.txt)
export HF_TOKEN=hf_...                    # PowerShell: $env:HF_TOKEN="hf_..."
python server.py                          # opens http://127.0.0.1:8000
MOCK_LLM=1 python server.py               # same dashboard with fake AI replies (offline rehearsal)
```
Tabs: **Patient** (chat, voice, medicines), **Caregiver** (risk, adherence, family updates),
**Clinician** (SBAR summary, vital trends, risk history), **Emergency** (urgency, specialist, ranked hospitals, map, 112/108 buttons).
The strip at the top lights up the agents that ran, so judges can see the workflow.

## Run in the terminal
```bash
pip install -r requirements.txt
export HF_TOKEN=hf_...            # Hugging Face token
python main.py --reset            # 3-scenario demo (routine -> high -> emergency)
python main.py --chat             # talk to the Patient Agent (medicine reminders run in the background)
python main.py --audio note.wav   # voice note via Whisper
MOCK_LLM=1 python main.py         # no API calls: tests the wiring offline
python main.py --compare          # same symptom, two patients -> different recommendations (no API needed)
SIMULATE_AVAILABILITY=1 python main.py   # shows bed/ICU status labelled SIMULATED (default: 'not verified')
```
Edit `config.py` to change models (`FAST_MODEL`, `REASONING_MODEL`), database or embedder.

## Files
| File | Role |
|---|---|
| `graph.py` | LangGraph workflow: nodes, routing by risk level |
| `agents.py` | Patient, Risk, Emergency, Clinician, Caregiver, Wellness agents |
| `care_plan.py` | Shared care plan database (SQLite; PostgreSQL-portable) |
| `memory.py` | ChromaDB long-term memory |
| `risk.py` | Rule-based risk scoring: LOW / MEDIUM / HIGH / CRITICAL + data-completeness confidence |
| `navigator.py` | Personalised emergency recommendation engine: specialist, destination type, transport, facility ranking, own-doctor lookup |
| `tools.py` | Nominatim hospital search, specialist mapping, Leaflet map, notification hook |
| `server.py` | Dashboard server (standard library only) |
| `static/index.html` | The dashboard page (Leaflet map from a CDN, needs internet for map tiles) |
| `stt.py` | Whisper speech-to-text (optional, command line `--audio`) |
| `llm.py`, `mock_llm.py` | Model wrapper, offline mock |

## Honest limits (say these to the judges before they ask)
- **Bed / ICU availability is NOT live data.** No verified source is connected. The app says "not verified, call ahead"; the optional demo mode is labelled SIMULATED.
- Facility type (clinic vs emergency-capable) is **estimated from the hospital's name**, so treat it as a hint.
- `confidence` means how complete the input data was, not a statistical probability.
- Browser voice input (the microphone button) uses the browser's own speech recognition: Chrome or Edge, needs internet.
- Nearby-doctor search depends on OpenStreetMap, whose coverage of individual doctors is patchy.

## Before using with real patients
Thresholds in `risk.py` are demo-grade and need clinician review; add consent, encryption and audit logging;
replace `send_notification` with a real channel (WhatsApp/SMS). This is a hackathon prototype, not a medical device.

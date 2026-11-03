"""Central settings. Change models / paths here only."""
import os

# --- LLM access (Hugging Face router, OpenAI-compatible) -------------------
HF_BASE_URL = "https://router.huggingface.co/v1"
HF_TOKEN = os.environ.get("HF_TOKEN", "")

# Two models only: one for deep reasoning, one fast multilingual/tool model.
# Verify the exact IDs / provider suffixes on the HF router model list.
REASONING_MODEL = os.getenv("REASONING_MODEL", "deepseek-ai/DeepSeek-R1:novita")
FAST_MODEL = os.getenv("FAST_MODEL", "Qwen/Qwen3-14B")

# MOCK_LLM=1 -> no API calls; canned replies. For testing the wiring offline.
MOCK_LLM = os.getenv("MOCK_LLM") == "1"

# --- Storage ---------------------------------------------------------------
DB_PATH = os.getenv("CARE_DB", "care_plan.db")          # swap for PostgreSQL later
CHROMA_DIR = os.getenv("CHROMA_DIR", "chroma_memory")
EMBED_MODEL = os.getenv("EMBED_MODEL", "")              # e.g. "BAAI/bge-m3" for multilingual

# --- Maps ------------------------------------------------------------------
NOMINATIM_URL = "https://nominatim.openstreetmap.org"
USER_AGENT = "agentic-care-hackathon/0.1 (replace-with-your-email)"  # Nominatim requires this

# --- Emergency navigation ----------------------------------------------------
# Live bed/ICU availability needs a verified data source, which we don't have.
# SIMULATE_AVAILABILITY=1 shows clearly-labelled SIMULATED demo status; default is "not verified".
SIMULATE_AVAILABILITY = os.getenv("SIMULATE_AVAILABILITY") == "1"
# India defaults: 112 = national emergency number; 108 = ambulance in many states (varies by state).
EMERGENCY_CONTACTS = {"emergency": "112", "ambulance": "108 (availability varies by state)"}

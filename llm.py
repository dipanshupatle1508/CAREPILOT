"""
Model wrapper used by every agent.
  ask(model, system, user, role=...)       -> plain text
  ask_json(model, system, user, role=...)  -> dict (parsed JSON, {} if the model returned garbage)

Talks to any OpenAI-compatible endpoint with plain `requests` (default: Hugging Face router).
Set LLM_BASE_URL / LLM_API_KEY / FAST_MODEL / REASONING_MODEL to use OpenAI, Anthropic, Gemini, Groq, Ollama...
Optional: LLM_TIMEOUT (s, default 60), LLM_RETRIES (default 5), LLM_MIN_INTERVAL (s between calls).
With MOCK_LLM=1 no network call is made and mock_llm.py supplies canned replies.
"""
import json
import os
import re
import time

import requests

import config


_last_call = 0.0
_QUIRKS = {}            # per-model parameter fixes learned at runtime (e.g. newer OpenAI models)


def _clean(text: str) -> str:
    """Remove <think>...</think> reasoning blocks (Qwen3 / DeepSeek-R1) and surrounding whitespace."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
    return text.replace("<think>", "").strip()


def _chat(model: str, system: str, user: str) -> str:
    base_url = os.getenv("LLM_BASE_URL", config.HF_BASE_URL)          # any OpenAI-compatible endpoint
    api_key = os.getenv("LLM_API_KEY") or config.HF_TOKEN
    if not api_key and "localhost" not in base_url:
        raise RuntimeError("No API key set. Set LLM_API_KEY (or HF_TOKEN), or use MOCK_LLM=1 for offline test mode.")
    max_tokens = 4000 if "deepseek" in model.lower() else 3000     # reasoning models spend tokens thinking
    payload = {"model": model, "max_tokens": max_tokens, "temperature": 0.3,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    q = _QUIRKS.setdefault(model, {})
    if q.get("completion_tokens"):
        payload["max_completion_tokens"] = payload.pop("max_tokens") * 3     # reasoning models also "think" in this budget
    if q.get("no_temp"):
        payload.pop("temperature", None)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout = int(os.getenv("LLM_TIMEOUT", "60"))
    attempts = int(os.getenv("LLM_RETRIES", "5"))
    last_err = ""
    gap = float(os.getenv("LLM_MIN_INTERVAL", "0"))          # seconds to keep between calls (stay under rate limits)
    global _last_call
    for i in range(attempts):
        wait_gap = gap - (time.time() - _last_call)
        if wait_gap > 0:
            time.sleep(wait_gap)
        _last_call = time.time()
        try:
            r = requests.post(f"{base_url.rstrip('/')}/chat/completions", headers=headers, json=payload,
                              timeout=timeout)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"] or ""
            last_err = f"HTTP {r.status_code}: {r.text[:900]}"
            if r.status_code == 400:                                   # model rejects a parameter: adapt once, remember
                changed = False
                if "max_completion_tokens" in r.text and "max_tokens" in payload:
                    payload["max_completion_tokens"] = payload.pop("max_tokens") * 3
                    q["completion_tokens"] = changed = True
                if "temperature" in r.text and "temperature" in payload:
                    payload.pop("temperature")
                    q["no_temp"] = changed = True
                if changed:
                    continue
            if r.status_code == 429 and re.search(r"per.?day|daily", r.text, re.I):   # daily quota: retrying is pointless
                raise RuntimeError(f"Daily free quota used up for model '{model}'. Wait until it resets, use another "
                                   f"model/provider, or run with MOCK_LLM=1. Details: {r.text[:400]}")
            if r.status_code not in (408, 429, 500, 502, 503, 504):      # not temporary: fail right away
                raise RuntimeError(f"LLM call failed for model '{model}': {last_err}")
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_err = f"{type(e).__name__}"
        if i < attempts - 1:
            wait = (25 if last_err.startswith("HTTP 429") else 3) * (i + 1)
            print(f"   [llm] temporary problem ({last_err[:200]}); retrying in {wait}s ({i + 2}/{attempts})...")
            time.sleep(wait)
    raise RuntimeError(f"LLM call failed after {attempts} attempts for model '{model}': {last_err}")


def ask(model: str, system: str, user: str, role: str = "") -> str:
    if config.MOCK_LLM:
        from mock_llm import mock_reply
        return mock_reply(role, user)
    return _clean(_chat(model, system, user))


def _parse_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")      # fall back to the outermost {...}
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def ask_json(model: str, system: str, user: str, role: str = "") -> dict:
    return _parse_json(ask(model, system, user, role))

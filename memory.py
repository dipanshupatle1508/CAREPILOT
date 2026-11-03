"""
LONG-TERM MEMORY. ChromaDB stores everything the patient has told us as searchable text, so the
Patient Agent can recall relevant history ("last month you said the new tablet made you dizzy").

If ChromaDB is not installed or does not work on your Python version (it can fail on very new Pythons),
memory automatically falls back to a simple JSON file with keyword search. It still persists between runs.
"""
import json
import os
import uuid
from datetime import datetime

import config

_collection = None
_chroma_failed = False
_fallback = None          # list of [patient_id, text], loaded lazily from disk
_FALLBACK_FILE = os.path.join(config.CHROMA_DIR, "fallback_memory.json")


def _get_collection():
    global _collection
    if _collection is None:
        import chromadb
        client = chromadb.PersistentClient(path=config.CHROMA_DIR)
        kwargs = {}
        if config.EMBED_MODEL:  # use a multilingual embedder (e.g. BAAI/bge-m3) for Hindi/Marathi recall
            from chromadb.utils import embedding_functions
            kwargs["embedding_function"] = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=config.EMBED_MODEL)
        _collection = client.get_or_create_collection("patient_memory", **kwargs)
    return _collection


def _mark_chroma_failed(err):
    global _chroma_failed
    if not _chroma_failed:
        print(f"[memory] ChromaDB unavailable ({type(err).__name__}); using simple file-based memory instead.")
    _chroma_failed = True


def _load_fallback():
    global _fallback
    if _fallback is None:
        try:
            with open(_FALLBACK_FILE, encoding="utf-8") as f:
                _fallback = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _fallback = []
    return _fallback


def _save_fallback():
    os.makedirs(config.CHROMA_DIR, exist_ok=True)
    with open(_FALLBACK_FILE, "w", encoding="utf-8") as f:
        json.dump(_fallback, f, ensure_ascii=False)


def add(pid: str, text: str, kind: str = "note"):
    text = f"[{datetime.now():%Y-%m-%d}] {text}"
    if not _chroma_failed:
        try:
            _get_collection().add(ids=[str(uuid.uuid4())], documents=[text], metadatas=[{"patient_id": pid, "kind": kind}])
            return
        except Exception as e:
            _mark_chroma_failed(e)
    _load_fallback().append([pid, text])
    _save_fallback()


def search(pid: str, query: str, k: int = 4) -> list:
    if not _chroma_failed:
        try:
            res = _get_collection().query(query_texts=[query], n_results=k, where={"patient_id": pid})
            return res["documents"][0] if res["documents"] else []
        except Exception as e:
            _mark_chroma_failed(e)
    words = set(query.lower().split())
    scored = [(len(words & set(t.lower().split())), t) for p, t in _load_fallback() if p == pid]
    best = [t for s, t in sorted(scored, key=lambda x: x[0], reverse=True)[:k] if s > 0]
    return best or [t for p, t in _load_fallback() if p == pid][-2:]   # no keyword hit -> most recent memories

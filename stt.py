"""Speech-to-text with Whisper (faster-whisper). Language is auto-detected, so Hindi/Marathi work out of the box."""
_model = None


def transcribe(path: str, model_size: str = "small") -> str:
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(model_size, compute_type="int8")
    segments, info = _model.transcribe(path)
    return " ".join(s.text.strip() for s in segments)

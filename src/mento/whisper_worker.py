"""Keeps a faster-whisper model loaded and transcribes the WAV files it is sent.

Run under the Python of the WhisperX tool, which ships faster-whisper (mento's
own environment has no PyTorch nor CTranslate2): standard library only besides
faster_whisper and numpy, no mento import. The WAV (16 kHz mono s16, from
transcribe.prepare_input) is read here, not by faster-whisper: its PyAV decoder
breaks on the PyAV version WhisperX pins. Prints {"ready": true} once the model is
loaded, then answers each request line on stdin with one line on stdout:

  → {"wav": "/path/piece.wav", "language": "fr", "prompt": "Acme, DSN"}
  ← {"segments": [{"start": 0.0, "end": 2.1, "text": "…"}]}  or  {"error": "…"}

Usage: python whisper_worker.py <model> <device> <compute_type>
"""

import json
import sys
import wave

SAMPLE_RATE = 16000


def load(path):
    import numpy as np

    with wave.open(path, "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise ValueError(f"{path}: expected 16 kHz mono 16-bit")
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768


def main():
    model_name, device, compute_type = sys.argv[1:4]
    if device == "cuda":
        # PyTorch loads the CUDA libraries of its nvidia-* wheels (cuBLAS, cuDNN), which
        # CTranslate2 would not find on its own; WhisperX gets them the same way
        import torch  # noqa: F401
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        try:
            req = json.loads(line)
            segments, _ = model.transcribe(load(req["wav"]), language=req.get("language") or None,
                                           initial_prompt=req.get("prompt") or None,
                                           vad_filter=True)  # no hallucinated text on silence
            reply = {"segments": [{"start": s.start, "end": s.end, "text": s.text}
                                  for s in segments]}
        except Exception as e:  # noqa: BLE001 — reported to the live process
            reply = {"error": f"{type(e).__name__}: {e}"}
        print(json.dumps(reply), flush=True)


if __name__ == "__main__":
    main()

import httpx
import pytest

from audio_recorder import transcribe as tr
from audio_recorder.library import read_meta
from audio_recorder.media import ffmpeg


def test_parse_speakers():
    assert tr.parse_speakers("") is None
    assert tr.parse_speakers("3") == (3, 3)
    assert tr.parse_speakers("2-4") == (2, 4)
    for bad in ("x", "4-2", "2-"):
        with pytest.raises(tr.TranscribeError):
            tr.parse_speakers(bad)


def test_vocabulary_puts_global_terms_first(cfg):
    assert tr.vocabulary(cfg, "l'URSSAF, DSN") == "Claude, Oto, l'URSSAF, DSN"
    assert tr.vocabulary(cfg, "") == "Claude, Oto"


def test_voxtral_request_and_speaker_names(cfg, tmp_path):
    wav = tmp_path / "input.wav"
    ffmpeg("-f", "lavfi", "-i", "sine=d=1", str(wav))
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read()
        return httpx.Response(200, json={"segments": [
            {"start": 0.0, "end": 1.0, "text": "Bonjour", "speaker_id": "speaker_2"},
            {"start": 1.0, "end": 2.0, "text": "Salut", "speaker_id": "speaker_1"},
            {"start": 2.0, "end": 3.0, "text": "Re", "speaker_id": "speaker_2"},
        ]})

    segments = tr.run_voxtral(wav, cfg, model="voxtral-mini-latest", language="fr",
                              vocab="Claude, DSN", speakers=None, log_path=tmp_path / "v.log",
                              client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen["auth"] == "Bearer mk"
    body = seen["body"]
    for field in (b'name="diarize"\r\n\r\ntrue', b'name="timestamp_granularities"\r\n\r\nsegment',
                  b'name="context_bias"\r\n\r\nClaude', b'name="context_bias"\r\n\r\nDSN',
                  b'name="language"\r\n\r\nfr', b'filename="audio.mp3"'):
        assert field in body
    assert [s["speaker"] for s in segments] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]


def test_voxtral_error_is_reported(cfg, tmp_path):
    wav = tmp_path / "input.wav"
    ffmpeg("-f", "lavfi", "-i", "sine=d=1", str(wav))
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="bad key")))
    with pytest.raises(tr.TranscribeError, match="401"):
        tr.run_voxtral(wav, cfg, model="m", language="", vocab="", speakers=None,
                       log_path=tmp_path / "v.log", client=client)


def test_pipeline_writes_transcript_and_meta(cfg, monkeypatch):
    folder = cfg.recordings_dir / "20261119_test"
    folder.mkdir()
    ffmpeg("-f", "lavfi", "-i", "sine=d=2", "-ac", "2", str(folder / "audio.mp3"))
    calls = {}

    def fake(wav, cfg_, **kw):
        calls.update(kw)
        assert wav.exists()
        return [{"start": 0.5, "end": 1.5, "speaker": "SPEAKER_00", "text": " Bonjour l'équipe"}]

    monkeypatch.setitem(tr.RUNNERS, "whisperx", fake)
    path = tr.transcribe(cfg, folder=folder, prompt="DSN", speakers="2-4", out=lambda _: None)
    assert path.read_text(encoding="utf-8") == "[00:00] SPEAKER_00: Bonjour l'équipe\n"
    assert calls["speakers"] == (2, 4) and calls["vocab"] == "Claude, Oto, DSN"
    meta = read_meta(folder)["transcription"]
    assert meta["engine"] == "whisperx" and meta["speakers"] == "2-4" and meta["dual_track"] is False


def test_unknown_engine(cfg):
    with pytest.raises(tr.TranscribeError, match="unknown engine"):
        tr.transcribe(cfg, folder=cfg.recordings_dir, engine="nope")

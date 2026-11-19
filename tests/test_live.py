import sys
import threading
import time

import numpy as np
import pytest

from mento import control, live
from mento.recorder import SAMPLE_RATE


def tone(seconds, freq, amplitude=0.3):
    idx = np.arange(int(seconds * SAMPLE_RATE))
    return (amplitude * 32767 * np.sin(2 * np.pi * freq * idx / SAMPLE_RATE)).astype("<i2")


def silence(seconds):
    return np.zeros(int(seconds * SAMPLE_RATE), "<i2")


@pytest.fixture
def recording(cfg, session):
    """A recording in progress: 45 s where the remote side speaks, then the mic."""
    folder = cfg.recordings_dir / "recording_20261119_100000"
    folder.mkdir()
    # remote 2-14 s, silence 14-15 s, mic 15-30 s, remote 30-45 s
    mic = np.concatenate([silence(15), tone(15, 440), silence(15)])
    system = np.concatenate([silence(2), tone(12, 880), silence(16), tone(15, 660)])
    (folder / "mic.pcm").write_bytes(mic.tobytes())
    (folder / "system.pcm").write_bytes(system.tobytes())
    session.write(status="recording", folder=str(folder))
    return folder


def fake_runner(calls):
    def run(wav, cfg, **kw):
        calls.append(kw)
        assert wav.exists()
        return [{"start": 1.0, "end": 9.0, "speaker": "SPEAKER_00", "text": f" piece {len(calls)}"}]
    return run


def test_cut_point_picks_the_quietest_frame():
    levels = [-20.0] * 300
    levels[150] = -90.0
    assert live.cut_point(levels) == 151
    levels[50] = -100.0  # before MIN_PIECE_S: ignored
    assert live.cut_point(levels) == 151


def test_pieces_cut_at_silence_with_local_speaker(cfg, session, recording):
    calls = []
    lv = live.Live(recording, cfg, prompt="DSN", language="fr", runner=fake_runner(calls),
                   session=session)
    paths, end = lv.next_piece()
    assert 14 * SAMPLE_RATE < end <= 15 * SAMPLE_RATE  # within the silence at 14-15 s
    lv.transcribe_piece(paths, end)
    paths, end = lv.next_piece()
    lv.transcribe_piece(paths, end)

    assert calls[0]["vocab"] == "Claude, Oto, DSN" and calls[0]["language"] == "fr"
    lines = live.live_file(recording).read_text(encoding="utf-8").splitlines()
    assert lines == ["[00:01] OTHERS: piece 1", "[00:15] Alexis: piece 2"]


def test_silent_piece_is_not_sent(cfg, session, recording):
    for name in ("mic.pcm", "system.pcm"):
        (recording / name).write_bytes(silence(25).tobytes())
    calls = []
    lv = live.Live(recording, cfg, runner=fake_runner(calls), session=session)
    lv.transcribe_piece(*lv.next_piece())
    assert calls == [] and not live.live_file(recording).exists() and lv.offset > 0


def test_failed_piece_is_retried_and_reported(cfg, session, recording, monkeypatch):
    monkeypatch.setattr(live, "POLL_S", 0.05)
    monkeypatch.setattr(live, "RETRY_S", 0.1)
    attempts = []

    def flaky(wav, cfg_, **kw):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("HTTP 503")
        return [{"start": 0, "end": 1, "text": "ok"}]

    lv = live.Live(recording, cfg, engine="voxtral", runner=flaky, session=session)
    t = threading.Thread(target=lv.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while len(attempts) < 2 and time.time() < deadline:
        time.sleep(0.05)
    session.write(status="stopping", folder=str(recording))
    t.join(timeout=10)
    state = live.read_state(recording)
    assert state["status"] == "done" and "error" not in state and state["offset"] > 0
    assert len(attempts) >= 2


def test_new_process_resumes_after_offset(cfg, session, recording):
    calls = []
    first = live.Live(recording, cfg, runner=fake_runner(calls), session=session)
    first.transcribe_piece(*first.next_piece())
    first.write_state("done")

    second = live.Live(recording, cfg, runner=fake_runner(calls), session=session)
    assert second.offset == first.offset
    second.transcribe_piece(*second.next_piece())
    lines = live.live_file(recording).read_text(encoding="utf-8").splitlines()
    assert [line.split("]")[0] for line in lines] == ["[00:01", "[00:15"]
    assert lines[1].endswith("Alexis: piece 2")  # levels of the first piece were reloaded


def test_start_live_spawns_once(session, recording):
    spawned = []
    folder, started = control.start_live(session, "whisperx", None, "DSN", "fr",
                                         spawn=lambda *a, **kw: spawned.append(a))
    assert started and folder == recording
    assert spawned == [("mento.live", recording, "--engine", "whisperx",
                        "--prompt", "DSN", "--language", "fr")]
    _, started = control.start_live(session, "voxtral", spawn=lambda *a, **kw: spawned.append(a))
    assert not started and len(spawned) == 1


def test_start_live_needs_a_recording(session):
    with pytest.raises(control.ControlError, match="no recording"):
        control.start_live(session, "voxtral", spawn=lambda *a, **kw: None)


def test_check_ready_needs_the_mistral_key(cfg):
    cfg.values["MISTRAL_API_KEY"] = ""
    with pytest.raises(live.LiveError, match="MISTRAL_API_KEY"):
        live.check_ready(cfg, "voxtral")


def test_engine_follows_the_config(cfg):
    assert live.engine_and_model(cfg) == ("whisperx", "large-v3")
    assert live.engine_and_model(cfg, "voxtral") == ("voxtral", "voxtral-mini-latest")
    assert live.engine_and_model(cfg, "whisperx", "small") == ("whisperx", "small")
    with pytest.raises(live.LiveError, match="unknown engine"):
        live.engine_and_model(cfg, "nope")


FAKE_WORKER = """
import json, sys
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    req = json.loads(line)
    if req["wav"] == "boom":
        print(json.dumps({"error": "bad wav"}), flush=True)
    elif req["wav"] == "die":
        sys.exit(1)
    else:
        print(json.dumps({"segments": [{"start": 0, "end": 1, "text": req["prompt"]}]}), flush=True)
"""


def test_local_whisper_keeps_one_worker(cfg, tmp_path, monkeypatch):
    script = tmp_path / "worker.py"
    script.write_text(FAKE_WORKER, encoding="utf-8")
    monkeypatch.setattr(live.transcribe, "whisperx_python", lambda: sys.executable)
    runner = live.LocalWhisper(cfg, "small")
    runner.cmd = [sys.executable, str(script)]
    try:
        assert runner("a.wav", cfg, language="fr", vocab="DSN")[0]["text"] == "DSN"
        proc = runner.proc
        assert runner("b.wav", cfg, language="fr", vocab="Acme")[0]["text"] == "Acme"
        assert runner.proc is proc  # model loaded once
        with pytest.raises(live.LiveError, match="bad wav"):
            runner("boom", cfg, language="", vocab="")
        with pytest.raises(live.LiveError, match="died"):
            runner("die", cfg, language="", vocab="")
        assert runner.proc is None
        assert runner("c.wav", cfg, language="", vocab="x")[0]["text"] == "x"  # restarted
    finally:
        runner.close()

import threading
import time

import numpy as np
import pytest

from mento import config, control, recorder
from mento.media import ffmpeg, probe

from .conftest import FakeDevice


def wait(predicate, timeout=10):
    deadline = time.time() + timeout
    while not predicate():
        assert time.time() < deadline, "timed out"
        time.sleep(0.05)


def channels_rms(path):
    raw = ffmpeg("-i", str(path), "-f", "s16le", "-ac", "2", "-ar", "8000", "-", capture=True)
    a = np.frombuffer(raw, "<i2").reshape(-1, 2).astype(float)
    return np.sqrt((a ** 2).mean(axis=0))


@pytest.fixture
def run_in_thread(session):
    """control.start's spawn: the recorder in a thread instead of a process."""
    threads = []

    def spawn(devices):
        def go(folder):
            t = threading.Thread(target=recorder.run, args=(folder, session, *devices), daemon=True)
            t.start()
            threads.append(t)
        return go

    yield spawn
    session.send("stop")
    for t in threads:
        t.join(timeout=30)


def test_start_pause_resume_stop_gives_dual_track_mp3(cfg, session, fake_devices, run_in_thread):
    mic, system = fake_devices
    folder, state = control.start(session, cfg.recordings_dir, spawn=run_in_thread((mic, system)))
    assert session.status() == "recording" and state["system_audio"]
    time.sleep(1)

    control.pause(session)
    assert session.status() == "paused"
    with pytest.raises(control.ControlError):
        control.start(session, cfg.recordings_dir)
    time.sleep(0.5)
    control.resume(session)
    assert mic.opened == 2  # devices released during the pause
    time.sleep(1)

    out, seconds = control.stop(session, out=lambda _: None)
    assert out == folder and session.status() == "idle"
    assert not list(folder.glob("*.pcm"))
    info = probe(folder / "audio.mp3")
    assert info["comment"] == config.DUAL_TRACK_TAG
    assert 1.5 <= info["duration"] <= 3.0  # pause excluded
    left, right = channels_rms(folder / "audio.mp3")
    assert left > 1000 and right > 1000


def test_elapsed_excludes_pauses(cfg, session, fake_devices, run_in_thread):
    control.start(session, cfg.recordings_dir, spawn=run_in_thread(fake_devices))
    time.sleep(1)
    control.pause(session)
    paused_at = session.elapsed()
    time.sleep(1.5)
    assert session.elapsed() == paused_at
    assert 0 <= paused_at <= 2


def test_mic_only_without_system_audio(cfg, session, run_in_thread):
    folder, state = control.start(session, cfg.recordings_dir,
                                  spawn=run_in_thread((FakeDevice(440), None)))
    assert not state["system_audio"]
    time.sleep(0.5)
    control.stop(session, out=lambda _: None)
    assert probe(folder / "audio.mp3")["comment"] == ""


def test_device_failure_is_reported(cfg, session, run_in_thread):
    with pytest.raises(control.ControlError, match="unplugged"):
        control.start(session, cfg.recordings_dir,
                      spawn=run_in_thread((FakeDevice(440, fail=True), None)))
    assert session.status() == "idle"


def test_stop_recovers_a_dead_recorder(cfg, session):
    folder = cfg.recordings_dir / "recording_20261119_100000"
    folder.mkdir()
    tone = (np.sin(np.arange(48000) / 10) * 10000).astype("<i2").tobytes()
    (folder / "mic.pcm").write_bytes(tone)
    (folder / "system.pcm").write_bytes(tone[: len(tone) // 2])  # shorter: padded
    session.write(status="recording", folder=str(folder), recorded_s=0, segment_start=time.time())
    state = session.read()
    state["heartbeat"] -= 60
    session.state_file.write_text(__import__("json").dumps(state))
    assert session.status() == "interrupted"

    _, seconds = control.stop(session, out=lambda _: None)
    assert seconds == 1
    assert probe(folder / "audio.mp3")["comment"] == config.DUAL_TRACK_TAG
    assert session.status() == "idle"


def test_pause_and_resume_check_state(session):
    with pytest.raises(control.ControlError):
        control.pause(session)
    with pytest.raises(control.ControlError):
        control.resume(session)
    with pytest.raises(control.ControlError):
        control.stop(session)

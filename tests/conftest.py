import numpy as np
import pytest

from mento.config import Config, DEFAULTS
from mento.session import Session


@pytest.fixture
def cfg(tmp_path):
    return Config({**DEFAULTS, "RECORDINGS_DIR": str(tmp_path / "rec"), "LOCAL_SPEAKER": "Alexis",
                   "WHISPER_INITIAL_PROMPT": "Claude, Oto", "HF_TOKEN": "hf", "MISTRAL_API_KEY": "mk"})


@pytest.fixture
def session(tmp_path):
    d = tmp_path / "state"
    d.mkdir()
    return Session(d)


class FakeDevice:
    """A soundcard-like device producing a tone in real time."""

    def __init__(self, freq, amplitude=0.3, fail=False):
        self.freq, self.amplitude, self.fail = freq, amplitude, fail
        self.opened = 0

    def recorder(self, samplerate, channels, blocksize=None):
        device = self

        class Rec:
            def __enter__(self):
                if device.fail:
                    raise RuntimeError("device unplugged")
                device.opened += 1
                self.t = 0
                return self

            def __exit__(self, *exc):
                return False

            def record(self, numframes):
                import time
                time.sleep(numframes / samplerate)
                idx = np.arange(self.t, self.t + numframes)
                self.t += numframes
                tone = device.amplitude * np.sin(2 * np.pi * device.freq * idx / samplerate)
                return tone.reshape(-1, 1).astype(np.float32)

        return Rec()


@pytest.fixture
def fake_devices():
    return FakeDevice(440), FakeDevice(880)

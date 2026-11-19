import pytest

from audio_recorder.media import ffmpeg
from audio_recorder.transcript import fmt_stamp, format_lines

SEGMENTS = [
    {"start": 0.2, "end": 1.4, "speaker": "SPEAKER_01", "text": " Bonjour."},
    {"start": 2.2, "end": 5.8, "speaker": "SPEAKER_01", "text": " Salut."},
    {"start": 5.9, "end": 5.95, "speaker": "SPEAKER_00", "text": "  "},
]


def make_audio(path, mic, system):
    """6 s stereo tone: mic (left) and system (right) volume expressions over t."""
    ffmpeg("-f", "lavfi", "-i", "sine=f=300:d=6", "-filter_complex",
           f"[0:a]volume='{mic}':eval=frame[l];[0:a]volume='{system}':eval=frame[r];"
           "[l][r]join=inputs=2:channel_layout=stereo[out]", "-map", "[out]", str(path))
    return path


@pytest.mark.parametrize("mic, system, dual, first", [
    ("if(lt(t,1.5),1,0)", "if(lt(t,1.5),0,1)", False, "SPEAKER_01"),  # mixed: diarization kept
    ("if(lt(t,1.5),1,0)", "if(lt(t,1.5),0,1)", True, "Alexis"),       # mic side pinned
    ("if(lt(t,1.5),0.05,0)", "if(lt(t,5.2),1,0)", True, "Alexis"),    # over loud system audio
    ("1", "0", True, "SPEAKER_01"),                                     # nothing plays remotely
])
def test_local_speaker(tmp_path, mic, system, dual, first):
    audio = make_audio(tmp_path / "a.wav", mic, system)
    lines = format_lines(SEGMENTS, audio, dual, "Alexis")
    assert lines == [f"[00:00] {first}: Bonjour.", "[00:02] SPEAKER_01: Salut."]


def test_stamp_past_one_hour():
    assert fmt_stamp(59 * 60 + 59) == "59:59"
    assert fmt_stamp(3600 + 62) == "1:01:02"

import json
from types import SimpleNamespace as NS

import pytest

from audio_recorder import library
from audio_recorder.library import UsageError


@pytest.fixture
def root(tmp_path):
    r = tmp_path
    for name in ("20261117_point_client_onboarding", "20261118_atelier_paie",
                 "recording_20261119_093000", "not_a_recording"):
        (r / name).mkdir()
    (r / "20261117_point_client_onboarding/transcript.txt").write_text(
        "[00:05] Alexis: Bonjour, on fait le point sur la réunion.\n"
        "[00:12] SPEAKER_01: Oui, la DSN est prête.\n"
        "[01:30] Alexis: Parfait.\n"
        "[62:10] SPEAKER_01: On se revoit jeudi.\n"
        "[1:03:00] SPEAKER_01: Format heures.\n", encoding="utf-8")
    (r / "20261117_point_client_onboarding/meta.json").write_text('{"duration_s": 3800}')
    (r / "20261118_atelier_paie/transcript.txt").write_text("[00:01] SPEAKER_00: Reunion paie, sans accent.\n")
    (r / "20261118_atelier_paie/meta.json").write_text('{"duration_s": 60}')
    (r / "recording_20261119_093000/mic.pcm").write_bytes(b"\0\0")
    return r


def args(**kw):
    base = dict(json=False, since=None, limit=None, speaker=None, from_=None, to=None, context=0)
    return NS(**{**base, **kw})


def test_list_newest_first_skips_other_folders(root, capsys):
    library.do_list(root, args(json=True))
    rows = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in rows] == ["recording_20261119_093000", "20261118_atelier_paie",
                                       "20261117_point_client_onboarding"]
    assert rows[0]["state"] == "in_progress"


def test_list_since(root, capsys):
    library.do_list(root, args(since="2026-11-18"))
    out = capsys.readouterr().out
    assert "atelier_paie" in out and "onboarding" not in out


def test_resolve_last_prefix_substring(root):
    assert library.resolve(root, "last").name == "recording_20261119_093000"
    assert library.resolve(root, None).name == "recording_20261119_093000"
    assert library.resolve(root, "20261117").name == "20261117_point_client_onboarding"
    assert library.resolve(root, "paie").name == "20261118_atelier_paie"


def test_resolve_ambiguous(root):
    with pytest.raises(UsageError, match="several"):
        library.resolve(root, "202611")


def test_search_ignores_case_and_accents(root, capsys):
    library.do_search(root, args(query="REUNION"))
    out = capsys.readouterr().out
    assert "20261117_point_client_onboarding [00:05] Alexis: Bonjour" in out
    assert "20261118_atelier_paie [00:01] SPEAKER_00: Reunion paie" in out


def test_search_speaker_and_no_match(root, capsys):
    library.do_search(root, args(query="reunion", speaker="alexis"))
    assert "atelier_paie" not in capsys.readouterr().out
    assert library.do_search(root, args(query="introuvable")) == 1


def test_cat_filters_and_reads_both_time_formats(root, capsys):
    library.do_cat(root, args(ref="onboarding", speaker="SPEAKER_01", from_="0:10", to="1:02:30"))
    assert capsys.readouterr().out.splitlines() == [
        "[00:12] SPEAKER_01: Oui, la DSN est prête.",
        "[1:02:10] SPEAKER_01: On se revoit jeudi.",
    ]
    lines = library.read_lines(root / "20261117_point_client_onboarding")
    assert lines[-1]["t"] == 3780


def test_show_talk_time(root, capsys):
    library.do_show(root, args(ref="onboarding", json=True))
    info = json.loads(capsys.readouterr().out)
    alexis = next(s for s in info["speakers"] if s["name"] == "Alexis")
    assert alexis["lines"] == 2


def test_rename_keeps_date_and_checks_name(root, capsys):
    with pytest.raises(UsageError):
        library.do_rename(root, args(ref="recording_20261119_093000", slug="Bad Name"))
    library.do_rename(root, args(ref="recording_20261119_093000", slug="point_equipe"))
    assert (root / "20261119_point_equipe").is_dir()


def test_set_meta_merges_nested(root):
    folder = root / "20261118_atelier_paie"
    library.set_meta(folder, {"transcription": {"model": "large-v3"}})
    library.set_meta(folder, {"transcription": {"dual_track": True}})
    assert library.read_meta(folder) == {"duration_s": 60,
                                         "transcription": {"model": "large-v3", "dual_track": True}}

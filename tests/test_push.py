import hashlib
import json

import httpx
import pytest

from audio_recorder.push import PushError, push, title_from


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "20261118_point_client_onboarding"
    f.mkdir()
    (f / "transcript.txt").write_text("[00:01] Alexis: Bonjour\n", encoding="utf-8")
    (f / "audio.mp3").write_bytes(b"ID3fake-mp3")
    (f / "meta.json").write_text(json.dumps({
        "started_at": "2026-11-18T14:00:00+01:00", "duration_s": 1500,
        "transcription": {"model": "large-v3", "speakers": "3"}}))
    return f


def test_title_from_folder_name():
    assert title_from("20261118_point_client_onboarding") == "Point client onboarding"
    assert title_from("recording_20261119_093000") == "recording_20261119_093000"


def test_push_with_audio_follows_the_contract(folder):
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url), request.headers, request.read()))
        path = request.url.path
        if path.endswith("/by-ref/20261118_point_client_onboarding"):
            return httpx.Response(200, json={"id": "rec_1"})
        if path.endswith("/audio/upload"):
            return httpx.Response(200, json={"upload_url": "https://s3.example/put?sig=1", "method": "PUT",
                                             "headers": {"Content-Type": "audio/mpeg"}})
        return httpx.Response(200, json={})

    rid = push(folder, "mento_pat_x", with_audio=True, out=lambda _: None,
               client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert rid == "rec_1"
    methods = [(m, u.split("?")[0]) for m, u, _, _ in calls]
    assert methods == [
        ("PUT", "https://mento.cc/api/v1/recordings/by-ref/20261118_point_client_onboarding"),
        ("PUT", "https://mento.cc/api/v1/recordings/rec_1/transcript"),
        ("POST", "https://mento.cc/api/v1/recordings/rec_1/audio/upload"),
        ("PUT", "https://s3.example/put"),
        ("POST", "https://mento.cc/api/v1/recordings/rec_1/audio/complete"),
    ]
    body = json.loads(calls[0][3])
    assert body["title"] == "Point client onboarding" and body["duration_s"] == 1500
    assert body["transcription"]["speakers"] == "3"
    assert calls[1][2]["content-type"] == "text/plain; charset=utf-8"
    assert json.loads(calls[2][3]) == {"size_bytes": 11, "sha256": hashlib.sha256(b"ID3fake-mp3").hexdigest()}
    assert "authorization" not in calls[3][2]  # the signed URL carries its own auth
    assert calls[3][3] == b"ID3fake-mp3"


def test_push_skips_audio_already_present(folder):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/audio/upload"):
            return httpx.Response(200, json={"already_present": True})
        return httpx.Response(200, json={"id": "rec_1"})

    push(folder, "t", with_audio=True, out=lambda _: None,
         client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert not any(p.endswith("/complete") for p in paths)


def test_push_reports_problem_details(folder):
    def handler(request):
        return httpx.Response(422, json={"title": "Invalid", "detail": "started_at is required"},
                              headers={"content-type": "application/problem+json"})

    with pytest.raises(PushError, match="started_at is required"):
        push(folder, "t", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_push_needs_token_and_transcript(folder):
    with pytest.raises(PushError, match="MENTO_API_TOKEN"):
        push(folder, "")
    (folder / "transcript.txt").unlink()
    with pytest.raises(PushError, match="transcribe"):
        push(folder, "t")

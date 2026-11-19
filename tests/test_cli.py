import pytest

from mento import cli, config
from mento.trim import find_cut

COMMANDS = ["start", "pause", "resume", "stop", "status", "tray", "transcribe", "trim", "list", "show",
            "cat", "search", "path", "rename", "open", "push", "setup", "install"]


@pytest.mark.parametrize("command", COMMANDS)
def test_every_command_has_help(command, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([command, "--help"])
    assert exc.value.code == 0
    assert f"mento {command}" in capsys.readouterr().out


def test_status_idle_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "state_dir", lambda: tmp_path)
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path / "rec"))
    assert cli.main(["status", "--json"]) == 0
    assert capsys.readouterr().out.strip() == '{"state": "idle"}'


def test_errors_exit_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "state_dir", lambda: tmp_path)
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path / "rec"))
    assert cli.main(["pause"]) == 1
    assert "not recording" in capsys.readouterr().err


def test_config_roundtrip_keeps_quotes():
    values = {"LOCAL_SPEAKER": "Alexis", "WHISPER_INITIAL_PROMPT": 'Claude, l\'IA, "Oto"'}
    assert config.parse(config.dump(values)) == values


def test_config_reads_the_shell_format():
    assert config.parse('HF_TOKEN="hf_x"\n# comment\nLOCAL_SPEAKER=Alexis\n') == {
        "HF_TOKEN": "hf_x", "LOCAL_SPEAKER": "Alexis"}


def test_trim_cut_points():
    # first long silence after 5 min, silences denser after it
    silences = [(100, 4), (400, 12), (450, 5), (500, 5)]
    assert find_cut(silences, 900) == 400
    # long silences only in the first 5 min: nothing to cut
    assert find_cut([(60, 20)], 900) is None

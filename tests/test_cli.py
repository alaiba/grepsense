from __future__ import annotations

from grepsense import cli


def test_version_command(capsys) -> None:
    assert cli.main(["version"]) == 0
    assert "grepsense" in capsys.readouterr().out


def test_status_command_prints_table(tmp_path, monkeypatch, capsys) -> None:
    from grepsense import incremental

    monkeypatch.setattr(
        incremental,
        "format_status",
        lambda _cfg: "REPO\nmyrepo baseline",
    )
    assert cli.main(["status", "--root", str(tmp_path)]) == 0
    assert "myrepo" in capsys.readouterr().out

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from tracecite_mobile.commands.analysis import cmd_filter
from tracecite_mobile.commands.device import dispatch_device_command
from tracecite_mobile.device.session import load_analysis_sessions
from tracecite_mobile.platforms.android.logger import session_state_path
from tracecite_mobile.shared.config import write_profile_template
from tracecite_mobile.cli import build_parser


def _filter_args(*, output_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        log_path=[],
        preset=None,
        grep="target-event",
        scenario=None,
        tag=None,
        out=None,
        snapshot=False,
        pid=None,
        tail_lines=None,
        line_from=None,
        line_to=None,
        last=None,
        since=None,
        until=None,
        segmenter="auto",
        format=None,
        from_sessions=True,
        merge_timeline=False,
        output_dir=str(output_dir),
        fold=False,
        platform="android",
        json=True,
    )


def _write_android_profile(root: Path, output_dir: Path) -> None:
    write_profile_template(root, platform="android")
    profile_path = root / ".tracecite" / "config.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile["log_output_dir"] = str(output_dir)
    profile["capture_output_dir"] = str(root / "captures")
    profile_path.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_android_session_state_feeds_filter_from_sessions(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    output_dir = tmp_path / "logs"
    output_dir.mkdir()
    _write_android_profile(tmp_path, output_dir)

    log_path = output_dir / "android_live_SERIAL.log"
    log_path.write_text(
        "08-09 10:00:00.000  123  123 E App: target-event\n",
        encoding="utf-8",
    )
    session_state_path(output_dir).write_text(
        json.dumps(
            {
                "platform": "android",
                "serial": "SERIAL",
                "package_name": "com.example.app",
                "pid": 123,
                "collector_pid": 456,
                "output_path": str(log_path),
                "started_at": "2026-08-09T10:00:00",
            }
        ),
        encoding="utf-8",
    )

    refs = load_analysis_sessions(output_dir, platform="android")
    assert list(refs) == ["SERIAL"]
    assert refs["SERIAL"].output_path == str(log_path)

    assert cmd_filter(_filter_args(output_dir=output_dir)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["match_records"] == 1
    assert payload["input_lineage"][0]["original"] == str(log_path.resolve())


def test_android_analysis_session_facade_reads_all_canonical_sessions(
    tmp_path: Path,
) -> None:
    sessions = []
    expected_paths = set()
    for serial in ("SERIAL-1", "SERIAL-2"):
        log_path = tmp_path / f"{serial}.log"
        log_path.write_text(
            "08-09 10:00:00.001  123  123 I App: ready\n",
            encoding="utf-8",
        )
        expected_paths.add(str(log_path))
        sessions.append(
            {
                "platform": "android",
                "serial": serial,
                "output_path": str(log_path),
                "started_at": "2026-08-09T10:00:00",
            }
        )
    (tmp_path / ".tracecite-sessions.json").write_text(
        json.dumps({"platform": "android", "sessions": sessions}),
        encoding="utf-8",
    )

    refs = load_analysis_sessions(tmp_path, platform="android")

    assert set(refs) == {"SERIAL-1", "SERIAL-2"}
    assert {ref.output_path for ref in refs.values()} == expected_paths


def test_android_archive_uses_backend_capability(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(["--platform", "android", "archive", "list", "--json"])

    assert dispatch_device_command(args) == 0
    assert json.loads(capsys.readouterr().out) == {
        "segments": [],
        "segment_count": 0,
    }

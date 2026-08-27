from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tracecite_mobile.device.archive import (
    ArchiveError,
    load_manifest,
    seal_hot_log,
)


def _write_hot(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")


def test_seal_hot_log_moves_content_to_archive(tmp_path: Path) -> None:
    log_dir = tmp_path / "log"
    hot = log_dir / "ios_live_iphone.log"
    _write_hot(
        hot,
        [
            "Aug 18 10:00:00 App[1] <Notice>: line one\n",
            "Aug 18 10:00:01 App[1] <Notice>: line two\n",
        ],
    )

    result, _ = seal_hot_log(hot, device_name="iphone")

    assert Path(result.sealed_path).is_file()
    sealed_text = Path(result.sealed_path).read_text(encoding="utf-8")
    assert "line one" in sealed_text
    assert "line two" in sealed_text
    assert hot.read_text(encoding="utf-8") == ""
    assert result.lines == 2
    assert result.bytes > 0

    device_dir = log_dir / ".archive" / "iphone"
    manifest = load_manifest(device_dir)
    assert len(manifest) == 1
    assert manifest[0].path == result.sealed_path


def test_seal_empty_hot_raises(tmp_path: Path) -> None:
    hot = tmp_path / "ios_live_empty.log"
    hot.write_text("", encoding="utf-8")
    try:
        seal_hot_log(hot, device_name="empty")
        raised = False
    except ArchiveError:
        raised = True
    assert raised

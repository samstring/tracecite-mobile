# -*- coding: utf-8 -*-
"""Snapshot 在高频写入 + hot rotate 并发下的压力/竞态测试。

不依赖真机：模拟 HotRotatingWriter 持续写盘，同时在另一线程用与
filter_text 相同的 shutil.copy2 做 snapshot，检测内容损坏与 --last 定界偏差。
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from tracecite_core.text_filter import filter_text
from tracecite_mobile.device import archive
from tracecite_mobile.plugins.segmenters import DeviceLogSegmenter

_LINE_RE = re.compile(
    r"^Jul 31 10:(?P<min>\d{2}):(?P<sec>\d{2}) DemoApp\(com\.example\.demo\.logging\)\[100\] "
    r"<Notice>: SEQ=(?P<seq>\d+) MARKER=(?P<marker>[A-Z0-9]+)\n$"
)


def _line(minute: int, second: int, seq: int, *, marker: str = "OK") -> str:
    return (
        f"Jul 31 10:{minute:02d}:{second:02d} DemoApp(com.example.demo.logging)[100] "
        f"<Notice>: SEQ={seq:06d} MARKER={marker}\n"
    )


def _parse_snapshot(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    seqs: List[int] = []
    bad_lines: List[int] = []
    for index, raw in enumerate(lines, start=1):
        if not raw.endswith("\n"):
            bad_lines.append(index)
            continue
        match = _LINE_RE.match(raw)
        if not match:
            bad_lines.append(index)
            continue
        seqs.append(int(match.group("seq")))
    dupes = len(seqs) - len(set(seqs))
    gaps = 0
    if seqs:
        ordered = sorted(set(seqs))
        for prev, curr in zip(ordered, ordered[1:]):
            if curr - prev > 1:
                gaps += 1
    return {
        "bytes": len(text.encode("utf-8")),
        "line_count": len(lines),
        "valid_records": len(seqs),
        "bad_lines": bad_lines,
        "duplicate_seqs": dupes,
        "seq_gaps": gaps,
        "min_seq": min(seqs) if seqs else None,
        "max_seq": max(seqs) if seqs else None,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _slow_copy2(source: Path, destination: Path, *, chunk_size: int, delay_sec: float) -> None:
    """刻意放慢 copy，放大与 rotate 的竞态窗口（模拟大文件 snapshot）。"""
    with source.open("rb") as src, destination.open("wb") as dst:
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            dst.write(chunk)
            if delay_sec:
                time.sleep(delay_sec)


@dataclass
class SnapshotAttempt:
    ok: bool
    reason: str = ""
    stats: dict = field(default_factory=dict)
    size_changed_during_copy: bool = False


class SnapshotUnderLoadTest(unittest.TestCase):
    def test_concurrent_snapshot_during_rotate_can_corrupt(self) -> None:
        """并发 copy2 + rotate：应能观测到损坏或非单调 seq（best-effort 复现）。"""
        segmenter = DeviceLogSegmenter()
        corrupt: List[SnapshotAttempt] = []
        attempts = 0
        stop = threading.Event()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hot = root / "ios_live_PhoneA.log"

            def writer() -> None:
                seq = 0
                with hot.open("w", encoding="utf-8") as fp:
                    writer_obj = archive.HotRotatingWriter(
                        fp,
                        hot_path=hot,
                        device_name="PhoneA",
                        hot_window_sec=30 * 60,
                        archive_interval_sec=0.05,
                    )
                    writer_obj.start_scheduler()
                    try:
                        while not stop.is_set():
                            minute = 10 + (seq // 3600) % 50
                            second = (seq // 60) % 60
                            writer_obj.write(_line(minute, second, seq))
                            seq += 1
                            if seq % 100 == 0:
                                writer_obj.check_due()
                            time.sleep(0.001)
                    finally:
                        writer_obj.close()

            def snapshooter() -> None:
                nonlocal attempts
                snap_dir = root / ".snapshots"
                snap_dir.mkdir(exist_ok=True)
                while not stop.is_set():
                    attempts += 1
                    dest = snap_dir / f"snap_{attempts:04d}.log"
                    if not hot.is_file():
                        time.sleep(0.005)
                        continue
                    size_before = hot.stat().st_size
                    _slow_copy2(hot, dest, chunk_size=16384, delay_sec=0.0003)
                    size_after = hot.stat().st_size if hot.is_file() else 0
                    stats = _parse_snapshot(dest)
                    size_changed = size_before != size_after
                    bad = bool(stats["bad_lines"]) or stats["duplicate_seqs"] > 0
                    if bad or size_changed:
                        corrupt.append(
                            SnapshotAttempt(
                                ok=not bad,
                                reason="bad_lines_or_dupes" if bad else "size_changed",
                                stats=stats,
                                size_changed_during_copy=size_changed,
                            )
                        )

            wt = threading.Thread(target=writer, name="writer", daemon=True)
            st = threading.Thread(target=snapshooter, name="snapshooter", daemon=True)
            wt.start()
            st.start()
            time.sleep(5.0)
            stop.set()
            wt.join(timeout=5)
            st.join(timeout=5)

        self.assertGreater(attempts, 30, f"snapshot 尝试次数过少: {attempts}")
        # ponytail: 竞态是概率性的；3s 压测下期望至少观测到一次异常信号
        msg = (
            f"attempts={attempts}, corrupt_signals={len(corrupt)}, "
            f"sample={corrupt[:3]}"
        )
        self.assertGreater(
            len(corrupt),
            0,
            f"未观测到 snapshot 异常（可能窗口太短）: {msg}",
        )

    def test_last_window_skews_when_snapshot_truncated(self) -> None:
        """人为截断 snapshot 尾部，--last 定界应偏离 live 末条时间。"""
        segmenter = DeviceLogSegmenter()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "live.log"
            lines = [_line(39, sec, seq) for seq, sec in enumerate(range(60))]
            live.write_text("".join(lines), encoding="utf-8")

            snap_dir = root / ".snapshots"
            snap_dir.mkdir()
            truncated = snap_dir / "truncated.log"
            # 去掉最后 10 行，模拟 copy 期间被 rotate 截断的尾部
            truncated.write_text("".join(lines[:-10]), encoding="utf-8")

            live_filter = filter_text(
                live,
                pattern="SEQ=",
                tag="live",
                segmenter=segmenter,
                snapshot=False,
                last="2m",
            )
            trunc_filter = filter_text(
                truncated,
                pattern="SEQ=",
                tag="trunc",
                segmenter=segmenter,
                snapshot=False,
                last="2m",
            )

        self.assertGreater(live_filter.match_records, trunc_filter.match_records)
        self.assertEqual(live_filter.match_records, 60)
        self.assertEqual(trunc_filter.match_records, 50)
        self.assertNotEqual(live_filter.time_to, trunc_filter.time_to)

    def test_filter_snapshot_io_amplification_on_large_file(self) -> None:
        """大文件 snapshot 路径会多次全文件扫描（性能信号）。"""
        segmenter = DeviceLogSegmenter()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / "big.log"
            # ~8MB：足够观察耗时，又不至于拖垮 CI
            bulk = [_line(30, (i % 60), i) for i in range(80_000)]
            live.write_text("".join(bulk), encoding="utf-8")
            size_mb = live.stat().st_size / (1024 * 1024)

            started = time.perf_counter()
            result = filter_text(
                live,
                pattern="SEQ=079999",
                tag="bench",
                segmenter=segmenter,
                snapshot=True,
                output_path=root / "out.log",
            )
            elapsed = time.perf_counter() - started

        self.assertIsNotNone(result.snapshot_path)
        self.assertGreater(result.snapshot_lines or 0, 70_000)
        # ponytail: 阈值宽松，只验证「大文件 snapshot 明显不是 O(1)」
        self.assertGreater(
            elapsed,
            0.15,
            f"{size_mb:.1f}MB 文件 snapshot+filter 耗时过短，可能未真实扫描: {elapsed:.3f}s",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

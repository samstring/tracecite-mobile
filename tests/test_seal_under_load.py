# -*- coding: utf-8 -*-
"""大体积 hot 在持续写入下的 seal（rename 切段）压测。"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tracecite_mobile.device import archive
from tracecite_mobile.device.archive import load_manifest, request_seal_hot, seal_hot_log

_LINE_RE = re.compile(
    r"^Jul 31 10:(?P<min>\d{2}):(?P<sec>\d{2}) DemoApp\(com\.example\.demo\.logging\)\[100\] "
    r"<Notice>: SEQ=(?P<seq>\d+) MARKER=OK\n$"
)

_TARGET_300MB = 300 * 1024 * 1024


def _line(minute: int, second: int, seq: int) -> str:
    return (
        f"Jul 31 10:{minute:02d}:{second:02d} DemoApp(com.example.demo.logging)[100] "
        f"<Notice>: SEQ={seq:06d} MARKER=OK\n"
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_hot_until_bytes(path: Path, min_bytes: int) -> tuple[int, int, str]:
    """流式写入 hot，返回 (行数, 字节数, sha256)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    seq = 0
    with path.open("wb") as fp:
        while fp.tell() < min_bytes:
            payload = _line(30, seq % 60, seq).encode("utf-8")
            fp.write(payload)
            digest.update(payload)
            seq += 1
    return seq, path.stat().st_size, digest.hexdigest()


def _spot_check_sealed(path: Path, *, line_count: int) -> None:
    with path.open("r", encoding="utf-8") as fp:
        first = fp.readline()
    if not _LINE_RE.match(first):
        raise AssertionError(f"首行格式异常: {first[:80]!r}")
    with path.open("rb") as fp:
        fp.seek(-128, os.SEEK_END)
        tail = fp.read().decode("utf-8", errors="replace")
    last = tail[tail.rfind("Jul 31 ") :]
    if not last.endswith("\n"):
        raise AssertionError("末行缺少换行")
    match = _LINE_RE.match(last)
    if not match:
        raise AssertionError(f"末行格式异常: {last[:80]!r}")
    if int(match.group("seq")) != line_count - 1:
        raise AssertionError(
            f"末行 seq 期望 {line_count - 1}，实际 {match.group('seq')}"
        )

def _parse_sealed(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)
    seqs: list[int] = []
    bad_lines: list[int] = []
    for index, raw in enumerate(lines, start=1):
        if not raw.endswith("\n"):
            bad_lines.append(index)
            continue
        match = _LINE_RE.match(raw)
        if not match:
            bad_lines.append(index)
            continue
        seqs.append(int(match.group("seq")))
    return {
        "bytes": len(text.encode("utf-8")),
        "line_count": len(lines),
        "valid_records": len(seqs),
        "bad_lines": bad_lines,
        "duplicate_seqs": len(seqs) - len(set(seqs)),
        "min_seq": min(seqs) if seqs else None,
        "max_seq": max(seqs) if seqs else None,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


class SealUnderLoadTest(unittest.TestCase):
    def test_seal_large_hot_is_fast_and_complete(self) -> None:
        """~8MB 静态 hot：seal 应近乎 O(1)，且段内容/manifest 完整。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hot = root / "ios_live_PhoneA.log"
            bulk = [_line(30, i % 60, i) for i in range(80_000)]
            hot.write_text("".join(bulk), encoding="utf-8")
            expected_bytes = hot.stat().st_size
            expected_sha = hashlib.sha256(hot.read_bytes()).hexdigest()

            started = time.perf_counter()
            result, _ = seal_hot_log(hot, device_name="PhoneA")
            elapsed = time.perf_counter() - started

            sealed = Path(result.sealed_path)
            stats = _parse_sealed(sealed)
            manifest = load_manifest(root / ".archive" / "PhoneA")

            self.assertEqual(result.bytes, expected_bytes)
            self.assertEqual(stats["bytes"], expected_bytes)
            self.assertEqual(stats["sha256"], expected_sha)
            self.assertEqual(stats["valid_records"], 80_000)
            self.assertEqual(stats["bad_lines"], [])
            self.assertEqual(stats["duplicate_seqs"], 0)
            self.assertEqual(hot.read_text(encoding="utf-8"), "")
            self.assertEqual(len(manifest), 1)
            self.assertEqual(manifest[0].path, result.sealed_path)
            # bounds 扫描为 O(n)，但不应出现 copy2 量级的二次读写；宽松上限防回归
            self.assertLess(
                elapsed,
                3.0,
                f"seal 耗时 {elapsed:.3f}s 异常: {expected_bytes / (1024 * 1024):.1f}MB",
            )

    def test_seal_during_writer_append_preserves_segment_integrity(self) -> None:
        """HotRotatingWriter 持续 append 时 cooperative seal：段内行完整、hot 可继续写。"""
        stop = threading.Event()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hot = root / "ios_live_PhoneA.log"

            fp = hot.open("w", encoding="utf-8")
            try:
                writer = archive.HotRotatingWriter(
                    fp,
                    hot_path=hot,
                    device_name="PhoneA",
                    hot_window_sec=30 * 60,
                    archive_interval_sec=30 * 60,
                )

                def append_loop() -> None:
                    seq = 0
                    while not stop.is_set():
                        writer.write(_line(30, seq % 60, seq))
                        seq += 1
                        time.sleep(0.00005)

                worker = threading.Thread(target=append_loop, daemon=True)
                worker.start()
                time.sleep(0.5)

                result = request_seal_hot(hot, device_name="PhoneA", timeout_sec=10.0)
                sealed = _parse_sealed(Path(result.sealed_path))
                post_hot_bytes = hot.stat().st_size if hot.is_file() else 0
                stop.set()
                worker.join(timeout=2.0)
                writer.close()

                self.assertGreater(sealed["bytes"], 200_000, "sealed 段过小，可能未捕获写入窗口")
                self.assertEqual(sealed["bad_lines"], [], f"损坏行: {sealed['bad_lines'][:5]}")
                self.assertEqual(sealed["duplicate_seqs"], 0)
                self.assertIsNotNone(sealed["min_seq"])
                self.assertIsNotNone(sealed["max_seq"])
                self.assertGreaterEqual(sealed["max_seq"], sealed["min_seq"])
                self.assertEqual(
                    sealed["valid_records"],
                    sealed["max_seq"] - sealed["min_seq"] + 1,
                )
                self.assertGreaterEqual(post_hot_bytes, 0)
            finally:
                fp.close()

    @unittest.skipUnless(
        os.environ.get("TRACECITE_SLOW_TESTS", "").lower() in ("1", "true", "yes"),
        "300MB seal 压测需 TRACECITE_SLOW_TESTS=1",
    )
    def test_seal_300mb_hot_preserves_bytes_and_renames(self) -> None:
        """~300MB 静态 hot：rename 切段后字节/行数/sha 一致，hot 清空。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hot = root / "ios_live_PhoneA.log"
            line_count, expected_bytes, expected_sha = _write_hot_until_bytes(
                hot, _TARGET_300MB
            )
            size_mb = expected_bytes / (1024 * 1024)
            self.assertGreaterEqual(expected_bytes, _TARGET_300MB)

            started = time.perf_counter()
            result, _ = seal_hot_log(hot, device_name="PhoneA")
            elapsed = time.perf_counter() - started

            sealed = Path(result.sealed_path)
            manifest = load_manifest(root / ".archive" / "PhoneA")

            self.assertEqual(result.bytes, expected_bytes)
            self.assertEqual(result.lines, line_count)
            self.assertEqual(sealed.stat().st_size, expected_bytes)
            self.assertEqual(_sha256_file(sealed), expected_sha)
            self.assertEqual(hot.read_text(encoding="utf-8"), "")
            self.assertEqual(len(manifest), 1)
            _spot_check_sealed(sealed, line_count=line_count)
            # ponytail: bounds 扫描 O(n)，300MB 允许到 180s；主要防 rename 失败/截断
            self.assertLess(
                elapsed,
                180.0,
                f"seal 耗时 {elapsed:.1f}s 异常: {size_mb:.1f}MB",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

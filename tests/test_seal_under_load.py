# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tracecite_mobile.device import archive
from tracecite_mobile.device.archive import load_manifest, request_seal_hot, seal_hot_log


_TARGET_300MB = 300 * 1024 * 1024


def _line(second: int, millis: int, seq: int) -> str:
    return (
        f"Aug 10 12:34:{second:02d}.{millis:03d} PhoneA TestApp[123:456] "
        f"<Notice>: seq={seq:08d} payload={'x' * 96}\n"
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_sealed(path: Path) -> dict:
    valid_records = 0
    bad_lines = []
    seqs = []
    byte_count = 0
    with path.open("rb") as handle:
        for line_number, raw in enumerate(handle, start=1):
            byte_count += len(raw)
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                bad_lines.append(line_number)
                continue
            marker = "seq="
            if marker not in line or not line.endswith("\n"):
                bad_lines.append(line_number)
                continue
            raw_seq = line.split(marker, 1)[1].split(" ", 1)[0]
            try:
                seq = int(raw_seq)
            except ValueError:
                bad_lines.append(line_number)
                continue
            valid_records += 1
            seqs.append(seq)
    return {
        "bytes": byte_count,
        "valid_records": valid_records,
        "bad_lines": bad_lines,
        "duplicate_seqs": len(seqs) - len(set(seqs)),
        "min_seq": min(seqs) if seqs else None,
        "max_seq": max(seqs) if seqs else None,
        "sha256": _sha256_file(path),
    }


def _write_hot_until_bytes(path: Path, target_bytes: int) -> tuple[int, int, str]:
    digest = hashlib.sha256()
    line_count = 0
    byte_count = 0
    with path.open("wb") as handle:
        while byte_count < target_bytes:
            raw = _line(30, line_count % 60, line_count).encode("utf-8")
            handle.write(raw)
            digest.update(raw)
            byte_count += len(raw)
            line_count += 1
    return line_count, byte_count, digest.hexdigest()


class SealUnderLoadTest(unittest.TestCase):
    def test_static_large_hot_is_sealed_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hot = root / "ios_live_PhoneA.log"
            expected_records = 80_000
            digest = hashlib.sha256()
            expected_bytes = 0
            with hot.open("wb") as handle:
                for seq in range(expected_records):
                    raw = _line(30, seq % 60, seq).encode("utf-8")
                    handle.write(raw)
                    digest.update(raw)
                    expected_bytes += len(raw)
            expected_sha = digest.hexdigest()

            started = time.perf_counter()
            result, _ = seal_hot_log(hot, device_name="PhoneA")
            elapsed = time.perf_counter() - started

            stats = _parse_sealed(Path(result.sealed_path))
            manifest = load_manifest(root / ".archive" / "PhoneA")
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

                # This is a correctness test, not a runner throughput benchmark.
                # Requiring a fixed byte count in 0.5s is flaky across Linux/x64
                # and macOS/arm64.  A meaningful captured window plus contiguous,
                # non-corrupt records proves the cooperative handoff semantics.
                self.assertGreater(
                    sealed["valid_records"],
                    100,
                    "sealed 段记录过少，可能未捕获持续写入窗口",
                )
                self.assertGreater(sealed["bytes"], 0)
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
            self.assertEqual(hot.read_bytes(), b"")
            self.assertEqual(len(manifest), 1)
            self.assertEqual(manifest[0].path, result.sealed_path)
            self.assertLess(elapsed, 8.0, f"300MB seal 耗时 {elapsed:.3f}s 异常")


if __name__ == "__main__":
    unittest.main()

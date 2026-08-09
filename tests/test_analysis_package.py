# -*- coding: utf-8 -*-
"""分析结果打包脚本测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile


# Tests and skills are siblings in this standalone repository.
SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "ios-analysis-package" / "scripts" / "package_analysis.py"


class AnalysisPackageTest(unittest.TestCase):
    def test_includes_matching_trace_from_toc_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            output_dir = root / "out"
            trace = input_dir / "capture_demo.trace"
            input_dir.mkdir()
            trace.mkdir()
            (trace / "data.txt").write_text("trace\n", encoding="utf-8")
            report = input_dir / "report.md"
            toc = input_dir / "capture_demo_toc.xml"
            report.write_text("# Report\n", encoding="utf-8")
            toc.write_text("<toc/>\n", encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--tag",
                    "demo",
                    "--report",
                    str(report),
                    "--extra",
                    str(toc),
                    "--output-dir",
                    str(output_dir),
                    "--json",
                ],
                check=True,
                text=True,
                capture_output=True,
            )

            result = json.loads(proc.stdout)
            self.assertEqual(Path(result["package_dir"]).parent.name, "out")
            self.assertRegex(Path(result["package_dir"]).name, r"^export_\d{8}T\d{4}$")
            with ZipFile(result["zip_path"]) as zf:
                names = set(zf.namelist())
            self.assertIn("analysis.md", names)
            self.assertIn("capture_demo_toc.xml", names)
            self.assertIn("capture_demo.trace/data.txt", names)

    def test_includes_latest_trace_when_trace_is_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            instrument_dir = root / "Instrument"
            output_dir = root / "out"
            input_dir.mkdir()
            instrument_dir.mkdir()
            report = input_dir / "report.md"
            report.write_text("# Report\n", encoding="utf-8")
            latest = instrument_dir / "capture_latest.trace"
            latest.mkdir()
            (latest / "data.txt").write_text("latest\n", encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--tag",
                    "latest",
                    "--report",
                    str(report),
                    "--instrument-dir",
                    str(instrument_dir),
                    "--output-dir",
                    str(output_dir),
                    "--json",
                ],
                check=True,
                text=True,
                capture_output=True,
            )

            result = json.loads(proc.stdout)
            with ZipFile(result["zip_path"]) as zf:
                names = set(zf.namelist())
            self.assertIn("capture_latest.trace/data.txt", names)

    def test_includes_raw_log_from_filtered_log_header_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            output_dir = root / "out"
            input_dir.mkdir()
            raw_log = input_dir / "ios_live_device.log"
            filtered_log = input_dir / "filtered_demo.log"
            report = input_dir / "report.md"
            raw_log.write_text("raw log\n", encoding="utf-8")
            filtered_log.write_text(
                f"# tracecite log filter\n# original_source: {raw_log}\n# ---\nfiltered\n",
                encoding="utf-8",
            )
            report.write_text("# Report\n", encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--tag",
                    "raw-log",
                    "--report",
                    str(report),
                    "--log",
                    str(filtered_log),
                    "--output-dir",
                    str(output_dir),
                    "--no-auto-trace",
                    "--json",
                ],
                check=True,
                text=True,
                capture_output=True,
            )

            result = json.loads(proc.stdout)
            with ZipFile(result["zip_path"]) as zf:
                names = set(zf.namelist())
            self.assertIn("filtered_demo.log", names)
            self.assertIn("raw_logs/ios_live_device.log", names)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Package an analysis report, logs, and trace artifacts into a zip file."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable
from zipfile import ZIP_DEFLATED, ZipFile


def _expand(path: str) -> Path:
    return Path(path).expanduser().resolve()


def _safe_tag(tag: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", tag.strip())
    return cleaned.strip("._") or "analysis"


def _copy_path(src: Path, dst_dir: Path) -> dict[str, str | int | bool]:
    if not src.exists():
        raise FileNotFoundError(str(src))

    dst = dst_dir / src.name
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, symlinks=True)
        size = sum(p.stat().st_size for p in dst.rglob("*") if p.is_file())
        return {
            "source": str(src),
            "archive_path": src.name,
            "is_dir": True,
            "size_bytes": size,
        }

    shutil.copy2(src, dst)
    return {
        "source": str(src),
        "archive_path": src.name,
        "is_dir": False,
        "size_bytes": dst.stat().st_size,
    }


def _copy_into_subdir(src: Path, dst_dir: Path, subdir: str) -> dict[str, str | int | bool]:
    target_dir = dst_dir / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    info = _copy_path(src, target_dir)
    info["archive_path"] = f"{subdir}/{info['archive_path']}"
    return info


def _zip_dir(src_dir: Path, zip_path: Path) -> None:
    with ZipFile(zip_path, "w", ZIP_DEFLATED) as zf:
        for path in sorted(src_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(src_dir))


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


def _trace_from_extra(extra: Path) -> Path | None:
    name = extra.name
    for suffix in ("_toc.xml", "_hangs.xml", "_hang_risks.xml", "_xctrace.log"):
        if name.endswith(suffix):
            candidate = extra.with_name(name[: -len(suffix)] + ".trace")
            if candidate.exists():
                return candidate
    return None


def _latest_trace(instrument_dir: Path) -> Path | None:
    if not instrument_dir.exists() or not instrument_dir.is_dir():
        return None
    traces = [path for path in instrument_dir.glob("*.trace") if path.exists()]
    if not traces:
        return None
    return max(traces, key=lambda path: path.stat().st_mtime)


def _auto_trace_paths(args: argparse.Namespace) -> list[Path]:
    traces = [_expand(raw) for raw in args.trace]
    for raw in args.extra:
        inferred = _trace_from_extra(_expand(raw))
        if inferred is not None:
            traces.append(inferred)
    if not traces and args.include_latest_trace:
        latest = _latest_trace(_expand(args.instrument_dir))
        if latest is not None:
            traces.append(latest)
    return _dedupe_paths(traces)


def _raw_log_from_filtered_log(log_path: Path) -> Path | None:
    if not log_path.is_file():
        return None
    try:
        with log_path.open(encoding="utf-8", errors="replace") as handle:
            for _ in range(40):
                line = handle.readline()
                if not line:
                    break
                if line.startswith("# original_source:"):
                    raw = line.split(":", 1)[1].strip()
                    if raw:
                        candidate = Path(raw).expanduser()
                        if candidate.exists():
                            return candidate.resolve()
                if line.startswith("# ---"):
                    break
    except OSError:
        return None
    return None


def _auto_raw_log_paths(args: argparse.Namespace) -> list[Path]:
    raw_logs = [_expand(raw) for raw in args.raw_log]
    if args.include_raw_log:
        for raw in args.log:
            inferred = _raw_log_from_filtered_log(_expand(raw))
            if inferred is not None:
                raw_logs.append(inferred)
    return _dedupe_paths(raw_logs)


def _next_package_dir(output_dir: Path, timestamp: str) -> tuple[str, Path]:
    base_name = f"export_{timestamp}"
    candidate = output_dir / base_name
    if not candidate.exists() and not (output_dir / f"{base_name}.zip").exists():
        return base_name, candidate
    for index in range(2, 1000):
        name = f"{base_name}_{index}"
        candidate = output_dir / name
        if not candidate.exists() and not (output_dir / f"{name}.zip").exists():
            return name, candidate
    raise FileExistsError(f"too many export packages for timestamp {timestamp}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Package a Markdown analysis report with filtered logs and trace artifacts.",
    )
    parser.add_argument("--tag", required=True, help="Package tag, for example capture_161922_perf")
    parser.add_argument("--report", required=True, help="analysis.md path")
    parser.add_argument("--log", action="append", default=[], help="Filtered log path; can repeat")
    parser.add_argument("--raw-log", action="append", default=[], help="Original raw log path; can repeat")
    parser.add_argument("--trace", action="append", default=[], help=".trace directory path; can repeat")
    parser.add_argument("--extra", action="append", default=[], help="Extra artifact path; can repeat")
    parser.add_argument(
        "--no-auto-raw-log",
        dest="include_raw_log",
        action="store_false",
        help="Do not auto-include original logs referenced by filtered logs",
    )
    parser.add_argument(
        "--instrument-dir",
        default="~/Desktop/TraceCite/Instrument",
        help="Directory used to auto-include the latest .trace when --trace is omitted",
    )
    parser.add_argument(
        "--no-auto-trace",
        dest="include_latest_trace",
        action="store_false",
        help="Do not auto-include a matching or latest .trace",
    )
    parser.add_argument(
        "--output-dir",
        default="~/Desktop/TraceCite/analysis",
        help="Destination directory for package folders and zip files",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON result")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tag = _safe_tag(args.tag)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M")
    output_dir = _expand(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    package_name, package_dir = _next_package_dir(output_dir, timestamp)
    package_dir.mkdir(parents=True, exist_ok=False)

    files: list[dict[str, str | int | bool]] = []
    try:
        report_src = _expand(args.report)
        report_info = _copy_path(report_src, package_dir)
        report_dst = package_dir / report_src.name
        if report_dst.name != "analysis.md":
            analysis_dst = package_dir / "analysis.md"
            report_dst.rename(analysis_dst)
            report_info["archive_path"] = "analysis.md"
        files.append(report_info)

        trace_paths = _auto_trace_paths(args)
        raw_log_paths = _auto_raw_log_paths(args)
        for path in [*[_expand(raw) for raw in args.log], *trace_paths, *[_expand(raw) for raw in args.extra]]:
            files.append(_copy_path(path, package_dir))
        for path in raw_log_paths:
            files.append(_copy_into_subdir(path, package_dir, "raw_logs"))

        manifest = {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "tag": tag,
            "package_name": package_name,
            "files": files,
        }
        manifest_path = package_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        zip_path = output_dir / f"{package_name}.zip"
        _zip_dir(package_dir, zip_path)
    except Exception:
        shutil.rmtree(package_dir, ignore_errors=True)
        raise

    result = {
        "package_dir": str(package_dir),
        "zip_path": str(zip_path),
        "manifest_path": str(package_dir / "manifest.json"),
        "file_count": len(files) + 1,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Package: {zip_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

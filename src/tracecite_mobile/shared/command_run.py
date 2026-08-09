"""Application command adapter for the shared :class:`AnalysisRun` contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from tracecite_core.run import AnalysisRun, RunFile, RunIntegrityError, RunWorkspace


class CommandRun:
    """Own one immutable command execution and its manifest.

    Scenario execution remains the richest pipeline, while standalone analysis
    and device collection use this small adapter so every path emits the same
    run identity, status, verdict, inputs, artifacts, and integrity metadata.
    """

    def __init__(
        self,
        *,
        name: str,
        kind: str,
        platform: Optional[str],
        run_root: Path,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.root = Path(run_root).expanduser().resolve()
        self.run = AnalysisRun(name=name, kind=kind, platform=platform)
        self.run.parameters = dict(parameters or {})
        self.workspace: RunWorkspace = self.run.workspace(self.root)
        self._input_index = 0
        self.run.write_manifest(self.root)

    def freeze_input(self, source: Path, *, role: str = "source_snapshot") -> Path:
        original = RunFile.from_path("source_original", source)
        if original.sha256 is None:
            raise RunIntegrityError(f"无法读取输入文件: {source}")
        frozen = self.workspace.freeze_input(source, index=self._input_index)
        self._input_index += 1
        self.run.add_input(
            frozen,
            role=role,
            metadata={
                "source_path": original.path,
                "source_size": original.size,
                "source_sha256_at_freeze": original.sha256,
            },
        )
        return frozen

    def freeze_inputs(self, sources: Iterable[Path]) -> list[Path]:
        return [self.freeze_input(source) for source in sources]

    def freeze_context(self, source: Optional[Path], *, role: str) -> Optional[Path]:
        if source is None or not Path(source).is_file():
            return None
        original = RunFile.from_path(role, source)
        frozen = self.workspace.freeze_context(source, name=role)
        self.run.add_input(
            frozen,
            role=role,
            metadata={
                "source_path": original.path,
                "source_sha256_at_freeze": original.sha256,
            },
        )
        return frozen

    def freeze_project_context(self, start_dir: Path, *, platform: str) -> None:
        from .project_paths import find_knowledge_path, find_profile_path

        self.freeze_context(find_profile_path(start_dir), role="project_profile")
        self.freeze_context(
            find_knowledge_path(start_dir, platform=platform),
            role="project_knowledge",
        )

    def add_artifact(
        self,
        path: Optional[Path | str],
        *,
        role: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if path is None:
            return
        candidate = Path(path).expanduser()
        if candidate.exists() and (candidate.is_file() or candidate.is_dir()):
            self.run.add_artifact(candidate, role=role, metadata=metadata)

    def write_json_artifact(self, name: str, payload: Any, *, role: str) -> Path:
        path = self.workspace.reports_dir / name
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        self.add_artifact(path, role=role)
        return path

    def complete(
        self,
        *,
        verdict: str = "passed",
        metrics: Optional[Dict[str, Any]] = None,
        assertions: Optional[Dict[str, Any]] = None,
        delivery: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        for item in (*self.run.inputs, *self.run.artifacts):
            item.verify()
        self.run.finish(
            status="completed",
            verdict=verdict,
            metrics=metrics,
            assertions=assertions,
            delivery=delivery,
        )
        manifest = self.run.write_manifest(self.root)
        self.workspace.cleanup_temp()
        return {
            "run_id": self.run.run_id,
            "status": self.run.status,
            "verdict": self.run.verdict,
            "manifest_path": str(manifest),
        }

    def fail(self, exc: Exception) -> Dict[str, Any]:
        self.run.finish(status="failed", verdict="error", error=str(exc))
        manifest = self.run.write_manifest(self.root)
        self.workspace.cleanup_temp()
        return {
            "run_id": self.run.run_id,
            "status": self.run.status,
            "verdict": self.run.verdict,
            "manifest_path": str(manifest),
        }

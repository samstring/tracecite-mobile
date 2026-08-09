"""场景报告输出器注册表与内置 Markdown 报告。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence, Union

from tracecite_core.run import AnalysisRun


class ReportOutputError(RuntimeError):
    """报告输出配置或渲染失败。"""


@dataclass(frozen=True)
class ReportContext:
    summary: Mapping[str, Any]
    run: AnalysisRun
    base_dir: Path
    run_dir: Path
    output_dir: Path


@dataclass(frozen=True)
class ReportArtifact:
    path: Path
    role: str = "report"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path).expanduser().resolve())
        object.__setattr__(self, "metadata", dict(self.metadata or {}))


ReportOutputter = Callable[
    [ReportContext, Mapping[str, Any]],
    Union[ReportArtifact, Iterable[ReportArtifact]],
]
_REPORT_OUTPUTTERS: Dict[str, ReportOutputter] = {}


def register_report_outputter(
    name: str, outputter: ReportOutputter, *, replace: bool = False
) -> None:
    """注册 ``output.reports[].type``。"""
    key = str(name).strip().lower()
    if not key:
        raise ValueError("report outputter 名不能为空")
    current = _REPORT_OUTPUTTERS.get(key)
    if current is not None and current is not outputter and not replace:
        raise ValueError(f"report outputter {key!r} 已注册")
    _REPORT_OUTPUTTERS[key] = outputter


def available_report_outputters() -> List[str]:
    return sorted(_REPORT_OUTPUTTERS)


def _resolve_report_path(
    raw: Any, *, context: ReportContext, default_name: str
) -> Path:
    if not raw:
        return context.output_dir / default_name
    path = Path(str(raw)).expanduser()
    if path.is_absolute() or ".." in path.parts:
        raise ReportOutputError(f"报告路径必须相对运行 reports 目录: {raw}")
    return context.output_dir / path


def _markdown_outputter(
    context: ReportContext, options: Mapping[str, Any]
) -> ReportArtifact:
    path = _resolve_report_path(
        options.get("path"), context=context, default_name="report.md"
    ).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = context.summary
    assertion_rows = (summary.get("assertions") or {}).get("assertions") or []
    lines = [
        f"# {summary.get('scenario') or context.run.name}",
        "",
        f"- run_id: `{context.run.run_id}`",
        f"- status: `{context.run.status}`",
        f"- verdict: `{context.run.verdict}`",
        f"- platform: `{summary.get('platform') or '-'}`",
        f"- segmenter: `{summary.get('segmenter') or '-'}`",
        f"- inputs: {len(summary.get('input_files') or [])}",
        f"- match_records: {summary.get('total_match_records') or 0}",
        f"- required_satisfied: `{bool(summary.get('required_satisfied'))}`",
        "",
        "## Assertions",
        "",
    ]
    if assertion_rows:
        for row in assertion_rows:
            marker = "PASS" if row.get("satisfied") else "FAIL"
            lines.append(
                f"- [{marker}] `{row.get('name')}` "
                f"type={row.get('kind')} hits={row.get('hits')}"
            )
    else:
        lines.append("- No assertions configured.")
    lines.extend(
        [
            "",
            "## Evidence",
            "",
            *[
                f"- `{row.get('output_path')}`"
                for row in summary.get("results") or []
                if isinstance(row, dict) and row.get("output_path")
            ],
            "",
            f"Manifest: `{context.run.manifest_path or ''}`",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return ReportArtifact(path=path, role="report", metadata={"format": "markdown"})


register_report_outputter("markdown", _markdown_outputter)


def render_reports(
    specs: Sequence[Union[str, Mapping[str, Any]]],
    *,
    context: ReportContext,
) -> List[ReportArtifact]:
    artifacts: List[ReportArtifact] = []
    for index, raw_spec in enumerate(specs):
        if isinstance(raw_spec, str):
            kind = raw_spec
            options: Dict[str, Any] = {}
        elif isinstance(raw_spec, Mapping):
            kind = str(raw_spec.get("type") or "")
            options = {
                str(key): value for key, value in raw_spec.items() if key != "type"
            }
        else:
            raise ReportOutputError(f"output.reports[{index}] 必须是字符串或对象")
        key = kind.strip().lower()
        outputter = _REPORT_OUTPUTTERS.get(key)
        if outputter is None:
            known = ", ".join(available_report_outputters()) or "(空)"
            raise ReportOutputError(f"未知 report outputter {key!r}（可用: {known}）")
        try:
            result = outputter(context, options)
        except Exception as exc:
            raise ReportOutputError(f"report outputter {key!r} 执行失败: {exc}") from exc
        produced = [result] if isinstance(result, ReportArtifact) else list(result)
        if any(not isinstance(item, ReportArtifact) for item in produced):
            raise ReportOutputError(
                f"report outputter {key!r} 必须返回 ReportArtifact 或其迭代"
            )
        for artifact in produced:
            if not artifact.path.is_file():
                raise ReportOutputError(
                    f"report outputter {key!r} 未生成声明文件: {artifact.path}"
                )
            if not artifact.path.resolve().is_relative_to(context.output_dir.resolve()):
                raise ReportOutputError(
                    f"report outputter {key!r} 产物必须位于 {context.output_dir}: "
                    f"{artifact.path}"
                )
            artifacts.append(artifact)
    return artifacts

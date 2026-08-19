# -*- coding: utf-8 -*-
"""项目级 profile 配置加载。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .output_layout import OutputLayout, write_default_output_config
from .constants import (
    ANDROID_ANALYSIS_OUTPUT_DIR,
    ANDROID_CAPTURE_OUTPUT_DIR,
    ANDROID_DEFAULT_CAPTURE_TEMPLATE,
    ANDROID_FILTER_PRESET_NAMES,
    ANDROID_LOG_OUTPUT_DIR,
    ANDROID_LOGCAT_FORMAT,
    DEFAULT_ATTACH_PROCESS,
    DEFAULT_ANALYSIS_OUTPUT_DIR,
    DEFAULT_CAPTURE_OUTPUT_DIR,
    DEFAULT_CAPTURE_TEMPLATE,
    DEFAULT_LOG_OUTPUT_DIR,
    DEFAULT_OUTPUT_ROOT_DIR,
    DEFAULT_PROCESS_NAME,
    DEFAULT_SUBSYSTEM,
    PROJECT_META_DIRNAME,
)
from ..analysis.knowledge import (
    KnowledgeError,
    add_filter_terms as knowledge_add_filter_terms,
    remove_filter_terms as knowledge_remove_filter_terms,
    load_project_knowledge,
    write_knowledge_template,
)
from tracecite_core.text_filter import (
    DEFAULT_FILTER_PRESET_SEEDS,
    combine_patterns,
    merge_terms,
    pattern_from_terms,
)
from .project_paths import (
    ensure_project_meta_gitignore,
    find_profile_path,
    project_meta_dir,
    resolve_profile_write_path,
)


class ProfileError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScenarioProfile:
    capture_template: str
    summarize: bool = True
    subsystem: Optional[str] = None
    launch_bundle_id: Optional[str] = None
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FilterPresetProfile:
    """filter --preset 的一条配置。

    ``terms`` 是字面量关键词（可随分析增长，匹配时转义）；``regex`` 是配置中的
    原始正则（保持正则语义，不转义）。``pattern`` 为两者并联后的生效结果。
    """

    pattern: str
    tag: str
    note: str = ""
    terms: Tuple[str, ...] = ()
    regex: str = ""

    def effective_terms(self) -> List[str]:
        return list(self.terms)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "tag": self.tag,
            "terms": list(self.terms),
            "pattern": self.pattern,
        }
        if self.regex:
            payload["regex"] = self.regex
        if self.note:
            payload["note"] = self.note
        return payload


@dataclass(frozen=True)
class ProjectProfile:
    source_path: Optional[Path]
    process_name: str
    subsystem: str
    log_output_dir: Path
    capture_output_dir: Path
    capture_template: str
    attach_process: str
    launch_bundle_id: Optional[str] = None
    scenarios: Dict[str, ScenarioProfile] = field(default_factory=dict)
    filter_presets: Dict[str, FilterPresetProfile] = field(default_factory=dict)
    # filter 未传 --grep/--preset 时的回落；preset 优先于 pattern
    default_filter_preset: Optional[str] = None
    default_filter_pattern: Optional[str] = None
    # hot 日志保留秒数；None = 用 CLI 默认（DEFAULT_HOT_WINDOW_SEC）
    hot_window_sec: Optional[int] = None
    # 统一分析产物输出目录；None 时沿用源文件同级 .filtered/
    analysis_output_dir: Optional[Path] = None
    # 分析阈值（新增场景无需碰代码即可按项目/场景调）：
    #   coverage_threshold  命中多少条提示「证据偏多建议收窄」（默认 200）
    #   template_threshold  模板折叠阈值；0=不折叠（默认），>0 命中达阈值自动生成
    analysis: Dict[str, Any] = field(default_factory=dict)
    # 命名文本格式注册表：name -> FormatSegmenter 参数 dict（start 正则等）。
    # 新增文本格式零代码：在此注册后，scenario parse.format 用名字引用。
    formats: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    platform: str = "ios"
    package_name: str = ""
    activity: str = ""
    device_serial: Optional[str] = None
    logcat_format: str = ANDROID_LOGCAT_FORMAT
    logcat_filters: Tuple[str, ...] = ()

    def analysis_get(self, key: str, default: Any) -> Any:
        return self.analysis.get(key, default)

    def filter_preset_table(self) -> Dict[str, tuple[str, str]]:
        """供 resolve_preset 使用：(pattern, tag)。"""
        return {
            name: (item.pattern, item.tag or name)
            for name, item in self.filter_presets.items()
        }

    def resolve_default_filter(self) -> Optional[tuple[str, str, str]]:
        """
        返回 (pattern, tag, source)。
        source: preset:<name> | pattern
        无配置时返回 None。
        """
        if self.default_filter_preset:
            name = self.default_filter_preset.strip()
            if not name:
                return None
            table = self.filter_preset_table()
            if name not in table:
                known = ", ".join(sorted(table)) or "(无)"
                raise ProfileError(
                    f"default_filter_preset={name!r} 不在 filter_presets 中（可选: {known}）"
                )
            pattern, tag = table[name]
            return pattern, tag, f"preset:{name}"
        if self.default_filter_pattern:
            pattern = self.default_filter_pattern.strip()
            if pattern:
                return pattern, "default", "pattern"
        return None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "source_path": str(self.source_path) if self.source_path else None,
            "platform": self.platform,
            "process_name": self.process_name,
            "subsystem": self.subsystem,
            "log_output_dir": str(self.log_output_dir),
            "capture_output_dir": str(self.capture_output_dir),
            "capture_template": self.capture_template,
            "attach_process": self.attach_process,
            "launch_bundle_id": self.launch_bundle_id,
            "default_filter_preset": self.default_filter_preset,
            "default_filter_pattern": self.default_filter_pattern,
            "hot_window_sec": self.hot_window_sec,
            "analysis_output_dir": str(self.analysis_output_dir) if self.analysis_output_dir else None,
            "analysis": self.analysis,
            "formats": self.formats,
            "scenarios": {
                name: scenario.to_dict()
                for name, scenario in sorted(self.scenarios.items())
            },
            "filter_presets": {
                name: preset.to_dict()
                for name, preset in sorted(self.filter_presets.items())
            },
        }
        if self.platform == "android":
            payload.update(
                {
                    "package_name": self.package_name,
                    "activity": self.activity,
                    "device_serial": self.device_serial,
                    "logcat_format": self.logcat_format,
                    "logcat_filters": list(self.logcat_filters),
                }
            )
        return payload


def _default_ios_scenarios() -> Dict[str, ScenarioProfile]:
    return {
        "interaction-hang": ScenarioProfile(
            capture_template="Time Profiler",
            note="排查点击无响应、主线程阻塞、交互卡顿。",
        ),
        "launch-slow": ScenarioProfile(
            capture_template="App Launch",
            note="排查冷启动慢、首屏渲染慢。",
        ),
        "network-spike": ScenarioProfile(
            capture_template="Network",
            subsystem="all",
            note="排查接口慢、网络峰值和超时。",
        ),
        "memory-growth": ScenarioProfile(
            capture_template="Allocations",
            note="排查持续涨内存和对象分配。",
        ),
    }


def _default_android_scenarios() -> Dict[str, ScenarioProfile]:
    return {
        "android-anr": ScenarioProfile(
            capture_template="perfetto-frame",
            note="排查 ANR / 主线程阻塞。",
        ),
        "android-slow-start": ScenarioProfile(
            capture_template="perfetto-startup",
            note="排查 Android 冷启动慢。",
        ),
        "android-memory-growth": ScenarioProfile(
            capture_template="perfetto-memory",
            note="排查持续涨内存。",
        ),
        "android-network-spike": ScenarioProfile(
            capture_template="perfetto-network",
            note="排查网络峰值与超时。",
        ),
    }


def _default_scenarios(platform: str = "ios") -> Dict[str, ScenarioProfile]:
    if platform == "android":
        return _default_android_scenarios()
    return _default_ios_scenarios()


_DEFAULT_FILTER_PRESET_NOTES: Dict[str, str] = {
    "profile-leak": "资料页相关（词由 grow term 积累）。",
    "apm-frame": "APM 帧率 / 页面 trace（词由 grow term 积累）。",
    "memory-leak": "泄漏相关（词由 grow term 积累）。",
    "user-behavior": "项目行为协议入口（默认无词；由项目 knowledge 或插件提供）。",
    "network-http": "项目网络协议入口（默认无词；由项目 knowledge 提供）。",
    "user-action": "项目操作协议入口（默认无词）。",
    "user-nav": "项目导航协议入口（默认无词）。",
    "system-lifecycle": "iOS 标准生命周期通知。",
    "system-memory": "iOS 标准内存压力与 Jetsam 信号。",
    "system-fault": "iOS 标准崩溃、watchdog 与系统故障信号。",
}

_ANDROID_FILTER_PRESET_NOTES: Dict[str, str] = {
    "android-anr": "ANR / 主线程阻塞（词由 grow term 积累）。",
    "android-crash": "崩溃 / 异常（词由 grow term 积累）。",
    "android-startup": "启动耗时（词由 grow term 积累）。",
    "android-memory": "内存 / OOM（词由 grow term 积累）。",
    "android-frame": "渲染帧率 / 卡顿（词由 grow term 积累）。",
    "android-network": "网络请求（词由 grow term 积累）。",
    "android-system": "Android framework 系统状态。",
    "android-custom": "项目自定义协议入口（默认无词）。",
}


def _default_filter_presets(platform: str = "ios") -> Dict[str, FilterPresetProfile]:
    """内置薄种子；分析增长的词在项目 filter_presets.terms。"""
    if platform == "android":
        names = ANDROID_FILTER_PRESET_NAMES
        notes = _ANDROID_FILTER_PRESET_NOTES
    else:
        names = tuple(DEFAULT_FILTER_PRESET_SEEDS)
        notes = _DEFAULT_FILTER_PRESET_NOTES
    return {
        name: FilterPresetProfile(
            pattern="",
            tag=name,
            note=notes.get(name, ""),
            terms=(),
        )
        for name in names
    }


def _coerce_terms(raw_terms: Any, *, key: str, profile_path: Path) -> List[str]:
    if raw_terms is None:
        return []
    if not isinstance(raw_terms, list):
        raise ProfileError(f"`filter_presets.{key}.terms` 必须是数组: {profile_path}")
    out: List[str] = []
    for item in raw_terms:
        t = str(item).strip()
        if t:
            out.append(t)
    return out


def _parse_filter_presets(
    raw_presets: Any,
    *,
    profile_path: Path,
    defaults: Dict[str, FilterPresetProfile],
) -> Dict[str, FilterPresetProfile]:
    """内置种子 + 项目 filter_presets。

    - 有 ``terms``：与同名种子 terms **合并增长**（项目追加，不删种子，除非 replace_terms）
    - 仅有 ``pattern``（正则配置）：整段覆盖
    - 新名：以项目 terms/pattern 为准
    """
    if raw_presets is None:
        return dict(defaults)
    if not isinstance(raw_presets, dict):
        raise ProfileError(f"`filter_presets` 必须是对象: {profile_path}")

    notes: Dict[str, str] = {name: item.note for name, item in defaults.items()}
    resolved: Dict[str, FilterPresetProfile] = dict(defaults)

    for name, item in raw_presets.items():
        key = str(name).strip()
        if not key:
            raise ProfileError(f"`filter_presets` 含空名称: {profile_path}")
        if not isinstance(item, dict):
            raise ProfileError(f"`filter_presets.{key}` 必须是对象: {profile_path}")

        tag = str(item.get("tag", key)).strip() or key
        if "note" in item:
            notes[key] = str(item.get("note") or "")

        project_terms = _coerce_terms(item.get("terms"), key=key, profile_path=profile_path)
        pattern = str(item.get("pattern", "")).strip()
        replace_terms = bool(item.get("replace_terms", False))

        seed = defaults.get(key)
        seed_terms = list(seed.terms) if seed and seed.terms else (
            seed.effective_terms() if seed else []
        )

        regex = pattern
        if project_terms:
            terms = project_terms if replace_terms else merge_terms(seed_terms, project_terms)
            # 配置中的原始正则与字面词并联生效，避免被转义破坏
            pattern = combine_patterns(regex, pattern_from_terms(terms))
        elif pattern:
            # 正则配置：整段 pattern 按正则原样生效（不拆词，拆词会因转义失真）
            terms = []
        elif seed:
            terms = seed_terms
            pattern = seed.pattern or ""
            regex = seed.regex
        else:
            # 允许空：preset 名占位，词由知识库增长后再用
            terms = []
            pattern = ""
            regex = ""

        # 允许空 pattern：preset 名占位，词由知识库增长后再用
        resolved[key] = FilterPresetProfile(
            pattern=pattern or "",
            tag=tag,
            note=notes.get(key, ""),
            terms=tuple(terms),
            regex=regex,
        )

    return resolved


def append_filter_preset_terms(
    preset_name: str,
    new_terms: Sequence[str],
    *,
    start_dir: Optional[Path] = None,
    note: Optional[str] = None,
    scenario: Optional[str] = None,
    platform: str = "ios",
) -> Dict[str, Any]:
    """把分析中发现的过滤词写入项目知识库（成长），不再堆进 Python。"""
    _ = note
    seed = list(DEFAULT_FILTER_PRESET_SEEDS.get(preset_name.strip(), []))
    try:
        result = knowledge_add_filter_terms(
            preset_name,
            new_terms,
            start_dir=start_dir,
            seed_terms=seed,
            scenario=scenario,
            platform=platform,
        )
    except KnowledgeError as exc:
        raise ProfileError(str(exc)) from exc

    # 仅全局词同步到 config.json 便于人眼查看；场景词只留在 knowledge
    if scenario:
        result.setdefault("profile_path", result.get("knowledge_path"))
        return result

    profile_path = find_profile_path(start_dir)
    if profile_path is not None:
        try:
            raw = json.loads(profile_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = None
        if isinstance(raw, dict):
            presets = raw.setdefault("filter_presets", {})
            if isinstance(presets, dict):
                name = preset_name.strip()
                entry = presets.get(name) if isinstance(presets.get(name), dict) else {}
                entry = dict(entry or {})
                entry["tag"] = str(entry.get("tag") or name)
                regex_pattern = (
                    "" if entry.get("terms") else str(entry.get("pattern") or "").strip()
                )
                entry["terms"] = list(result["project_terms"])
                # 配置中的原始正则保留在 pattern 里，交由加载时与字面词并联
                entry["pattern"] = regex_pattern or result["effective_pattern"]
                if note is not None:
                    entry["note"] = note
                presets[name] = entry
                raw["filter_presets"] = presets
                profile_path.write_text(
                    json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                result["profile_path"] = str(profile_path)

    result.setdefault("profile_path", result.get("knowledge_path"))
    return result


def remove_filter_preset_terms(
    preset_name: str,
    terms: Sequence[str],
    *,
    start_dir: Optional[Path] = None,
    scenario: Optional[str] = None,
    platform: str = "ios",
) -> Dict[str, Any]:
    """从项目知识库删除过滤词（成长裁剪）。"""
    try:
        result = knowledge_remove_filter_terms(
            preset_name,
            terms,
            start_dir=start_dir,
            scenario=scenario,
            platform=platform,
        )
    except KnowledgeError as exc:
        raise ProfileError(str(exc)) from exc

    if scenario:
        result.setdefault("profile_path", result.get("knowledge_path"))
        return result

    profile_path = find_profile_path(start_dir)
    if profile_path is not None:
        try:
            raw = json.loads(profile_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = None
        if isinstance(raw, dict):
            presets = raw.setdefault("filter_presets", {})
            if isinstance(presets, dict):
                name = preset_name.strip()
                entry = presets.get(name) if isinstance(presets.get(name), dict) else {}
                entry = dict(entry or {})
                entry["tag"] = str(entry.get("tag") or name)
                regex_pattern = (
                    "" if entry.get("terms") else str(entry.get("pattern") or "").strip()
                )
                entry["terms"] = list(result.get("project_terms") or [])
                entry["pattern"] = (
                    regex_pattern
                    or result.get("effective_pattern")
                    or entry.get("pattern")
                    or ""
                )
                presets[name] = entry
                raw["filter_presets"] = presets
                profile_path.write_text(
                    json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                result["profile_path"] = str(profile_path)

    result.setdefault("profile_path", result.get("knowledge_path"))
    return result


def default_profile(platform: str = "ios") -> ProjectProfile:
    if platform == "android":
        return ProjectProfile(
            source_path=None,
            process_name="",
            subsystem="all",
            log_output_dir=ANDROID_LOG_OUTPUT_DIR.expanduser().resolve(),
            capture_output_dir=ANDROID_CAPTURE_OUTPUT_DIR.expanduser().resolve(),
            capture_template=ANDROID_DEFAULT_CAPTURE_TEMPLATE,
            attach_process="",
            scenarios=_default_scenarios(platform),
            filter_presets=_default_filter_presets(platform),
            default_filter_preset="android-system",
            analysis_output_dir=ANDROID_ANALYSIS_OUTPUT_DIR.expanduser().resolve(),
            analysis={"coverage_threshold": 200, "template_threshold": 0},
            platform="android",
        )
    if platform == "ios":
        return ProjectProfile(
            source_path=None,
            process_name=DEFAULT_PROCESS_NAME,
            subsystem=DEFAULT_SUBSYSTEM,
            log_output_dir=DEFAULT_LOG_OUTPUT_DIR.expanduser().resolve(),
            capture_output_dir=DEFAULT_CAPTURE_OUTPUT_DIR.expanduser().resolve(),
            capture_template=DEFAULT_CAPTURE_TEMPLATE,
            attach_process=DEFAULT_ATTACH_PROCESS,
            scenarios=_default_scenarios(platform),
            filter_presets=_default_filter_presets(platform),
            analysis_output_dir=DEFAULT_ANALYSIS_OUTPUT_DIR.expanduser().resolve(),
            analysis={"coverage_threshold": 200, "template_threshold": 0},
            platform="ios",
        )
    platform_root = OutputLayout.load().output_root / platform
    return ProjectProfile(
        source_path=None,
        process_name="",
        subsystem="all",
        log_output_dir=(platform_root / "log").resolve(),
        capture_output_dir=(platform_root / "instrument").resolve(),
        capture_template="default",
        attach_process="",
        scenarios={},
        filter_presets={},
        analysis_output_dir=(platform_root / "runs").resolve(),
        analysis={"coverage_threshold": 200, "template_threshold": 0},
        platform=platform,
    )


def project_root_from_profile_path(profile_path: Path) -> Path:
    """``.tracecite/config.json`` → 项目根。"""
    parent = profile_path.parent
    if parent.name == PROJECT_META_DIRNAME:
        return parent.parent
    return parent


def _require_profile_path(raw: Dict[str, Any], key: str, profile_path: Path) -> Path:
    if key not in raw or not str(raw[key]).strip():
        raise ProfileError(
            f"配置文件缺少必填输出路径 `{key}`: {profile_path}\n"
            "或执行 tracecite-mobile profile init --force 重新生成。"
        )
    return Path(str(raw[key])).expanduser().resolve()


def _apply_knowledge_filter_terms(
    filter_presets: Dict[str, FilterPresetProfile],
    start_dir: Optional[Path],
    *,
    platform: str,
) -> Dict[str, FilterPresetProfile]:
    try:
        knowledge = load_project_knowledge(start_dir, platform=platform)
    except KnowledgeError as exc:
        # 静默降级会让 filter 用不完整词表，得出「无命中=无问题」的错误结论
        raise ProfileError(
            f"知识库不可用，已中止（避免使用不完整过滤词）: {exc}"
        ) from exc
    if not knowledge.filter_terms:
        return filter_presets
    merged = dict(filter_presets)
    for name, extra_terms in knowledge.filter_terms.items():
        if not extra_terms:
            continue
        current = merged.get(name)
        seed = list(DEFAULT_FILTER_PRESET_SEEDS.get(name, []))
        base_terms = list(current.terms) if current else list(seed)
        terms = merge_terms(base_terms, extra_terms)
        regex = current.regex if current else ""
        merged[name] = FilterPresetProfile(
            pattern=combine_patterns(regex, pattern_from_terms(terms)),
            tag=(current.tag if current else name),
            note=(
                current.note
                if current
                else _DEFAULT_FILTER_PRESET_NOTES.get(name, "")
            ),
            terms=tuple(terms),
            regex=regex,
        )
    return merged


def load_project_profile(start_dir: Optional[Path] = None, platform: str = "ios") -> ProjectProfile:
    defaults = default_profile(platform)
    profile_path = find_profile_path(start_dir)
    if profile_path is None:
        return ProjectProfile(
            source_path=None,
            process_name=defaults.process_name,
            subsystem=defaults.subsystem,
            log_output_dir=defaults.log_output_dir,
            capture_output_dir=defaults.capture_output_dir,
            capture_template=defaults.capture_template,
            attach_process=defaults.attach_process,
            launch_bundle_id=defaults.launch_bundle_id,
            scenarios=defaults.scenarios,
            filter_presets=_apply_knowledge_filter_terms(
                defaults.filter_presets, start_dir, platform=platform
            ),
            default_filter_preset=defaults.default_filter_preset,
            default_filter_pattern=defaults.default_filter_pattern,
            hot_window_sec=defaults.hot_window_sec,
            analysis_output_dir=defaults.analysis_output_dir,
            analysis=defaults.analysis,
            formats=defaults.formats,
            platform=platform,
            package_name=defaults.package_name,
            activity=defaults.activity,
            device_serial=defaults.device_serial,
            logcat_format=defaults.logcat_format,
            logcat_filters=defaults.logcat_filters,
        )

    try:
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileError(f"配置文件不是合法 JSON: {profile_path}\n{exc}") from exc

    if not isinstance(raw, dict):
        raise ProfileError(f"配置文件顶层必须是对象: {profile_path}")
    known_keys = {
        "platform", "package_name", "activity", "device_serial",
        "logcat_format", "logcat_filters",
        "process_name", "subsystem", "log_output_dir", "capture_output_dir",
        "analysis_output_dir", "capture_template",
        "attach_process", "launch_bundle_id", "scenarios", "filter_presets",
        "default_filter_preset", "default_filter_pattern", "hot_window_sec",
        "analysis", "formats", "source_path",
    }
    unknown = set(raw.keys()) - known_keys
    if unknown:
        import warnings
        warnings.warn(f"config.json 未知字段将被忽略: {sorted(unknown)}", stacklevel=2)

    configured_platform = str(raw.get("platform", platform)).strip().lower()
    from ..platforms.registry import available_platforms

    if configured_platform not in set(available_platforms()):
        raise ProfileError(
            f"`platform` 未注册: {configured_platform!r}（可用: "
            f"{', '.join(available_platforms())}）: {profile_path}"
        )
    if configured_platform != platform:
        raise ProfileError(
            f"配置平台为 {configured_platform}，当前命令平台为 {platform}: {profile_path}"
        )

    raw_logcat_filters = raw.get("logcat_filters", defaults.logcat_filters)
    if not isinstance(raw_logcat_filters, (list, tuple)):
        raise ProfileError(f"`logcat_filters` 必须是数组: {profile_path}")

    scenarios = dict(defaults.scenarios)
    raw_scenarios = raw.get("scenarios")
    if raw_scenarios is not None:
        if not isinstance(raw_scenarios, dict):
            raise ProfileError(f"`scenarios` 必须是对象: {profile_path}")
        scenarios = {}
        for name, item in raw_scenarios.items():
            if not isinstance(item, dict):
                raise ProfileError(f"`scenarios.{name}` 必须是对象: {profile_path}")
            scenarios[name] = ScenarioProfile(
                capture_template=str(item.get("capture_template", defaults.capture_template)),
                summarize=bool(item.get("summarize", True)),
                subsystem=(
                    str(item["subsystem"])
                    if item.get("subsystem") is not None
                    else None
                ),
                launch_bundle_id=(
                    str(item["launch_bundle_id"])
                    if item.get("launch_bundle_id") is not None
                    else None
                ),
                note=str(item.get("note", "")),
            )

    default_filter_preset = raw.get("default_filter_preset", defaults.default_filter_preset)
    if default_filter_preset is not None:
        default_filter_preset = str(default_filter_preset).strip() or None
    default_filter_pattern = raw.get(
        "default_filter_pattern", defaults.default_filter_pattern
    )
    if default_filter_pattern is not None:
        default_filter_pattern = str(default_filter_pattern).strip() or None

    hot_window_sec = raw.get("hot_window_sec", defaults.hot_window_sec)
    if hot_window_sec is not None:
        try:
            hot_window_sec = int(hot_window_sec)
        except (TypeError, ValueError) as exc:
            raise ProfileError(
                f"`hot_window_sec` 必须是整数秒: {profile_path}"
            ) from exc
        if hot_window_sec < 60:
            raise ProfileError(
                f"`hot_window_sec` 不能小于 60 秒: {profile_path}"
            )

    filter_presets = _parse_filter_presets(
        raw.get("filter_presets"),
        profile_path=profile_path,
        defaults=defaults.filter_presets,
    )
    filter_presets = _apply_knowledge_filter_terms(
        filter_presets,
        project_root_from_profile_path(profile_path),
        platform=platform,
    )

    if default_filter_preset and default_filter_preset not in filter_presets:
        known = ", ".join(sorted(filter_presets)) or "(无)"
        raise ProfileError(
            f"default_filter_preset={default_filter_preset!r} 不在 filter_presets 中"
            f"（可选: {known}）: {profile_path}"
        )

    return ProjectProfile(
        source_path=profile_path,
        process_name=str(raw.get("process_name", defaults.process_name)),
        subsystem=str(raw.get("subsystem", defaults.subsystem)),
        log_output_dir=_require_profile_path(raw, "log_output_dir", profile_path),
        capture_output_dir=_require_profile_path(raw, "capture_output_dir", profile_path),
        capture_template=str(raw.get("capture_template", defaults.capture_template)),
        attach_process=str(raw.get("attach_process", defaults.attach_process)),
        launch_bundle_id=(
            str(raw["launch_bundle_id"])
            if raw.get("launch_bundle_id") is not None
            else defaults.launch_bundle_id
        ),
        scenarios=scenarios,
        filter_presets=filter_presets,
        default_filter_preset=default_filter_preset,
        default_filter_pattern=default_filter_pattern,
        hot_window_sec=hot_window_sec,
        analysis_output_dir=(
            Path(str(raw["analysis_output_dir"])).expanduser().resolve()
            if raw.get("analysis_output_dir")
            else defaults.analysis_output_dir
        ),
        analysis={**defaults.analysis, **(raw.get("analysis") or {})},
        formats={**defaults.formats, **(raw.get("formats") or {})},
        platform=platform,
        package_name=str(raw.get("package_name", defaults.package_name)),
        activity=str(raw.get("activity", defaults.activity)),
        device_serial=(
            str(raw["device_serial"])
            if raw.get("device_serial") is not None
            else defaults.device_serial
        ),
        logcat_format=str(raw.get("logcat_format", defaults.logcat_format)),
        logcat_filters=tuple(str(item) for item in raw_logcat_filters),
    )


def write_profile_template(
    destination: Path, *, overwrite: bool = False, platform: str = "ios"
) -> Path:
    meta_dir = project_meta_dir(destination)
    path = resolve_profile_write_path(destination)
    if path.exists() and not overwrite:
        raise ProfileError(f"配置文件已存在: {path}")

    meta_dir.mkdir(parents=True, exist_ok=True)
    write_default_output_config()
    OutputLayout.load().ensure_mobile(platform)

    if platform == "android":
        template = _android_profile_template_dict()
    else:
        profile = default_profile(platform)
        template = profile.to_dict()
        template["source_path"] = None
        template["launch_bundle_id"] = None
        if platform == "ios":
            template["scenarios"]["launch-slow"]["launch_bundle_id"] = "com.example.app"
        # 可选回落；未传 --grep/--preset 时才生效。勿在模板里塞业务示例 pattern。
        template["default_filter_preset"] = None
        template["default_filter_pattern"] = None
        # filter_presets.terms 占位为空；关键词在 knowledge.json，经 grow 积累
        for name, item in template.get("filter_presets", {}).items():
            if not isinstance(item, dict):
                continue
            item["terms"] = []
            item["pattern"] = pattern_from_terms(
                list(DEFAULT_FILTER_PRESET_SEEDS.get(name, []))
            )

    path.write_text(
        json.dumps(template, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    # 知识库 + gitignore（隐藏目录不被 git 跟踪）
    try:
        write_knowledge_template(destination, overwrite=overwrite, platform=platform)
    except KnowledgeError:
        pass
    ensure_project_meta_gitignore(destination)
    return path


def _android_profile_template_dict() -> Dict[str, Any]:
    """Android 项目配置模板：使用 Android 专属字段，不混入 iOS 字段。"""
    presets: Dict[str, Any] = {}
    for name in ANDROID_FILTER_PRESET_NAMES:
        presets[name] = {
            "tag": name,
            "terms": [],
            "pattern": "",
            "note": _ANDROID_FILTER_PRESET_NOTES.get(name, ""),
        }
    scenarios: Dict[str, Any] = {}
    for sid, sc in _default_android_scenarios().items():
        scenarios[sid] = {
            "capture_template": sc.capture_template,
            "summarize": sc.summarize,
            "note": sc.note,
        }
    return {
        "platform": "android",
        "package_name": "com.example.app",
        "process_name": "",
        "activity": "com.example.app.MainActivity",
        "device_serial": None,
        "log_output_dir": str(ANDROID_LOG_OUTPUT_DIR),
        "capture_output_dir": str(ANDROID_CAPTURE_OUTPUT_DIR),
        "analysis_output_dir": str(ANDROID_ANALYSIS_OUTPUT_DIR),
        "capture_template": ANDROID_DEFAULT_CAPTURE_TEMPLATE,
        "logcat_format": ANDROID_LOGCAT_FORMAT,
        "logcat_filters": [],
        "default_filter_preset": "android-system",
        "default_filter_pattern": None,
        "hot_window_sec": None,
        "scenarios": scenarios,
        "filter_presets": presets,
    }

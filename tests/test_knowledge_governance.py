from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracecite.knowledge import KnowledgeGovernanceError
from tracecite_mobile.analysis.knowledge import (
    add_learning,
    ensure_default_project_knowledge,
    load_project_knowledge,
)
from tracecite_mobile.analysis.knowledge_governance import (
    check_mobile_knowledge_integrity,
    promote_mobile_knowledge,
    propose_mobile_knowledge,
    verify_mobile_knowledge,
)
from tracecite_mobile.cli import main


def _proposal(root: Path):
    return propose_mobile_knowledge(
        kind="learning",
        payload={"summary": "Use bounded evidence", "tags": ["evidence"]},
        created_by="agent-a",
        case_id="run-1",
        evidence_refs=["evidence://run/1#event=1"],
        start_dir=root,
        platform="ios",
    )


def test_mobile_candidate_does_not_mutate_curated_knowledge(tmp_path: Path) -> None:
    ensure_default_project_knowledge(tmp_path)
    before = load_project_knowledge(tmp_path).to_dict()
    result = _proposal(tmp_path)
    after = load_project_knowledge(tmp_path).to_dict()

    assert result["candidate"]["status"] == "candidate"
    assert result["candidate_store"] != str(
        tmp_path / ".tracecite" / "knowledge.ios.json"
    )
    before.pop("updated_at", None)
    after.pop("updated_at", None)
    assert after == before


def test_mobile_candidate_requires_two_cases_and_distinct_reviewer(tmp_path: Path) -> None:
    proposed = _proposal(tmp_path)["candidate"]
    verified = verify_mobile_knowledge(
        proposed["id"],
        case_id="run-2",
        outcome="support",
        evidence_refs=["evidence://run/2#event=8"],
        verified_by="agent-b",
        start_dir=tmp_path,
    )["candidate"]
    assert verified["status"] == "verified"

    with pytest.raises(KnowledgeGovernanceError, match="不能批准自己"):
        promote_mobile_knowledge(
            proposed["id"], approved_by="agent-a", start_dir=tmp_path
        )

    promoted = promote_mobile_knowledge(
        proposed["id"], approved_by="human-reviewer", start_dir=tmp_path
    )["candidate"]
    assert promoted["status"] == "promoted"
    knowledge = load_project_knowledge(tmp_path)
    assert any(item.summary == "Use bounded evidence" for item in knowledge.learnings)
    assert check_mobile_knowledge_integrity(tmp_path)["status"] == "ok"


def test_direct_knowledge_write_is_detected(tmp_path: Path) -> None:
    _proposal(tmp_path)
    add_learning("unreviewed", start_dir=tmp_path)
    result = check_mobile_knowledge_integrity(tmp_path)
    assert result["status"] == "modified"
    with pytest.raises(KnowledgeGovernanceError, match="完整性检查"):
        propose_mobile_knowledge(
            kind="learning",
            payload={"summary": "second"},
            created_by="agent-a",
            case_id="run-2",
            evidence_refs=["evidence://run/2"],
            start_dir=tmp_path,
        )


def test_contradiction_blocks_mobile_promotion(tmp_path: Path) -> None:
    proposed = _proposal(tmp_path)["candidate"]
    contradicted = verify_mobile_knowledge(
        proposed["id"],
        case_id="run-2",
        outcome="contradict",
        evidence_refs=["evidence://run/2#counterexample"],
        verified_by="agent-b",
        start_dir=tmp_path,
    )["candidate"]
    assert contradicted["status"] == "contradicted"
    with pytest.raises(KnowledgeGovernanceError, match="不能晋升"):
        promote_mobile_knowledge(
            proposed["id"], approved_by="human-reviewer", start_dir=tmp_path
        )


def test_agent_cli_blocks_legacy_direct_writes(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["grow", "learning", "unsafe", "--evidence", "raw.log"]) == 2
    assert "禁止直接修改正式知识" in capsys.readouterr().err
    assert main(["preset", "add", "demo", "unsafe-term"]) == 2
    assert "禁止 preset add" in capsys.readouterr().err
    knowledge = load_project_knowledge(tmp_path)
    assert not any(item.summary == "unsafe" for item in knowledge.learnings)


def test_agent_cli_propose_verify_promote_flow(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(
        [
            "grow",
            "propose",
            "learning",
            "verified workflow",
            "--created-by",
            "agent-a",
            "--case-id",
            "run-1",
            "--evidence",
            "evidence://run/1#event=1",
            "--json",
        ]
    ) == 0
    proposed = json.loads(capsys.readouterr().out)
    candidate_id = proposed["candidate"]["id"]

    assert main(
        [
            "grow",
            "verify",
            candidate_id,
            "--case-id",
            "run-2",
            "--outcome",
            "support",
            "--evidence",
            "evidence://run/2#event=2",
            "--verified-by",
            "agent-b",
            "--json",
        ]
    ) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["candidate"]["status"] == "verified"

    assert main(
        [
            "grow",
            "promote",
            candidate_id,
            "--approved-by",
            "human-reviewer",
            "--json",
        ]
    ) == 0
    promoted = json.loads(capsys.readouterr().out)
    assert promoted["candidate"]["status"] == "promoted"

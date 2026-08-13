import json

import pytest

from qatc.cli import main
from qatc.config import AppConfig
from qatc.knowledge.store import KnowledgeStore


@pytest.fixture()
def cfg_env(tmp_path, monkeypatch):
    kroot = tmp_path / "knowledge"
    original = AppConfig.load

    def patched(cls=AppConfig):
        c = original()
        c.knowledge_root = str(kroot)
        return c

    monkeypatch.setattr(AppConfig, "load", staticmethod(patched))
    return kroot


@pytest.fixture()
def ready(cfg_env):
    main(["slot", "init", "파티편성", "--game", "starrail"])
    main(["slot", "set", "파티편성", "core_action", "--status", "filled",
          "--value", "파티를 짜고 적용한다"])
    return cfg_env


def _payload(title="정상 동작"):
    return json.dumps({
        "testcases": [{
            "title": title,
            "precondition": "파티 편성 화면",
            "steps": ["파티 적용을 누른다"],
            "expected": ["파티가 적용된다"],
            "rationale": "core_action 슬롯에서 도출",
        }]
    }, ensure_ascii=False)


def test_plan_lists_filled_family(ready, capsys):
    assert main(["tc", "plan", "파티편성", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "정상 경로" in [p["family"] for p in data["planned"]]


def test_plan_lists_skipped_with_reason(ready, capsys):
    main(["tc", "plan", "파티편성", "--json"])
    data = json.loads(capsys.readouterr().out)
    skipped = {s["family"]: s for s in data["skipped"]}
    assert skipped["재화 부족"]["status"] == "empty"
    assert skipped["재화 부족"]["slot"] == "cost"


def test_add_accepts_planned_family(ready, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", _StdIn(_payload()))
    rc = main(["tc", "add", "파티편성", "--family", "정상 경로",
               "--origin", "interview", "--json", "-"])
    assert rc == 0
    with KnowledgeStore(ready / "starrail.db") as s:
        assert [t.title for t in s.testcases("파티편성")] == ["정상 동작"]


def test_add_rejects_unplanned_family(ready, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", _StdIn(_payload()))
    rc = main(["tc", "add", "파티편성", "--family", "재화 부족",
               "--origin", "inferred", "--json", "-"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "재화 부족" in out
    assert "cost" in out
    assert "tc plan" in out


def test_add_rejects_unknown_family(ready, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _StdIn(_payload()))
    rc = main(["tc", "add", "파티편성", "--family", "없는계열",
               "--origin", "interview", "--json", "-"])
    assert rc == 1
    assert "알 수 없는 계열" in capsys.readouterr().out


def test_add_rejects_missing_required_field(ready, monkeypatch, capsys):
    bad = json.dumps({"testcases": [{"title": "제목만 있음"}]}, ensure_ascii=False)
    monkeypatch.setattr("sys.stdin", _StdIn(bad))
    rc = main(["tc", "add", "파티편성", "--family", "정상 경로",
               "--origin", "interview", "--json", "-"])
    assert rc == 1
    assert "steps" in capsys.readouterr().out


def test_add_sets_kind_and_priority_from_family(ready, monkeypatch):
    monkeypatch.setattr("sys.stdin", _StdIn(_payload()))
    main(["tc", "add", "파티편성", "--family", "정상 경로",
          "--origin", "interview", "--json", "-"])
    with KnowledgeStore(ready / "starrail.db") as s:
        tc = s.testcases("파티편성")[0]
    assert tc.kind.value == "정상"
    assert tc.priority.value == "High"
    assert tc.origin.value == "인터뷰"
    assert tc.category_major == "파티편성"
    assert tc.category_minor == "정상 경로"


def test_list_shows_unmet_slots(ready, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _StdIn(_payload()))
    main(["tc", "add", "파티편성", "--family", "정상 경로",
          "--origin", "interview", "--json", "-"])
    assert main(["tc", "list", "파티편성"]) == 0
    out = capsys.readouterr().out
    assert "정상 동작" in out
    assert "재화 부족" in out  # 미충족 리포트


def test_add_rejects_unknown_status_slot_family(ready, monkeypatch, capsys):
    # cost 슬롯을 "모른다"로 답한 상태 — empty 와 다른 사유여야 한다.
    main(["slot", "set", "파티편성", "cost", "--status", "unknown"])
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", _StdIn(_payload()))
    rc = main(["tc", "add", "파티편성", "--family", "재화 부족",
               "--origin", "inferred", "--json", "-"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "cost" in out
    assert "사용자가 모른다고 답함" in out
    assert "슬롯이 비어 있음" not in out
    assert "해당 없음으로 표시됨" not in out


def test_add_rejects_na_status_slot_family(ready, monkeypatch, capsys):
    # cost 슬롯이 "해당 없음"으로 표시된 상태 — empty/unknown 과 다른 사유여야 한다.
    main(["slot", "set", "파티편성", "cost", "--status", "na"])
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", _StdIn(_payload()))
    rc = main(["tc", "add", "파티편성", "--family", "재화 부족",
               "--origin", "inferred", "--json", "-"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "cost" in out
    assert "해당 없음으로 표시됨" in out
    assert "슬롯이 비어 있음" not in out
    assert "사용자가 모른다고 답함" not in out


def test_add_reports_correct_added_and_kept_counts(ready, monkeypatch, capsys):
    payload = json.dumps({
        "testcases": [
            {
                "title": "정상 동작 1",
                "precondition": "파티 편성 화면",
                "steps": ["파티 적용을 누른다"],
                "expected": ["파티가 적용된다"],
                "rationale": "core_action 슬롯에서 도출",
            },
            {
                "title": "정상 동작 2",
                "precondition": "파티 편성 화면",
                "steps": ["다른 파티를 적용한다"],
                "expected": ["다른 파티가 적용된다"],
                "rationale": "core_action 슬롯에서 도출",
            },
        ]
    }, ensure_ascii=False)
    monkeypatch.setattr("sys.stdin", _StdIn(payload))
    rc = main(["tc", "add", "파티편성", "--family", "정상 경로",
               "--origin", "interview", "--json", "-"])
    assert rc == 0
    out = capsys.readouterr().out
    # 페이로드의 testcases 수(2)와 정확히 일치해야 한다 — (added, kept) 언패킹
    # 순서가 뒤바뀌면 "TC 0건 저장 · 사람 손댄 2건 보존"처럼 거짓 실패로 보인다.
    assert "TC 2건 저장" in out
    assert "보존" not in out
    with KnowledgeStore(ready / "starrail.db") as s:
        assert len(s.testcases("파티편성")) == 2


def test_add_stores_inferred_origin(ready, monkeypatch):
    monkeypatch.setattr("sys.stdin", _StdIn(_payload()))
    main(["tc", "add", "파티편성", "--family", "정상 경로",
          "--origin", "inferred", "--json", "-"])
    with KnowledgeStore(ready / "starrail.db") as s:
        tc = s.testcases("파티편성")[0]
    assert tc.origin.value == "추론됨"


def test_add_stores_user_origin(ready, monkeypatch):
    monkeypatch.setattr("sys.stdin", _StdIn(_payload()))
    main(["tc", "add", "파티편성", "--family", "정상 경로",
          "--origin", "user", "--json", "-"])
    with KnowledgeStore(ready / "starrail.db") as s:
        tc = s.testcases("파티편성")[0]
    assert tc.origin.value == "사용자추가"


class _StdIn:
    def __init__(self, text: str):
        self._text = text

    def read(self) -> str:
        return self._text

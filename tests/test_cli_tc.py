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
    """필수 필드가 두 개 빠지면 실제 거부 메시지에 **둘 다** 나와야 한다.

    예전 판은 `rc == 1` 과 `"steps" in out` 만 봤고, 그 두 단언은 **검증이 0인
    구현에서도 둘 다 참**이었다 — 검증 블록을 통째로 지우면 `item["steps"]` 에서
    `KeyError: 'steps'` 가 나고 `cli.py` 의 범용 핸들러가 그것을
    `오류: KeyError: 'steps'` 로 출력하며 rc=1 을 돌려주기 때문이다.
    (실측: 블록 삭제 후 `pytest tests/test_cli_tc.py` → 13 passed.)
    실제 메시지를 고정해 그 구멍을 막는다.
    """
    bad = json.dumps({"testcases": [{"title": "제목만 있음"}]}, ensure_ascii=False)
    monkeypatch.setattr("sys.stdin", _StdIn(bad))
    rc = main(["tc", "add", "파티편성", "--family", "정상 경로",
               "--origin", "interview", "--json", "-"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "필수 필드가 없습니다" in out
    assert "steps" in out
    assert "expected" in out
    assert "KeyError" not in out          # 날 예외가 아니라 우리 메시지여야 한다


def test_add_missing_field_message_names_only_what_is_missing(ready, monkeypatch, capsys):
    """빠진 필드만 나열한다.

    필수 필드 세 개를 항상 찍는 구현으로는 통과할 수 없어야, 메시지가 실제로
    무엇이 빠졌는지 계산한다는 것이 고정된다.
    """
    bad = json.dumps({"testcases": [{"title": "t", "steps": ["s"]}]}, ensure_ascii=False)
    monkeypatch.setattr("sys.stdin", _StdIn(bad))
    rc = main(["tc", "add", "파티편성", "--family", "정상 경로",
               "--origin", "interview", "--json", "-"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "expected" in out
    assert "steps" not in out             # 있는 필드는 언급하지 않는다


def _one(**over):
    """유효한 TC 항목 하나를 만들고 지정한 키만 덮어쓴다."""
    item = {
        "title": "정상 동작",
        "steps": ["파티 적용을 누른다"],
        "expected": ["파티가 적용된다"],
    }
    item.update(over)
    return json.dumps({"testcases": [item]}, ensure_ascii=False)


def _stored(root):
    with KnowledgeStore(root / "starrail.db") as s:
        return s.testcases("파티편성")


def test_add_rejects_string_steps(ready, monkeypatch, capsys):
    """`"steps": "한 줄"` 은 조용히 글자 단위로 쪼개지면 안 된다.

    이 명령의 호출자는 LLM 이고, 배열이어야 할 자리에 문자열을 주는 것은 가장
    흔한 JSON 형태 오류다. 예전에는 truthiness 검사만 통과하면 문자열을 순회해
    `['한', ' ', '줄']` 이 rc=0 + 성공 메시지와 함께 최종 xlsx 절차 칸까지 갔다.
    """
    monkeypatch.setattr("sys.stdin", _StdIn(_one(steps="한 줄")))
    rc = main(["tc", "add", "파티편성", "--family", "정상 경로",
               "--origin", "interview", "--json", "-"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "testcases[0].steps" in out      # 어느 필드가 틀렸는지 짚는다
    assert "배열" in out                     # 다음 조치
    assert _stored(ready) == []              # 쓰레기가 저장되지 않았다


def test_add_rejects_string_expected(ready, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _StdIn(_one(expected="한 줄")))
    rc = main(["tc", "add", "파티편성", "--family", "정상 경로",
               "--origin", "interview", "--json", "-"])
    assert rc == 1
    assert "testcases[0].expected" in capsys.readouterr().out
    assert _stored(ready) == []


def test_add_rejects_non_dict_item(ready, monkeypatch, capsys):
    """항목이 객체가 아니면 날 `AttributeError` 대신 다음 조치를 알린다."""
    bad = json.dumps({"testcases": [["제목만 든 배열"]]}, ensure_ascii=False)
    monkeypatch.setattr("sys.stdin", _StdIn(bad))
    rc = main(["tc", "add", "파티편성", "--family", "정상 경로",
               "--origin", "interview", "--json", "-"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "testcases[0]" in out
    assert "AttributeError" not in out      # 파이썬 타입명이 새어나오면 안 된다


def test_add_rejects_non_string_list_element(ready, monkeypatch, capsys):
    """배열 원소가 문자열이 아니면 `str()` 로 뭉개지 않고 거부한다."""
    monkeypatch.setattr("sys.stdin", _StdIn(_one(steps=[{"a": 1}])))
    rc = main(["tc", "add", "파티편성", "--family", "정상 경로",
               "--origin", "interview", "--json", "-"])
    assert rc == 1
    assert "testcases[0].steps[0]" in capsys.readouterr().out
    assert _stored(ready) == []


def test_add_rejects_unknown_priority(ready, monkeypatch, capsys):
    """잘못된 priority 는 날 `ValueError` 대신 유효값을 알려주며 거부한다."""
    monkeypatch.setattr("sys.stdin", _StdIn(_one(priority="긴급")))
    rc = main(["tc", "add", "파티편성", "--family", "정상 경로",
               "--origin", "interview", "--json", "-"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "testcases[0].priority" in out
    assert "ValueError" not in out
    assert "High" in out                    # 유효값을 알려준다
    assert _stored(ready) == []


def test_add_names_the_offending_index(ready, monkeypatch, capsys):
    """여러 건 중 몇 번째가 틀렸는지 짚는다 — 앞의 두 건은 유효하다."""
    ok = {"title": "t", "steps": ["s"], "expected": ["e"]}
    payload = json.dumps({"testcases": [ok, ok, {**ok, "steps": "문자열"}]},
                         ensure_ascii=False)
    monkeypatch.setattr("sys.stdin", _StdIn(payload))
    rc = main(["tc", "add", "파티편성", "--family", "정상 경로",
               "--origin", "interview", "--json", "-"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "testcases[2].steps" in out
    assert "testcases[0]" not in out
    # 한 건이라도 틀리면 아무것도 저장하지 않는다
    assert _stored(ready) == []


def test_add_accepts_valid_priority_override(ready, monkeypatch):
    """검증이 정상 입력까지 막지 않는지 — 유효한 priority 는 그대로 쓰인다."""
    monkeypatch.setattr("sys.stdin", _StdIn(_one(priority="Low")))
    assert main(["tc", "add", "파티편성", "--family", "정상 경로",
                 "--origin", "interview", "--json", "-"]) == 0
    assert _stored(ready)[0].priority.value == "Low"


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

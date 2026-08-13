"""구조화 출력 JSON 스키마.

**모든 객체에 ``additionalProperties: false``와 ``required``가 있어야 한다** —
없으면 API가 스키마를 거부한다. 지원되지 않는 제약(``minLength``, ``maximum``,
재귀 스키마)은 쓰지 않는다.

스키마를 좁게 유지하는 이유는 두 가지다. 모델이 채워야 할 칸이 적을수록 정확하고,
필드가 적을수록 응답 토큰이 줄어 비용과 잘림 위험이 함께 내려간다.
"""

from __future__ import annotations

from typing import Any


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


#: 화면 명명 — 여러 화면을 한 번에 처리한다 (호출 수를 줄이기 위해).
SCREEN_NAMING = _obj(
    {
        "screens": {
            "type": "array",
            "items": _obj(
                {
                    "index": {
                        "type": "integer",
                        "description": "입력에서 제시된 화면 번호. 반드시 그대로 되돌려줄 것.",
                    },
                    "name": {
                        "type": "string",
                        "description": "화면 이름. QA 문서에 그대로 쓸 수 있는 한국어 명사구. 예: '캐릭터 목록', '성유물 강화 결과'",
                    },
                    "category": {
                        "type": "string",
                        "description": "TC 대분류 후보. 예: '캐릭터', '상점', '가챠', '전투', '설정'",
                    },
                    "role": {
                        "type": "string",
                        "description": "이 화면이 게임에서 하는 일. 한 문장.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "0.0~1.0. 화면이 잘리거나 전환 중이면 낮게.",
                    },
                    "key_elements": {
                        "type": "array",
                        "description": "이 화면의 정체성을 결정하는 UI 요소 이름 (최대 6개).",
                        "items": {"type": "string"},
                    },
                },
                ["index", "name", "category", "role", "confidence", "key_elements"],
            ),
        }
    },
    ["screens"],
)


#: 화면 동일성 판별 — 애매한 쌍만 물어본다.
SCREEN_SAME = _obj(
    {
        "verdicts": {
            "type": "array",
            "items": _obj(
                {
                    "pair_index": {"type": "integer", "description": "입력에서 제시된 쌍 번호."},
                    "same_screen": {
                        "type": "boolean",
                        "description": "두 이미지가 게임의 '같은 화면'인가. 스크롤 위치, 선택 항목, 애니메이션 프레임 차이는 같은 화면으로 본다. 탭이 다르거나 패널이 교체되었으면 다른 화면이다.",
                    },
                    "reason": {"type": "string", "description": "판단 근거. 한 문장."},
                },
                ["pair_index", "same_screen", "reason"],
            ),
        }
    },
    ["verdicts"],
)


_TC_ITEM = _obj(
    {
        "category_major": {"type": "string", "description": "대분류. 화면의 category를 따를 것."},
        "category_minor": {"type": "string", "description": "중분류. 기능 단위."},
        "title": {"type": "string", "description": "TC 제목. 무엇을 검증하는지 한 줄."},
        "precondition": {
            "type": "string",
            "description": "테스트 시작 전 갖춰야 할 상태. 없으면 '없음'.",
        },
        "steps": {
            "type": "array",
            "description": "테스트 절차. 한 줄에 한 행동. 번호를 붙이지 말 것.",
            "items": {"type": "string"},
        },
        "expected": {
            "type": "array",
            "description": "기대 결과. steps와 같은 개수로, 각 단계에 대응시킬 것.",
            "items": {"type": "string"},
        },
        "priority": {"type": "string", "enum": ["High", "Medium", "Low"]},
        "kind": {
            "type": "string",
            "enum": ["정상", "경계값", "예외", "역방향", "중단", "UI/UX"],
        },
        "rationale": {
            "type": "string",
            "description": "이 TC를 만든 근거. 관측된 것인지 추론한 것인지 분명히 쓸 것.",
        },
        "edge_ids": {
            "type": "array",
            "description": "이 TC가 커버하는 전이 ID. 제시된 목록에 있는 것만 쓸 것. 없으면 빈 배열.",
            "items": {"type": "string"},
        },
    },
    [
        "category_major",
        "category_minor",
        "title",
        "precondition",
        "steps",
        "expected",
        "priority",
        "kind",
        "rationale",
        "edge_ids",
    ],
)

#: TC 생성 (정상 경로 / 파생 케이스 공통).
TESTCASES = _obj({"testcases": {"type": "array", "items": _TC_ITEM}}, ["testcases"])


# ---------------------------------------------------------------- 채팅 도구
#
# 여기부터는 구조화 출력이 아니라 **실제 tool use**다. LLM이 리뷰 워크스페이스의
# 상태를 직접 바꾼다. 사용자가 "3번이랑 5번 같은 화면이야"라고 말하면 LLM이
# merge_states를 호출하고 타임라인이 즉시 갱신된다.

CHAT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "rename_state",
        "description": (
            "화면의 이름·분류·역할을 확정한다. 사용자가 화면의 정체를 알려주거나 "
            "잘못된 이름을 바로잡을 때 호출한다. 여기서 설정한 값은 사용자 확정 정보로 "
            "저장되어 재분석에도 보존된다."
        ),
        "input_schema": _obj(
            {
                "state_id": {"type": "string", "description": "대상 화면 ID (예: st_003)"},
                "name": {"type": "string", "description": "새 화면 이름"},
                "category": {"type": "string", "description": "대분류. 바꾸지 않으려면 빈 문자열."},
                "role": {"type": "string", "description": "화면의 역할. 바꾸지 않으려면 빈 문자열."},
            },
            ["state_id", "name", "category", "role"],
        ),
    },
    {
        "name": "merge_states",
        "description": (
            "두 화면이 사실 같은 화면일 때 하나로 합친다. keep_id 쪽이 남고 absorb_id는 "
            "사라지며, 두 화면을 오가던 전이는 자기 자신으로의 전이가 된다. "
            "되돌릴 수 없으므로 사용자가 명확히 요청했을 때만 호출한다."
        ),
        "input_schema": _obj(
            {
                "keep_id": {"type": "string", "description": "남길 화면 ID"},
                "absorb_id": {"type": "string", "description": "흡수될 화면 ID"},
            },
            ["keep_id", "absorb_id"],
        ),
    },
    {
        "name": "add_note",
        "description": (
            "화면에 메모를 남긴다. TC 생성 시 함께 전달되므로, 화면에 대한 배경 지식이나 "
            "테스트 시 주의점을 기록하는 데 쓴다."
        ),
        "input_schema": _obj(
            {
                "state_id": {"type": "string"},
                "note": {"type": "string", "description": "메모 내용"},
            },
            ["state_id", "note"],
        ),
    },
    {
        "name": "mark_element",
        "description": (
            "화면의 특정 UI 요소에 의미 있는 이름을 붙인다. 검출된 요소 중 하나를 "
            "인덱스로 지정한다. 이 라벨은 TC 절차 문구에 '[강화하기] 버튼 클릭'처럼 쓰인다."
        ),
        "input_schema": _obj(
            {
                "state_id": {"type": "string"},
                "element_index": {"type": "integer", "description": "요소 목록에서의 인덱스"},
                "label": {"type": "string", "description": "요소 이름 (예: '강화하기 버튼')"},
            },
            ["state_id", "element_index", "label"],
        ),
    },
    {
        "name": "hide_state",
        "description": (
            "화면을 노이즈로 표시해 TC 생성에서 제외한다. 로딩 화면이나 잘못 잡힌 "
            "전환 프레임처럼 테스트 대상이 아닌 것에 쓴다."
        ),
        "input_schema": _obj(
            {
                "state_id": {"type": "string"},
                "hidden": {"type": "boolean", "description": "true면 숨김, false면 복원"},
            },
            ["state_id", "hidden"],
        ),
    },
    {
        "name": "delete_transition",
        "description": (
            "무의미한 전이를 제거한다. 화면 병합 후 생긴 '같은 화면으로의 전이'처럼 "
            "실제 동작이 아닌 것을 정리할 때 쓴다."
        ),
        "input_schema": _obj(
            {"transition_id": {"type": "string"}},
            ["transition_id"],
        ),
    },
]

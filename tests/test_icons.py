"""아이콘 사전 테스트.

지키는 것 셋:

1. **오매칭보다 미매칭이 낫다** — 아이콘을 못 알아보면 TC 문구가 좌표로 남을 뿐이지만,
   잘못 알아보면 "[구매] 클릭 → 재화가 차감된다" 같은 **사실과 다른 TC**가 나온다.
2. **자동 매칭이 사람의 판단을 덮지 않는다** — 담당자가 붙인 라벨을 자동 처리가
   지우면 사전의 신뢰가 무너진다.
3. **화면 참조는 이름 기준이다** — 화면 ID는 세션마다 새로 부여되므로 ID로 저장하면
   다음 세션에서 엉뚱한 화면을 가리킨다.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from qatc.icons import descriptor as desc
from qatc.icons.matcher import IconMatcher, unmatched_icon_elements
from qatc.icons.models import ActionKind, IconAction, IconEntry, IconSample
from qatc.icons.store import IconStore
from qatc.icons.suggest import suggest_for_element, suggest_from_transition
from qatc.models import (
    AutoFeatures,
    ElementKind,
    FlowGraph,
    InputEvent,
    InputKind,
    NormRect,
    ScreenState,
    Transition,
    UIElement,
)

ICON_COLORS = {
    "gacha": (200, 120, 240),
    "bag": (90, 180, 230),
    "mail": (240, 200, 90),
    "quest": (120, 230, 140),
}


def make_icon(kind: str, jitter: float = 0.0, bright: float = 1.0, seed: int = 0) -> np.ndarray:
    """게임 아이콘 스타일 패치: 둥근 배경 + 고유 형태."""
    rng = np.random.default_rng(seed)
    img = np.full((72, 72, 3), 30, np.uint8)
    c = tuple(int(v * bright) for v in ICON_COLORS[kind])
    cv2.circle(img, (36, 36), 32, tuple(int(v * 0.35) for v in c), -1)
    if kind == "gacha":
        cv2.drawMarker(img, (36, 36), c, cv2.MARKER_STAR, 40, 4)
    elif kind == "bag":
        cv2.rectangle(img, (20, 28), (52, 54), c, -1)
        cv2.rectangle(img, (28, 20), (44, 30), c, 3)
    elif kind == "mail":
        cv2.rectangle(img, (18, 26), (54, 50), c, -1)
        cv2.line(img, (18, 26), (36, 40), (30, 30, 30), 3)
        cv2.line(img, (54, 26), (36, 40), (30, 30, 30), 3)
    else:
        cv2.drawMarker(img, (36, 36), c, cv2.MARKER_DIAMOND, 38, 5)
    if jitter:
        img = np.clip(img + rng.normal(0, jitter, img.shape), 0, 255).astype(np.uint8)
    return img


@pytest.fixture()
def store(tmp_path) -> IconStore:
    return IconStore.load("testgame", tmp_path)


# ---------------------------------------------------------------- 디스크립터


def test_descriptor_has_expected_dim():
    assert desc.describe(make_icon("gacha")).shape == (desc.DIM,)


def test_descriptor_separates_different_icons():
    vectors = {k: desc.describe(make_icon(k)) for k in ICON_COLORS}
    same = desc.similarity(vectors["gacha"], desc.describe(make_icon("gacha", jitter=6)))
    others = [desc.similarity(vectors["gacha"], vectors[k]) for k in ICON_COLORS if k != "gacha"]
    assert same > max(others) + 0.15, f"같은 아이콘 {same:.3f} vs 다른 아이콘 {max(others):.3f}"


def test_descriptor_survives_scale_change():
    """검출 박스 크기가 조금 달라도 같은 아이콘으로 인식돼야 한다."""
    a = desc.describe(make_icon("bag"))
    b = desc.describe(cv2.resize(make_icon("bag"), (96, 96)))
    assert desc.similarity(a, b) > 0.95


def test_descriptor_rejects_empty_image():
    with pytest.raises(ValueError):
        desc.describe(np.zeros((0, 0, 3), np.uint8))


def test_patch_hash_is_stable_and_discriminating():
    icon = make_icon("mail")
    assert desc.hash_distance(desc.patch_hash(icon), desc.patch_hash(icon.copy())) == 0
    assert desc.hash_distance(desc.patch_hash(icon), desc.patch_hash(make_icon("quest"))) > 4


def test_crop_patch_bounds():
    image = np.full((200, 400, 3), 50, np.uint8)
    patch = desc.crop_patch(image, NormRect(0.9, 0.9, 0.2, 0.2))
    assert patch is not None and patch.size > 0     # 화면 밖으로 나가도 잘려서 반환
    assert desc.crop_patch(image, NormRect(0.5, 0.5, 0.0, 0.0)) is None


# ---------------------------------------------------------------- 저장소


def test_register_and_reload(store, tmp_path):
    action = IconAction(
        kind=ActionKind.NAVIGATE, target_screen_name="기원 화면",
        expected="기원 화면이 표시된다", consumes="성간 항행권", reversible=False,
    )
    entry = store.register("기원", make_icon("gacha"), action=action, screen_name="홈")
    store.save()

    reloaded = IconStore.load("testgame", tmp_path)
    back = reloaded.by_name("기원")
    assert back is not None
    assert back.action.kind is ActionKind.NAVIGATE
    assert back.action.consumes == "성간 항행권"
    assert back.action.reversible is False
    assert back.sample_count == 1
    assert reloaded.template_path(back) is not None


def test_screens_are_referenced_by_name_not_id(store):
    """**핵심 불변식** — 화면 ID는 세션마다 바뀌므로 이름으로 저장해야 한다."""
    entry = store.register(
        "가방", make_icon("bag"),
        action=IconAction(kind=ActionKind.NAVIGATE, target_screen_name="가방 화면"),
        screen_name="홈 화면",
    )
    assert entry.action.target_screen_name == "가방 화면"
    assert entry.seen_screens == ["홈 화면"]
    assert not any(s.startswith("st_") for s in entry.seen_screens)


def test_duplicate_samples_are_rejected(store):
    """같은 프레임을 여러 번 확정해도 학습에 보탬이 없고 투표만 치우친다."""
    entry = store.register("우편", make_icon("mail"))
    assert store.add_sample(entry.id, make_icon("mail")) is False
    assert entry.sample_count == 1


def test_varied_samples_accumulate(store):
    entry = store.register("임무", make_icon("quest"))
    for i in range(4):
        store.add_sample(entry.id, make_icon("quest", jitter=10, bright=1 - i * 0.06, seed=i))
    assert entry.sample_count > 1


def test_merge_combines_samples(store):
    a = store.register("기원", make_icon("gacha"))
    b = store.register("기원(중복)", make_icon("gacha", jitter=12, seed=3))
    before = a.sample_count + b.sample_count
    assert store.merge(a.id, b.id)
    assert store.get(a.id).sample_count == before
    assert store.get(b.id) is None


def test_delete_removes_entry(store):
    entry = store.register("가방", make_icon("bag"))
    assert store.delete(entry.id)
    assert store.get(entry.id) is None


def test_corrupt_dictionary_does_not_crash(tmp_path):
    """사전이 깨졌다고 앱이 못 뜨면 안 된다."""
    root = tmp_path / "testgame"
    root.mkdir(parents=True)
    (root / "dictionary.json").write_text("{ 깨진 JSON", encoding="utf-8")
    loaded = IconStore.load("testgame", tmp_path)
    assert len(loaded) == 0
    assert (root / "dictionary.json.corrupt").exists()


def test_stats_counts_complete_only(store):
    store.register("완성", make_icon("gacha"), action=IconAction(kind=ActionKind.NAVIGATE))
    store.register("미완성", make_icon("bag"))  # 동작 미지정
    assert store.stats() == {"icons": 2, "complete": 1, "samples": 2}


# ---------------------------------------------------------------- 매칭


@pytest.fixture()
def trained(store) -> tuple[IconStore, IconMatcher, dict[str, str]]:
    ids: dict[str, str] = {}
    for kind, name in (("gacha", "기원"), ("bag", "가방"), ("mail", "우편"), ("quest", "임무")):
        entry = store.register(
            name, make_icon(kind),
            action=IconAction(kind=ActionKind.NAVIGATE, target_screen_name=f"{name} 화면"),
            screen_name="홈", rect=NormRect(0.8, 0.05, 0.04, 0.05),
        )
        ids[kind] = entry.id
        for i in range(3):
            store.add_sample(
                entry.id, make_icon(kind, jitter=10, bright=1 - i * 0.05, seed=i),
                screen_name="홈", rect=NormRect(0.8, 0.05, 0.04, 0.05),
            )
    return store, IconMatcher(store), ids


def test_matches_known_icons(trained):
    _, matcher, ids = trained
    for kind in ICON_COLORS:
        result = matcher.match_patch(make_icon(kind, jitter=8, seed=9))
        assert result is not None, f"{kind} 미매칭"
        assert result.entry.id == ids[kind], f"{kind} 오매칭 → {result.entry.name}"


def test_rejects_unknown_icon(trained):
    """**오매칭 방지** — 등록 안 된 아이콘은 미매칭으로 남아야 한다."""
    _, matcher, _ = trained
    unknown = np.full((72, 72, 3), 30, np.uint8)
    cv2.circle(unknown, (36, 36), 30, (60, 60, 200), -1)
    cv2.drawMarker(unknown, (36, 36), (255, 255, 255), cv2.MARKER_TRIANGLE_UP, 36, 5)
    result = matcher.match_patch(unknown)
    assert result is None or not result.is_confident


def test_empty_dictionary_matches_nothing(store):
    assert IconMatcher(store).match_patch(make_icon("gacha")) is None


def test_learning_improves_robustness(store):
    """샘플이 늘수록 변형 조건에서 잘 맞아야 한다 — '데이터가 쌓일수록' 검증."""
    entry = store.register("기원", make_icon("gacha"), screen_name="홈")
    store.register("가방", make_icon("bag"), screen_name="홈")
    store.register("우편", make_icon("mail"), screen_name="홈")
    matcher = IconMatcher(store)

    hard = [make_icon("gacha", jitter=16, bright=b, seed=s) for s, b in enumerate((0.8, 1.2, 0.9))]
    before = sum(
        1 for p in hard if (m := matcher.match_patch(p)) and m.entry.id == entry.id
    )

    for i in range(5):
        matcher.confirm(entry.id, make_icon("gacha", jitter=12, bright=1 - i * 0.05, seed=100 + i))
    after = sum(
        1 for p in hard if (m := matcher.match_patch(p)) and m.entry.id == entry.id
    )
    assert after >= before


def test_correction_moves_sample(trained):
    """오분류 교정 — 틀린 쪽에서 빼고 맞는 쪽에 넣어야 경계가 고쳐진다."""
    store, matcher, ids = trained
    wrong, right = ids["gacha"], ids["bag"]
    before_wrong = store.get(wrong).sample_count
    assert matcher.correct(wrong, right, make_icon("gacha", jitter=6, seed=42))
    assert store.get(wrong).sample_count <= before_wrong
    assert store.get(right).sample_count > 0


# ---------------------------------------------------------------- 화면 적용


def _state_with_icons() -> ScreenState:
    return ScreenState(
        id="st_000",
        auto=AutoFeatures(
            elements=[
                UIElement(NormRect(0.80, 0.04, 0.035, 0.045), ElementKind.ICON),
                UIElement(NormRect(0.10, 0.90, 0.20, 0.05), ElementKind.TEXT, text="일일 임무"),
                UIElement(NormRect(0.05, 0.05, 0.90, 0.85), ElementKind.PANEL),
            ]
        ),
    )


def _screenshot_with(kind: str, rect: NormRect) -> np.ndarray:
    image = np.full((720, 1280, 3), 26, np.uint8)
    x, y, w, h = rect.to_pixels(1280, 720)
    image[y : y + h, x : x + w] = cv2.resize(make_icon(kind), (w, h))
    return image


def test_annotate_state_labels_matched_icons(trained):
    _, matcher, _ = trained
    state = _state_with_icons()
    rect = state.elements_sorted()[0].rect
    matches = matcher.annotate_state(state, _screenshot_with("gacha", rect))
    assert matches, "아이콘을 인식하지 못했습니다"
    labeled = state.elements_sorted()[0]
    assert labeled.label == "기원"
    assert labeled.source == "icon"


def test_annotate_never_overwrites_user_label(trained):
    """**핵심 불변식** — 자동 매칭이 사람의 판단을 지우면 안 된다."""
    _, matcher, _ = trained
    state = _state_with_icons()
    element = state.elements_sorted()[0]
    element.label = "담당자가 정한 이름"
    element.source = "user"

    matcher.annotate_state(state, _screenshot_with("gacha", element.rect))
    assert element.label == "담당자가 정한 이름"
    assert element.source == "user"


def test_annotate_never_claims_one_icon_twice(trained):
    """**회귀 방지** — 한 아이콘이 같은 화면의 요소 여러 개를 자기 것이라 주장하면 안 된다.

    실측에서 아이콘 2개가 홈 화면의 요소 12개에 중복 매칭된 적이 있다.
    상호 최선 매칭으로 구조적으로 막았고, 이 테스트가 그 회귀를 잡는다.
    """
    _, matcher, _ = trained
    # 거의 같은 요소를 여럿 배치한다 — 순진한 구현이면 전부 같은 아이콘으로 잡힌다
    state = ScreenState(
        id="st_000",
        auto=AutoFeatures(
            elements=[
                UIElement(NormRect(0.10 + i * 0.08, 0.05, 0.035, 0.045), ElementKind.ICON)
                for i in range(6)
            ]
        ),
    )
    image = np.full((720, 1280, 3), 26, np.uint8)
    for element in state.elements_sorted():
        x, y, w, h = element.rect.to_pixels(1280, 720)
        image[y : y + h, x : x + w] = cv2.resize(make_icon("gacha"), (w, h))

    matches = matcher.annotate_state(state, image)
    names = [m.entry.name for _, m in matches]
    assert len(names) == len(set(names)), f"같은 아이콘이 중복 매칭됨: {names}"
    assert len(matches) <= 1


def test_auto_label_threshold_is_stricter_than_lookup(trained):
    """자동 라벨링은 GUI 후보 제시보다 보수적이어야 한다."""
    from qatc.icons.matcher import AUTO_LABEL_MIN, KNN_MIN_SIM

    assert AUTO_LABEL_MIN > KNN_MIN_SIM


def test_annotate_skips_text_elements(trained):
    """텍스트가 읽힌 요소는 아이콘 사전의 대상이 아니다."""
    _, matcher, _ = trained
    state = _state_with_icons()
    text_el = next(e for e in state.elements_sorted() if e.text)
    matcher.annotate_state(state, _screenshot_with("gacha", text_el.rect))
    assert text_el.label == ""


def test_unmatched_reports_registration_candidates():
    """미등록 아이콘이 보이지 않으면 사용자가 무엇을 채워야 하는지 알 수 없다."""
    state = _state_with_icons()
    pending = unmatched_icon_elements(state, matched_indices=set())
    rects = [e.rect for _, e in pending]
    assert any(r.area < 0.01 for r in rects)              # 작은 아이콘은 후보
    assert all(r.area <= 0.05 for r in rects)             # 큰 패널은 제외
    assert not any(e.text for _, e in pending)            # 텍스트 있는 것도 제외


# ---------------------------------------------------------------- 자동 제안


def _graph_with_path() -> FlowGraph:
    graph = FlowGraph(session_id="s")
    for sid, name in (("st_000", "홈"), ("st_001", "캐릭터")):
        state = ScreenState(id=sid)
        state.user.name = name
        graph.states[sid] = state
    forward = Transition(id="tr_1", from_state="st_000", to_state="st_001", action_desc="클릭")
    graph.transitions.append(forward)
    graph.step_order.append(forward.id)
    return graph


def test_suggest_navigate_from_transition():
    graph = _graph_with_path()
    suggestion = suggest_from_transition(graph, graph.transitions[0])
    assert suggestion.action.kind is ActionKind.NAVIGATE
    assert suggestion.action.target_screen_name == "캐릭터"
    assert "캐릭터" in suggestion.action.expected
    assert suggestion.name_hint == ""     # 이름은 추론하지 않는다


def test_suggest_detects_return_by_time_order():
    """A→B가 먼저 있고 나중에 B→A가 오면 후자가 되돌아가기다."""
    graph = _graph_with_path()
    back = Transition(id="tr_2", from_state="st_001", to_state="st_000", action_desc="ESC")
    graph.transitions.append(back)
    graph.step_order.append(back.id)
    assert suggest_from_transition(graph, back).action.kind is ActionKind.BACK
    assert suggest_from_transition(graph, graph.transitions[0]).action.kind is ActionKind.NAVIGATE


def test_suggest_self_loop_stays_undecided():
    """화면이 안 바뀐 클릭은 단정하면 안 된다 — 사실과 다른 TC의 원인이 된다."""
    graph = _graph_with_path()
    loop = Transition(id="tr_3", from_state="st_000", to_state="st_000", action_desc="클릭")
    graph.transitions.append(loop)
    graph.step_order.append(loop.id)
    suggestion = suggest_from_transition(graph, loop)
    assert suggestion.action.kind is ActionKind.UNKNOWN
    assert "확인이 필요" in suggestion.rationale


def test_suggest_uses_element_text_as_name_hint():
    graph = _graph_with_path()
    element = UIElement(NormRect(0.2, 0.1, 0.1, 0.05), text="강화하기")
    suggestion = suggest_from_transition(graph, graph.transitions[0], None, element)
    assert suggestion.name_hint == "강화하기"


def test_suggest_for_element_without_click_history():
    graph = _graph_with_path()
    state = graph.states["st_000"]
    suggestion = suggest_for_element(graph, state, UIElement(NormRect(0.5, 0.5, 0.05, 0.05)))
    assert suggestion.action.kind is ActionKind.UNKNOWN
    assert "기록이 없어" in suggestion.rationale


# ---------------------------------------------------------------- 동작 모델


@pytest.mark.parametrize(
    "kind,reversible,priority",
    [
        (ActionKind.PURCHASE, False, "High"),
        (ActionKind.CONFIRM, False, "High"),
        (ActionKind.NAVIGATE, True, "Medium"),
        (ActionKind.SELECT, True, "Low"),
    ],
)
def test_action_kind_defaults(kind, reversible, priority):
    assert kind.default_reversible is reversible
    assert kind.default_priority == priority


def test_action_roundtrip():
    action = IconAction(
        kind=ActionKind.PURCHASE, target_screen_name="상점",
        expected="구매가 완료된다", consumes="크레딧", reversible=False,
    )
    back = IconAction.from_dict(action.to_dict())
    assert back.kind is ActionKind.PURCHASE
    assert back.consumes == "크레딧"


def test_unknown_action_kind_falls_back():
    assert IconAction.from_dict({"kind": "존재하지않는유형"}).kind is ActionKind.UNKNOWN


def test_tc_context_carries_user_knowledge():
    entry = IconEntry(
        id="ic_1", name="워프",
        action=IconAction(
            kind=ActionKind.PURCHASE, expected="워프 연출 후 결과가 표시된다",
            consumes="성간 항행권", reversible=False,
        ),
        notes="10연차는 4성 이상 확정",
    )
    context = entry.tc_context()
    assert "워프" in context and "성간 항행권" in context
    assert "10연차" in context
    assert "재화 부족" in context      # PURCHASE 유형의 검증 포인트


def test_typical_rect_averages_observations():
    entry = IconEntry(id="ic_1", name="x")
    entry.samples = [
        IconSample(descriptor=[], dhash="", rect=[0.1, 0.2, 0.05, 0.05]),
        IconSample(descriptor=[], dhash="", rect=[0.3, 0.4, 0.05, 0.05]),
    ]
    rect = entry.typical_rect
    assert rect is not None
    assert rect.x == pytest.approx(0.2)
    assert rect.y == pytest.approx(0.3)

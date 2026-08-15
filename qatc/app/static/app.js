"use strict";
/*
 * QATC 3분할 화면 — 트리 · 채팅 · 검토.
 *
 * 이 파일은 오직 fetch/SSE 로만 백엔드와 말한다. 지식 DB 는 어디서도 직접
 * 건드리지 않는다 — 슬롯·TC 를 바꾸는 유일한 경로는 채팅이 흘려보내는
 * `claude` 자식 프로세스(그리고 그 프로세스가 스스로 부르는 CLI) 뿐이고,
 * 이 화면은 그 결과를 다시 읽어서 보여줄 뿐이다.
 *
 * 갱신 규칙: 채팅 턴이 `done` 으로 끝날 때만 `/api/tree`·`/api/content` 를
 * 다시 가져온다. 폴링도 파일 감시도 없다 — 쓰기가 채팅으로만 일어나므로
 * 턴 종료가 "바뀌었을 수 있는 유일한 시점"이다. `db_mtime` 이 이전과 같으면
 * 렌더를 건너뛴다.
 *
 * `error` 프레임은 `done` 이 아니다. 인증 만료(`data.kind === "auth"`)는
 * 성공한 것처럼 보이는 유일한 실패 모양이므로, 여기서 절대 재조회를
 * 트리거하지 않는다.
 *
 * `notice` 프레임은 오류도 `done` 도 아니다 — 앱이 사용자에게 하는 말이고,
 * 그 턴은 계속 진행된다. 그래서 이 프레임만으로는 입력을 풀지도, 패널을
 * 다시 읽지도 않는다.
 *
 * `progress` 프레임도 마찬가지로 아무 것도 확정하지 않는다 — 지금 무엇을
 * 하는 중인지만 말한다. 이 화면은 그 위에 **자체 타이머**를 한 겹 더 얹는다:
 * 백엔드가 한마디도 못 하는 구간에서도 경과 초는 계속 올라가야 하기 때문이다.
 * 그 침묵 구간이 이 기능이 생긴 이유다 ("진행하고 있는지 멈춘건지 구별이
 * 불가능").
 */

const state = {
  tree: null,
  selectedGame: null,
  selectedContent: null,
  contentDetail: null,
  selectedTcId: null,
};

let lastMtime = null;
let turnInProgress = false;

// --- 작은 DOM 헬퍼 -----------------------------------------------------

function el(tag, opts, children) {
  opts = opts || {};
  children = children || [];
  const node = document.createElement(tag);
  if (opts.class) node.className = opts.class;
  if (opts.text !== undefined) node.textContent = opts.text;
  if (opts.title) node.title = opts.title;
  if (opts.onclick) node.addEventListener("click", opts.onclick);
  for (const c of children) node.appendChild(c);
  return node;
}

function makeOrderedList(items) {
  const ol = document.createElement("ol");
  for (const item of items) {
    ol.appendChild(el("li", { text: item }));
  }
  return ol;
}

async function fetchJSON(url) {
  const res = await fetch(url);
  let body = null;
  try {
    body = await res.json();
  } catch (err) {
    body = null;
  }
  if (!res.ok) {
    const message = body && body.error ? body.error : `요청이 실패했습니다 (${res.status}).`;
    throw new Error(message);
  }
  return body;
}

// --- 트리 (왼쪽) --------------------------------------------------------

function sameMtimes(a, b) {
  const ak = Object.keys(a);
  const bk = Object.keys(b);
  if (ak.length !== bk.length) return false;
  for (const k of ak) {
    if (a[k] !== b[k]) return false;
  }
  return true;
}

async function loadTree() {
  let data;
  try {
    data = await fetchJSON("/api/tree");
  } catch (err) {
    renderTreeError(err.message);
    return false;
  }
  const changed = lastMtime === null || !sameMtimes(data.db_mtime, lastMtime);
  lastMtime = data.db_mtime;
  if (!changed) return false;
  state.tree = data;
  renderTree();
  return true;
}

function renderTreeError(message) {
  const body = document.getElementById("tree-body");
  body.innerHTML = "";
  body.appendChild(el("p", { class: "empty", text: message }));
}

function renderTree() {
  const body = document.getElementById("tree-body");
  body.innerHTML = "";
  const data = state.tree;
  if (!data || !data.games || data.games.length === 0) {
    body.appendChild(el("p", {
      class: "empty",
      text: "지식 DB가 없습니다. 채팅에서 첫 컨텐츠를 시작하세요.",
    }));
    return;
  }
  for (const game of data.games) {
    const gameEl = el("div", { class: "game" });
    gameEl.appendChild(el("div", { class: "game-name", text: game.game }));
    if (game.contents.length === 0) {
      gameEl.appendChild(el("p", { class: "empty muted", text: "컨텐츠가 없습니다." }));
    }
    for (const content of game.contents) {
      gameEl.appendChild(renderContentRow(game.game, content));
    }
    body.appendChild(gameEl);
  }
}

// `8 / 10` 은 10이 고정 상한처럼 읽힌다. 실제로는 유형별 슬롯과 `slot add` 로
// 늘어난다 — 첫 실사용에서 사용자가 슬롯을 늘릴 수 있다는 것을 몰랐던 이유의
// 하나가 이 표시였다. 기본 세트보다 많으면 그 사실을 함께 적어, 이 숫자가 늘
// 수 있는 값임을 알린다. **진척 자체는 여전히 filled/total 이다** — 여기서
// 하는 일은 그 이상이 아니다.
function addedSlotCount(content) {
  // 기준선은 백엔드가 실어 보낸다(`base_total`). 화면이 기본 슬롯 개수를
  // 따로 알고 있으면 세트가 바뀔 때 조용히 어긋난다.
  if (typeof content.base_total !== "number") return 0;
  return Math.max(0, content.total - content.base_total);
}

function renderContentRow(game, content) {
  const isSelected = state.selectedGame === game && state.selectedContent === content.name;
  const contentEl = el("div", {
    class: "content" + (isSelected ? " selected" : ""),
    onclick: () => selectContent(game, content.name),
  });
  const head = el("div", { class: "content-head" });
  head.appendChild(el("span", { class: "content-name", text: content.name }));
  head.appendChild(el("span", {
    class: "content-progress",
    text: `${content.filled} / ${content.total}`,
  }));
  const added = addedSlotCount(content);
  if (added > 0) {
    head.appendChild(el("span", {
      class: "content-added",
      text: `+${added} 추가됨`,
      title: `기본 항목 ${content.base_total}개에 ${added}개가 더해졌습니다.`
        + " 이 분모는 고정된 한도가 아닙니다 — 필요하면 더 늘릴 수 있습니다.",
    }));
  }
  contentEl.appendChild(head);
  if (isSelected) {
    contentEl.appendChild(renderFamilies(content));
  }
  return contentEl;
}

function renderFamilies(content) {
  const wrap = el("div", { class: "families" });
  const detail = state.contentDetail;
  for (const fam of content.families) {
    wrap.appendChild(renderFamilyRow(fam, detail));
  }
  return wrap;
}

function renderFamilyRow(fam, detail) {
  let statusClass = "family-planned";
  let statusLabel = "";
  if (fam.withdrawn) {
    statusClass = "family-withdrawn";
    statusLabel = "⚠ 근거 철회됨";
  } else if (!fam.planned) {
    statusClass = "family-blocked";
    statusLabel = `막힘 — ${fam.reason || ""}`;
  }

  const famEl = el("div", { class: "family " + statusClass });
  const head = el("div", { class: "family-head" });
  head.appendChild(el("span", { class: "family-name", text: fam.family }));
  head.appendChild(el("span", { class: "family-count", text: String(fam.tc_count) }));
  famEl.appendChild(head);
  if (statusLabel) {
    famEl.appendChild(el("div", { class: "family-status", text: statusLabel }));
  }

  if (detail && fam.tc_count > 0) {
    for (const tc of detail.testcases.filter((t) => t.family === fam.family)) {
      famEl.appendChild(renderTcRow(tc));
    }
  }
  return famEl;
}

function renderTcRow(tc) {
  const selected = state.selectedTcId === tc.id;
  let cls = "tc" + (selected ? " selected" : "");
  if (tc.withdrawn) cls += " tc-withdrawn";
  const tcEl = el("div", {
    class: cls,
    text: tc.title,
    title: tc.title,
    onclick: (e) => {
      e.stopPropagation(); // 컨텐츠 재선택으로 번지지 않게
      selectTc(tc.id);
    },
  });
  return tcEl;
}

// --- 선택 상태 전이 ------------------------------------------------------

async function selectContent(game, name) {
  if (state.selectedGame === game && state.selectedContent === name) return;
  state.selectedGame = game;
  state.selectedContent = name;
  state.selectedTcId = null;
  state.contentDetail = null;
  renderTree();
  updateChatHeader();
  renderReview();
  await loadContentDetail();
}

async function loadContentDetail() {
  if (!state.selectedContent) return;
  try {
    const data = await fetchJSON(
      `/api/content?game=${encodeURIComponent(state.selectedGame)}&name=${encodeURIComponent(state.selectedContent)}`
    );
    state.contentDetail = data;
  } catch (err) {
    state.contentDetail = null;
    renderTree();
    renderReviewMessage(err.message);
    return;
  }
  renderTree();
  renderReview();
}

function selectTc(id) {
  state.selectedTcId = id;
  renderTree();
  renderReview();
}

// `선택 해제` 버튼의 핸들러. 이름 그대로 컨텐츠 선택과 채팅 로그만 지운다 —
// 서버에는 아무것도 보내지 않는다. **세션을 리셋하지 않는다**: 다음 메시지는
// `content: null` 로 가서 `__default__` 세션 키를 그대로 재개한다(새 세션을
// 만드는 라우트를 추가하지 않기로 한 결정 — 검증 없는 새 POST 엔드포인트가
// 정확히 `/api/chat` 결함이 생긴 자리였다). 예전 이름(`새 대화`)과 예전
// 동작(로그를 안 지움)은 둘 다 이 함수가 실제로 하는 일과 어긋났다.
function clearSelection() {
  state.selectedGame = null;
  state.selectedContent = null;
  state.selectedTcId = null;
  state.contentDetail = null;
  document.getElementById("chat-log").innerHTML = "";
  renderTree();
  updateChatHeader();
  renderReview();
}

function updateChatHeader() {
  const label = document.getElementById("chat-content-label");
  label.textContent = state.selectedContent
    ? `${state.selectedGame} · ${state.selectedContent}`
    : "컨텐츠를 선택하지 않음";
}

// --- 검토 (오른쪽) -------------------------------------------------------

function updateExportButton() {
  document.getElementById("export-btn").disabled = !state.selectedContent;
}

function renderReviewMessage(message) {
  document.getElementById("review-body").hidden = true;
  const emptyEl = document.getElementById("review-empty");
  emptyEl.hidden = false;
  emptyEl.textContent = message;
  updateExportButton();
}

function renderReview() {
  const emptyEl = document.getElementById("review-empty");
  const bodyEl = document.getElementById("review-body");
  const detail = state.contentDetail;
  const tc = detail ? detail.testcases.find((t) => t.id === state.selectedTcId) : null;

  if (!tc) {
    bodyEl.hidden = true;
    bodyEl.innerHTML = "";
    emptyEl.hidden = false;
    emptyEl.textContent = state.selectedContent
      ? "왼쪽에서 TC를 선택하세요."
      : "컨텐츠를 먼저 선택하세요.";
    updateExportButton();
    return;
  }

  emptyEl.hidden = true;
  bodyEl.hidden = false;
  bodyEl.innerHTML = "";

  bodyEl.appendChild(el("h2", { class: "tc-title", text: tc.title }));

  const meta = el("div", { class: "tc-meta" });
  meta.appendChild(el("span", { class: "tag", text: tc.kind }));
  meta.appendChild(el("span", { class: "tag", text: tc.priority }));
  meta.appendChild(el("span", { class: "tag", text: tc.family }));
  bodyEl.appendChild(meta);

  const badges = el("div", { class: "badges" });
  if (tc.withdrawn) {
    badges.appendChild(el("span", { class: "badge badge-withdrawn", text: "⚠ 근거 철회됨" }));
  }
  if (tc.edited) {
    badges.appendChild(el("span", { class: "badge badge-edited", text: "사람이 고침" }));
  }
  if (badges.children.length) bodyEl.appendChild(badges);

  if (tc.precondition) {
    bodyEl.appendChild(el("h3", { text: "사전 조건" }));
    bodyEl.appendChild(el("p", { class: "precondition", text: tc.precondition }));
  }

  bodyEl.appendChild(el("h3", { text: "절차" }));
  bodyEl.appendChild(makeOrderedList(tc.steps));

  bodyEl.appendChild(el("h3", { text: "기대결과" }));
  bodyEl.appendChild(makeOrderedList(tc.expected));

  bodyEl.appendChild(el("h3", { text: "근거 슬롯" }));
  const slotMap = new Map((detail.slots || []).map((s) => [s.key, s]));
  const slotsWrap = el("div", { class: "slots" });
  for (const key of tc.slot_keys) {
    const slot = slotMap.get(key);
    const row = el("div", { class: "slot-row" });
    row.appendChild(el("span", { class: "slot-key", text: key }));
    row.appendChild(el("span", {
      class: "slot-value",
      text: slot ? slot.value || "(값 없음)" : "(슬롯을 찾을 수 없음)",
    }));
    slotsWrap.appendChild(row);
  }
  bodyEl.appendChild(slotsWrap);
  if (tc.rationale) {
    bodyEl.appendChild(el("p", { class: "rationale", text: tc.rationale }));
  }

  updateExportButton();
}

async function exportContent() {
  if (!state.selectedContent) return;
  const btn = document.getElementById("export-btn");
  const status = document.getElementById("export-status");
  btn.disabled = true;
  status.className = "";
  status.textContent = "내보내는 중…";
  try {
    const resp = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ game: state.selectedGame, content: state.selectedContent }),
    });
    let body = null;
    try {
      body = await resp.json();
    } catch (err) {
      body = null;
    }
    if (resp.ok) {
      status.className = "ok";
      status.textContent = body && body.path ? `엑셀을 열었습니다: ${body.path}` : "엑셀을 열었습니다.";
    } else {
      // 409(파일 잠김)는 흔한 경우다 — 서버가 보낸 한국어 문장을 그대로 보여준다.
      status.className = "error";
      status.textContent = body && body.error ? body.error : "엑셀을 여는 중 오류가 발생했습니다.";
    }
  } catch (err) {
    status.className = "error";
    status.textContent = "서버와 통신하지 못했습니다.";
  } finally {
    btn.disabled = !state.selectedContent;
  }
}

// --- claude 상태 배지 ----------------------------------------------------

async function loadHealth() {
  try {
    const body = await fetchJSON("/api/health");
    renderHealth(body.claude);
  } catch (err) {
    renderHealth("missing");
  }
}

function renderHealth(status) {
  const labels = { ok: "연결됨", missing: "claude 없음", unauthenticated: "재인증 필요" };
  const badgeEl = document.getElementById("claude-status");
  badgeEl.textContent = labels[status] || status;
  badgeEl.className = "badge status-" + status;
}

// --- 채팅 (가운데) -------------------------------------------------------

function scrollChatToBottom() {
  const log = document.getElementById("chat-log");
  log.scrollTop = log.scrollHeight;
}

// --- 진행 표시 ----------------------------------------------------------
//
// 두 겹이다. **경과 초는 거짓말을 할 수 없다** — 백엔드가 한마디도 못 해도
// 이 숫자는 1초마다 올라가므로 화면이 멈춘 것처럼 보이지 않는다. `progress`
// 프레임은 *무엇을 하는 중인지* 를 말해 준다. 하나만으론 부족하다: 타이머만
// 있으면 뭘 하는지 모르고, 이벤트만 있으면 침묵 구간에서 화면이 멈춘다.
//
// 수명은 턴과 정확히 같다 — `setTurnInProgress` 한 곳에서만 켜고 끈다.
// 그래야 어떤 실패 경로(서버 거절·통신 끊김·프레임 없는 종료)로 빠져도
// 표시가 혼자 남아 영원히 도는 일이 없다.
const progress = { node: null, phaseEl: null, elapsedEl: null, timer: null, startedAt: 0 };

function startProgress() {
  stopProgress();
  progress.startedAt = Date.now();
  progress.phaseEl = el("span", { class: "progress-phase", text: "claude 를 깨우는 중" });
  progress.elapsedEl = el("span", { class: "progress-elapsed", text: "0초" });
  const dots = el("span", { class: "progress-dots" }, [el("i", {}), el("i", {}), el("i", {})]);
  progress.node = el("div", { class: "progress" }, [
    dots, progress.phaseEl, progress.elapsedEl,
  ]);
  document.getElementById("chat-log").appendChild(progress.node);
  progress.timer = setInterval(tickProgress, 1000);
  scrollChatToBottom();
}

function tickProgress() {
  if (!progress.elapsedEl) return;
  progress.elapsedEl.textContent = formatElapsed(
    Math.floor((Date.now() - progress.startedAt) / 1000)
  );
}

function formatElapsed(seconds) {
  if (seconds < 60) return `${seconds}초`;
  return `${Math.floor(seconds / 60)}분 ${seconds % 60}초`;
}

function setProgressPhase(phase) {
  if (!progress.phaseEl || !phase) return;
  progress.phaseEl.textContent = phase;
}

// 말풍선이나 알림이 로그에 붙으면 진행 표시가 그 **위**에 남는다. 항상 맨
// 아래로 다시 옮겨, "지금 기다리는 중" 이 늘 마지막 줄에 오게 한다.
function keepProgressLast() {
  const log = document.getElementById("chat-log");
  if (progress.node && progress.node.parentNode === log) log.appendChild(progress.node);
}

function stopProgress() {
  if (progress.timer !== null) clearInterval(progress.timer);
  if (progress.node && progress.node.parentNode) {
    progress.node.parentNode.removeChild(progress.node);
  }
  progress.node = null;
  progress.phaseEl = null;
  progress.elapsedEl = null;
  progress.timer = null;
}

// --- 첨부 이미지 --------------------------------------------------------
//
// 첫 실사용에서 사용자는 스크린샷 3장을 따로 저장해 두고, 그것을 보면서 화면
// 인벤토리를 손으로 옮겨 적었다 — `screen` 슬롯 하나에 전부. 붙일 수 있으면
// 그 옮겨적기가 없어진다.
//
// **개인정보.** 화면에 있는 것이 그대로 넘어간다 — 그때의 스크린샷에는 실제
// 이메일 주소가 찍혀 있었다. 손으로 옮길 때는 무엇을 넘길지 골랐지만 첨부는
// 고르지 않는다. 그래서 보내기 **전에** 항상 보여주고, 한 장씩 뺄 수 있게
// 한다. 여기서 못 빼면 사용자가 고를 기회가 아예 없다.
//
// 여기 상한은 서버 상한의 사본이 아니라 **먼저 걸리는 안내**다. 판정은 서버가
// 한다(`decode_shots`) — 그쪽이 실제로 디스크에 쓰는 자리이기 때문이다.
const MAX_ATTACHMENTS = 4;
const MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024;

let attachments = [];

function readImageFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("읽지 못했습니다"));
    reader.readAsDataURL(file);
  });
}

async function addImageFiles(fileList) {
  const files = Array.from(fileList || []).filter((f) => f && f.type.startsWith("image/"));
  for (const file of files) {
    if (attachments.length >= MAX_ATTACHMENTS) {
      appendErrorMessage(`이미지는 한 번에 ${MAX_ATTACHMENTS}장까지 붙일 수 있습니다.`);
      break;
    }
    if (file.size > MAX_ATTACHMENT_BYTES) {
      appendErrorMessage("이미지 한 장은 8MB까지입니다. 더 작게 잘라서 붙여넣어 주세요.");
      continue;
    }
    let dataUrl;
    try {
      dataUrl = await readImageFile(file);
    } catch (err) {
      appendErrorMessage("이미지를 읽지 못했습니다. 다시 붙여넣어 주세요.");
      continue;
    }
    attachments.push({
      dataUrl,
      base64: dataUrl.slice(dataUrl.indexOf(",") + 1),
      mediaType: file.type || "image/png",
    });
  }
  renderAttachments();
}

function thumbnail(dataUrl) {
  const img = document.createElement("img");
  img.src = dataUrl;
  img.alt = "붙인 이미지";
  return img;
}

function renderAttachments() {
  const strip = document.getElementById("chat-attachments");
  strip.innerHTML = "";
  strip.hidden = attachments.length === 0;
  attachments.forEach((shot, index) => {
    const remove = el("button", {
      class: "attachment-remove",
      text: "×",
      title: "이 이미지를 뺍니다",
      onclick: () => {
        attachments.splice(index, 1);
        renderAttachments();
      },
    });
    remove.type = "button";
    strip.appendChild(el("div", { class: "attachment" }, [thumbnail(shot.dataUrl), remove]));
  });
}

function bindAttachments() {
  document.getElementById("chat-input").addEventListener("paste", (e) => {
    const files = e.clipboardData && e.clipboardData.files;
    if (files && files.length) {
      e.preventDefault();       // 안 막으면 파일 이름이 텍스트로 딸려 들어간다
      addImageFiles(files);
    }
  });
  const pane = document.getElementById("chat");
  pane.addEventListener("dragover", (e) => {
    e.preventDefault();
    pane.classList.add("dropping");
  });
  pane.addEventListener("dragleave", () => pane.classList.remove("dropping"));
  pane.addEventListener("drop", (e) => {
    e.preventDefault();
    pane.classList.remove("dropping");
    if (e.dataTransfer) addImageFiles(e.dataTransfer.files);
  });
  document.getElementById("capture-btn").addEventListener("click", captureShot);
}

// 게임 창을 찍어 붙인다. **캡처 전용 업로드 경로를 만들지 않는다** - 받은
// 이미지를 붙여넣기와 똑같이 `addImageFiles` 에 넣어, 4장/8MB 검증과 썸네일과
// `x` 제거를 그대로 쓴다. 두 경로가 생기면 한쪽에만 검증이 붙는다.
//
// 찍은 것도 곧바로 전송되지 않는다 - 첨부 스트립을 거치므로 사용자가 보고 뺄
// 수 있다. 화면에 있는 것이 그대로 넘어가는 기능이라 고를 기회가 있어야 한다.
async function captureShot() {
  const game = state.selectedGame;
  if (!game) {
    appendErrorMessage("먼저 왼쪽 트리에서 컨텐츠를 고르세요 - 어느 게임의 창을 찍을지 알아야 합니다.");
    return;
  }
  const btn = document.getElementById("capture-btn");
  btn.disabled = true;
  try {
    const resp = await fetch("/api/capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ game }),
    });
    let body = null;
    try {
      body = await resp.json();
    } catch (err) {
      body = null;
    }
    if (!resp.ok) {
      appendErrorMessage(
        body && body.error ? body.error : `촬영에 실패했습니다 (${resp.status}).`);
      return;
    }
    const raw = atob(body.data);
    const buf = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) buf[i] = raw.charCodeAt(i);
    await addImageFiles([new File([buf], "capture.png", { type: body.media_type })]);
  } catch (err) {
    appendErrorMessage("서버와 통신하지 못했습니다. 다시 시도하세요.");
  } finally {
    btn.disabled = false;
  }
}

// 말풍선에 썸네일을 남긴다 — 나중에 "이 슬롯 근거가 뭐였지" 를 되짚을 때,
// 그때 무엇을 보여줬는지가 대화 안에 남아 있어야 한다.
function appendUserMessage(text, shots) {
  const log = document.getElementById("chat-log");
  const bubble = el("div", { class: "msg msg-user" }, [el("p", { text })]);
  if (shots && shots.length) {
    const strip = el("div", { class: "msg-shots" });
    for (const shot of shots) strip.appendChild(thumbnail(shot.dataUrl));
    bubble.appendChild(strip);
  }
  log.appendChild(bubble);
  scrollChatToBottom();
}

function beginAssistantTurn() {
  // 말풍선은 만들어만 두고, 실제 내용(텍스트든 도구 호출이든)이 처음
  // 도착할 때 붙인다 — 그래야 델타 하나 없이 바로 error 로 끝나는 턴에서
  // 빈 말풍선이 화면에 남지 않는다.
  const bubble = el("div", { class: "msg msg-assistant" });
  return { bubble, textSpan: null, attached: false };
}

function attachAssistantBubble(turn) {
  if (turn.attached) return;
  document.getElementById("chat-log").appendChild(turn.bubble);
  turn.attached = true;
  keepProgressLast();
}

function appendAssistantDelta(turn, text) {
  attachAssistantBubble(turn);
  if (!turn.textSpan) {
    turn.textSpan = el("span", { class: "text" });
    turn.bubble.appendChild(turn.textSpan);
  }
  turn.textSpan.textContent += text;
  scrollChatToBottom();
}

function appendToolCall(turn, name, summary) {
  attachAssistantBubble(turn);
  const line = el("div", { class: "tool-call" });
  line.appendChild(el("span", { class: "tool-label", text: "도구 호출" }));
  line.appendChild(el("span", { class: "tool-name", text: name }));
  line.appendChild(el("span", { class: "tool-summary", text: summary }));
  turn.bubble.appendChild(line);
  turn.textSpan = null; // 다음 delta 는 이 줄 다음의 새 span 에서 이어간다
  scrollChatToBottom();
}

function appendErrorMessage(message) {
  const log = document.getElementById("chat-log");
  const bubble = el("div", { class: "msg msg-error" }, [el("p", { text: message })]);
  log.appendChild(bubble);
  scrollChatToBottom();
}

// 앱이 사용자에게 하는 말. 오류가 아니다 — 이 줄이 나와도 그 턴은 계속
// 진행되어 `done` 으로 끝나고 패널도 갱신된다. 그래서 말풍선(모델이 한 말)
// 과도, 빨간 오류 줄과도 다르게 그린다: 옅은 가운데 한 줄.
function appendNoticeMessage(message) {
  const log = document.getElementById("chat-log");
  log.appendChild(el("div", { class: "msg msg-notice" }, [el("p", { text: message })]));
  keepProgressLast();
  scrollChatToBottom();
}

function setTurnInProgress(value) {
  turnInProgress = value;
  document.getElementById("chat-send").disabled = value;
  document.getElementById("chat-input").disabled = value;
  if (value) startProgress();
  else stopProgress();
}

function handleChatFrame(frame, turn) {
  let kind = "";
  let dataLine = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event: ")) kind = line.slice("event: ".length);
    else if (line.startsWith("data: ")) dataLine = line.slice("data: ".length);
  }
  let data = {};
  if (dataLine) {
    try {
      data = JSON.parse(dataLine);
    } catch (err) {
      data = {};
    }
  }

  if (kind === "progress") {
    // 아무 것도 확정하지 않는 표시다 — 여기서 입력을 풀지도, 패널을 다시
    // 읽지도 않는다. 그 판단은 `done`/`error` 만 한다.
    setProgressPhase(data.phase || "");
  } else if (kind === "delta") {
    appendAssistantDelta(turn, data.text || "");
    // 답이 흐르기 시작했다 — 이 순간부터 "기다리는 중" 은 거짓말이다.
    // 표시를 지우지는 않는다: 도구 호출 뒤에 다시 긴 침묵이 오는 것이 실제
    // 턴의 모양이라, 지워 버리면 그 구간이 처음의 문제로 되돌아간다.
    setProgressPhase("답하는 중");
  } else if (kind === "tool") {
    appendToolCall(turn, data.name || "", data.summary || "");
  } else if (kind === "notice") {
    // 알림은 턴을 끝내지 않는다 — `setTurnInProgress` 도 재조회도 여기서
    // 하지 않는다. 뒤이어 오는 `done`/`error` 가 그 판단을 한다.
    appendNoticeMessage(data.message || "");
  } else if (kind === "done") {
    // 턴이 정상적으로 끝났다 — 이때만 다시 읽는다.
    setTurnInProgress(false);
    refreshAfterTurn();
  } else if (kind === "error") {
    // error 는 done 이 아니다. 특히 인증 만료(kind === "auth")는 "성공한 것처럼
    // 보이는" 유일한 실패 모양이므로, 여기서는 절대 트리·검토를 다시
    // 불러오지 않는다 — 턴이 실패했다는 사실을 화면 갱신으로 덮지 않는다.
    appendErrorMessage(data.message || "오류가 발생했습니다.");
    if (data.kind === "auth") renderHealth("unauthenticated");
    setTurnInProgress(false);
  }
}

async function readSseStream(reader, turn) {
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    // 프레임 경계는 정확히 빈 줄(연속 개행 두 번)이다.
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      if (frame) handleChatFrame(frame, turn);
    }
  }
  // done/error 프레임 없이 스트림이 끝났을 수도 있다 — 그래도 입력은 풀어준다.
  if (turnInProgress) setTurnInProgress(false);
}

async function submitMessage() {
  if (turnInProgress) return;
  const input = document.getElementById("chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  // 이 턴에 실제로 나가는 것만 떼어 낸다. 보내는 도중에 사용자가 새로
  // 붙여넣어도 그것은 다음 턴의 것이다.
  const shots = attachments;
  attachments = [];
  renderAttachments();
  appendUserMessage(text, shots);
  setTurnInProgress(true);
  const turn = beginAssistantTurn();

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        content: state.selectedContent,
        images: shots.map((s) => ({ data: s.base64, media_type: s.mediaType })),
      }),
    });
    if (!resp.ok || !resp.body) {
      // 서버가 왜 거절했는지 한국어로 실어 보냈으면 그걸 그대로 보여준다 —
      // 상태 코드만 보여주면 사용자가 할 수 있는 일이 없다.
      let body = null;
      try {
        body = await resp.json();
      } catch (err) {
        body = null;
      }
      appendErrorMessage(
        body && body.error ? body.error : `요청이 실패했습니다 (${resp.status}).`
      );
      // 붙인 것을 되돌려 준다. 안 그러면 "4장은 너무 많다" 같은 거절 하나에
      // 사용자가 스크린샷을 전부 다시 캡처해야 한다.
      restoreAttachments(shots);
      setTurnInProgress(false);
      return;
    }
    await readSseStream(resp.body.getReader(), turn);
  } catch (err) {
    appendErrorMessage("서버와 통신하지 못했습니다. 다시 시도하세요.");
    restoreAttachments(shots);
    setTurnInProgress(false);
  }
}

// 턴이 뜨지도 못하고 거절당했을 때만 쓴다. 그 사이 사용자가 새로 붙인 것이
// 있으면 그것을 앞에 두고, 상한을 넘는 만큼은 버린다.
function restoreAttachments(shots) {
  if (!shots.length) return;
  attachments = attachments.concat(shots).slice(0, MAX_ATTACHMENTS);
  renderAttachments();
}

async function refreshAfterTurn() {
  const changed = await loadTree();
  if (changed && state.selectedContent) {
    await loadContentDetail();
  }
}

// --- 시작 ----------------------------------------------------------------

function bindChatForm() {
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    submitMessage();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submitMessage();
    }
  });
}

function init() {
  bindChatForm();
  bindAttachments();
  document.getElementById("chat-new-btn").addEventListener("click", clearSelection);
  document.getElementById("export-btn").addEventListener("click", exportContent);
  updateChatHeader();
  renderReview();
  loadHealth();
  loadTree();
}

document.addEventListener("DOMContentLoaded", init);

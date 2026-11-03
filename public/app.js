/* 이어하기 — 프론트엔드 (빌드 도구 없는 단일 파일 SPA) */
(() => {
  "use strict";

  const $app = document.getElementById("app");
  const state = {
    home: null,          // /api/home 응답
    projectId: null,
    openTasks: new Set(), // 히스토리에서 펼친 작업
    showDone: false,
  };
  let pageCleanup = null;
  const AUTOSAVE_MS = 5 * 60 * 1000;

  // ------------------------------------------------------------ utils

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* 저장소 없음 */ } }

  class ApiError extends Error {
    constructor(msg, status) { super(msg); this.status = status; }
  }

  async function api(method, path, body) {
    let res;
    try {
      res = await fetch(path, {
        method,
        credentials: "same-origin",
        headers: body !== undefined ? { "Content-Type": "application/json" } : {},
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (e) {
      throw new ApiError("서버에 연결하지 못했습니다. 네트워크를 확인하세요", 0);
    }
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 && path !== "/api/login") {
      state.home = null;
      if (location.hash !== "#/login") {
        if (data.detail && data.detail !== "로그인이 필요합니다") toast(data.detail, true);
        location.hash = "#/login";
      }
      throw new ApiError(data.detail || "로그인이 필요합니다", 401);
    }
    if (!res.ok) {
      const d = Array.isArray(data.detail) ? "입력값을 확인하세요" : data.detail;
      throw new ApiError(d || `오류가 발생했습니다 (${res.status})`, res.status);
    }
    return data;
  }

  let toastTimer;
  function toast(msg, isErr = false) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.className = "show" + (isErr ? " err" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.className = ""), isErr ? 3600 : 2200);
  }

  const pad = (n) => String(n).padStart(2, "0");
  const DOW = ["일", "월", "화", "수", "목", "금", "토"];
  function fmtTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    const now = new Date();
    const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diff = Math.round((day(now) - day(d)) / 86400000);
    const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    if (diff === 0) return `오늘 ${hm}`;
    if (diff === 1) return `어제 ${hm}`;
    if (diff > 1 && diff < 7) return `${d.getMonth() + 1}/${d.getDate()}(${DOW[d.getDay()]}) ${hm}`;
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`;
  }
  const nowHM = () => { const d = new Date(); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
  const STATUS_LABEL = { active: "진행 중", waiting: "대기", done: "완료" };
  const fileName = (p) => p.split(/[\\/]/).pop();

  function go(hash) {
    if (location.hash === hash) render();
    else location.hash = hash;
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      // http(사내망) 등 clipboard API를 못 쓰는 환경 대비
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
      ta.remove();
      return ok;
    }
  }

  // ------------------------------------------------------------ modal

  function modal({ title, body, foot }) {
    const bg = document.createElement("div");
    bg.className = "modal-bg";
    bg.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <div class="modal-head"><span>${esc(title)}</span><button class="btn ghost icon-btn" data-close aria-label="닫기">✕</button></div>
      <div class="modal-body">${body}</div>
      ${foot ? `<div class="modal-foot">${foot}</div>` : ""}
    </div>`;
    const close = () => { bg.remove(); document.removeEventListener("keydown", onKey, true); };
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
    bg.addEventListener("click", (e) => { if (e.target === bg || e.target.closest("[data-close]")) close(); });
    document.addEventListener("keydown", onKey, true);
    document.body.appendChild(bg);
    return { el: bg, close };
  }

  function askText(title, label, initial = "") {
    return new Promise((resolve) => {
      const m = modal({
        title,
        body: `<form id="ask-form"><label class="label">${esc(label)}</label><input class="input" id="ask-input" maxlength="200" value="${esc(initial)}"></form>`,
        foot: `<button class="btn" data-close>취소</button><button class="btn primary" id="ask-ok">확인</button>`,
      });
      const input = $("#ask-input", m.el);
      input.focus();
      input.select();
      const done = (e) => {
        e && e.preventDefault();
        const v = input.value.trim();
        m.close();
        resolve(v || null);
      };
      $("#ask-form", m.el).addEventListener("submit", done);
      $("#ask-ok", m.el).addEventListener("click", done);
      m.el.addEventListener("click", (e) => { if (e.target === m.el || e.target.closest("[data-close]")) resolve(null); });
    });
  }

  function confirmBox(title, text, okLabel = "확인") {
    return new Promise((resolve) => {
      const m = modal({
        title,
        body: `<p style="margin:0">${text}</p>`,
        foot: `<button class="btn" data-close>취소</button><button class="btn primary" id="cf-ok">${esc(okLabel)}</button>`,
      });
      $("#cf-ok", m.el).focus();
      $("#cf-ok", m.el).addEventListener("click", () => { m.close(); resolve(true); });
      m.el.addEventListener("click", (e) => { if (e.target === m.el || e.target.closest("[data-close]")) resolve(false); });
    });
  }

  // ------------------------------------------------------------ shared pieces

  function topbar(data, current) {
    const u = data.user;
    const opts = data.projects.map((p) => `<option value="${p.id}" ${data.project && p.id === data.project.id ? "selected" : ""}>${esc(p.name)}${p.vcs_type !== "unknown" ? ` (${p.vcs_type.toUpperCase()})` : ""}</option>`).join("");
    const banner = u.is_demo
      ? `<div class="demo-banner"><div class="wrap"><span>체험 모드 · <span class="hide-sm">입력한 내용은 </span>24시간 후 자동 삭제<span class="hide-sm">됩니다</span></span><span class="spacer" style="flex:1"></span><button id="demo-reset">처음부터 다시</button></div></div>`
      : "";
    return `${banner}<header class="topbar"><div class="wrap">
      <a class="brand" href="#/"><img src="/favicon.svg" alt=""><span>이어하기</span></a>
      ${data.projects.length ? `<select class="select proj-select" id="proj-select" aria-label="프로젝트 선택">${opts}<option value="__new">+ 새 프로젝트</option></select>` : `<span class="spacer"></span>`}
      <span class="spacer"></span>
      ${current === "history"
        ? `<a class="btn ghost sm" href="#/">← 이어하기</a>`
        : `<a class="btn ghost sm" href="#/history">히스토리</a>`}
      <button class="btn ghost icon-btn" id="theme-btn" title="라이트/다크 전환" aria-label="라이트/다크 전환">◐</button>
      <button class="btn ghost sm" id="logout-btn" title="${esc(u.name)}">${u.is_demo ? "나가기" : "로그아웃"}</button>
    </div></header>`;
  }

  function bindTopbar(after) {
    const sel = $("#proj-select");
    if (sel) {
      sel.addEventListener("change", async () => {
        if (sel.value === "__new") {
          const name = await askText("새 프로젝트", "프로젝트 이름 (예: 결제시스템)");
          if (!name) { sel.value = String(state.projectId); return; }
          try {
            const p = await api("POST", "/api/projects", { name });
            state.projectId = p.id;
            toast(`'${name}' 프로젝트를 만들었습니다`);
          } catch (e) { toast(e.message, true); }
        } else {
          state.projectId = Number(sel.value);
        }
        after();
      });
    }
    $("#theme-btn").addEventListener("click", () => {
      const cur = document.documentElement.dataset.theme
        || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      const next = cur === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      lsSet("theme", next);
    });
    $("#logout-btn").addEventListener("click", async () => {
      await api("POST", "/api/logout").catch(() => {});
      state.home = null;
      state.projectId = null;
      go("#/login");
    });
    const reset = $("#demo-reset");
    if (reset) {
      reset.addEventListener("click", async () => {
        reset.disabled = true;
        try {
          await api("POST", "/api/demo/reset");
          state.projectId = null;
          state.openTasks.clear();
          toast("샘플 데이터를 처음 상태로 되돌렸습니다");
          go("#/");
        } catch (e) { toast(e.message, true); reset.disabled = false; }
      });
    }
  }

  function filesHTML(files) {
    return `<div class="files">${files.map((f) => `<span class="file" title="${esc(f.path)}"><b class="st-${esc(f.status)}">${esc(f.status)}</b>${esc(fileName(f.path))}</span>`).join("")}</div>`;
  }

  function linksHTML(links) {
    return links.map((l) => /^https?:\/\//i.test(l)
      ? `<a href="${esc(l)}" target="_blank" rel="noopener noreferrer">${esc(l)}</a>`
      : esc(l)).join("<br>");
  }

  function buildPrompt(card) {
    const files = (card.vcs_files || []).map((f) => f.path).join(", ");
    const lines = [
      ["작업 내용", card.what],
      ["멈춘 위치", card.where],
      ["다음 할 일", card.next],
      ["수정 중인 파일", files],
      ["브랜치", card.vcs_branch],
      ["참고 메모", card.memo],
    ].filter(([, v]) => v && String(v).trim()).map(([k, v]) => `- ${k}: ${String(v).trim()}`);
    return `나는 [${card.project.name}] 프로젝트에서 "${card.task.label}" 작업을 이어서 하려고 해.\n\n${lines.join("\n")}\n\n먼저 위 파일들을 확인해서 현재 상태를 파악하고, "다음 할 일"부터 진행하자.`;
  }

  // ------------------------------------------------------------ S0 로그인

  async function renderLogin() {
    try {
      await fetch("/api/me", { credentials: "same-origin" }).then((r) => { if (r.ok) throw "in"; });
    } catch (e) {
      if (e === "in") { go("#/"); return; }
    }
    $app.innerHTML = `<div class="login"><div class="wrap">
      <img class="hero-mark" src="/favicon.svg" alt="">
      <h1>어제 멈춘 곳에서,<br>1분 안에 다시 시작.</h1>
      <p class="sub">멈출 때 한 줄 남기면, 돌아올 때 이어하기 카드 한 장이 기다립니다.</p>
      <ul class="feature-list">
        <li><span class="n">1</span><span><b>이어하기 카드</b> — 무엇을 · 어디까지 · 다음엔, 수정 중 파일과 브랜치까지 한눈에</span></li>
        <li><span class="n">2</span><span><b>10초 멈춤 기록</b> — 회의·호출로 급히 떠날 때 버튼 → 한 줄 → Enter</span></li>
        <li><span class="n">3</span><span><b>AI 프롬프트 복사</b> — opencode 등에 붙여넣으면 바로 이어서 작업</span></li>
      </ul>
      <button class="btn primary lg demo-btn" id="demo-btn">샘플로 체험하기 →</button>
      <p class="demo-note">로그인 없이 · 샘플 데이터로 모든 기능 사용 · 24시간 후 자동 삭제</p>
      <div class="divider">내 계정으로 시작</div>
      <form id="login-form" autocomplete="on">
        <div id="login-error" class="form-error hidden"></div>
        <div id="create-box"></div>
        <div class="field"><label class="label" for="name">이름</label>
          <input class="input" id="name" name="username" maxlength="30" autocomplete="username" required></div>
        <div class="field"><label class="label" for="pin">PIN (숫자 4자리)</label>
          <input class="input pin-input" id="pin" name="password" type="password" inputmode="numeric" pattern="\\d{4}" maxlength="4" autocomplete="current-password" required></div>
        <button class="btn lg" style="width:100%" id="login-btn">로그인</button>
        <p class="hint" style="text-align:center">처음이면 이 이름과 PIN으로 새 계정을 만듭니다. 5회 틀리면 5분간 잠깁니다.</p>
      </form>
    </div></div>`;

    const errBox = $("#login-error");
    const showErr = (m) => { errBox.textContent = m; errBox.classList.toggle("hidden", !m); };

    $("#demo-btn").addEventListener("click", async (e) => {
      const b = e.currentTarget;
      b.disabled = true;
      b.textContent = "샘플 준비 중…";
      try {
        await api("POST", "/api/demo");
        state.projectId = null;
        state.home = null;
        go("#/");
      } catch (err) {
        showErr(err.message);
        b.disabled = false;
        b.textContent = "샘플로 체험하기 →";
      }
    });

    const submit = async (create) => {
      const name = $("#name").value.trim();
      const pin = $("#pin").value.trim();
      if (!name) { showErr("이름을 입력하세요"); return; }
      if (!/^\d{4}$/.test(pin)) { showErr("PIN은 숫자 4자리입니다"); return; }
      showErr("");
      $("#login-btn").disabled = true;
      try {
        const r = await api("POST", "/api/login", { name, pin, create });
        if (r.need_create) {
          $("#create-box").innerHTML = `<div class="confirm-box"><b>'${esc(name)}'</b> 계정이 없습니다. 이 이름과 PIN으로 새로 만들까요?
            <div class="actions"><button type="button" class="btn primary sm" id="create-yes">새 계정 만들기</button><button type="button" class="btn sm" id="create-no">이름 다시 입력</button></div></div>`;
          $("#create-yes").addEventListener("click", () => submit(true));
          $("#create-no").addEventListener("click", () => { $("#create-box").innerHTML = ""; $("#name").focus(); });
          return;
        }
        state.projectId = null;
        state.home = null;
        if (r.created) toast("계정을 만들었습니다. 첫 프로젝트를 추가하세요");
        go("#/");
      } catch (err) {
        showErr(err.message);
        $("#pin").value = "";
        $("#pin").focus();
      } finally {
        const b = $("#login-btn");
        if (b) b.disabled = false;
      }
    };
    $("#login-form").addEventListener("submit", (e) => { e.preventDefault(); submit(false); });
  }

  // ------------------------------------------------------------ S1 시작 화면

  async function loadHome() {
    const q = state.projectId ? `?project_id=${state.projectId}` : "";
    const data = await api("GET", "/api/home" + q);
    state.home = data;
    state.projectId = data.project ? data.project.id : null;
    return data;
  }

  function cardHTML(data) {
    const c = data.card;
    if (!data.project) {
      return `<section class="card empty">
        <div class="big">첫 프로젝트를 만들어 시작하세요</div>
        <p>프로젝트 → 작업 → 멈춤 기록 순서로 쌓입니다.</p>
        <form id="first-project" class="inline-form" style="border:0;background:transparent;justify-content:center">
          <input class="input" id="first-project-name" placeholder="프로젝트 이름 (예: 결제시스템)" maxlength="120" required>
          <button class="btn primary">만들기</button>
        </form></section>`;
    }
    if (!c) {
      return `<section class="card empty" id="resume-card">
        <div class="big">이어갈 작업이 없습니다. 작업을 시작하세요</div>
        <p>아래 목록에서 <b>▶ 이 작업 시작</b>을 누르거나 새 작업을 추가하세요.</p>
        <button class="btn primary" id="focus-add-task">+ 작업 추가</button>
      </section>`;
    }
    const t = c.task;
    let rows = "";
    if (!c.has_record) {
      rows = `<div class="rows"><div class="memo-preview">아직 기록이 없습니다. 작업을 멈출 때 <b>[멈춤 기록]</b>을 눌러 한 줄 남기세요.</div></div>`;
    } else {
      rows += `<div class="rows">`;
      if (c.what) rows += `<div class="row"><span class="k">무엇을</span><span class="v">${esc(c.what)}</span></div>`;
      if (c.where) rows += `<div class="row"><span class="k">어디까지</span><span class="v">${esc(c.where)}</span></div>`;
      if (c.next) {
        rows += `<div class="row next"><span class="k">다음엔</span><span class="v">${esc(c.next)}</span></div>`;
      } else {
        rows += `<div class="next-empty"><div class="title">다음엔: (기록 없음)</div>
          <form id="quick-next"><input class="input" id="quick-next-input" placeholder="지금 한 줄 적어두세요 — 예: 헤더 인코딩 고친 뒤 크롬/엣지에서 다운로드 확인" maxlength="2000"><button class="btn primary">저장</button></form></div>`;
      }
      if (c.vcs_files.length) rows += `<div class="row"><span class="k">수정 중</span><span class="v">${filesHTML(c.vcs_files)}</span></div>`;
      if (c.vcs_branch) rows += `<div class="row"><span class="k">브랜치</span><span class="v branch">${esc(c.vcs_branch)}</span></div>`;
      if (c.memo) {
        const first = c.memo.split("\n").slice(0, 2).join("\n");
        rows += `<div class="row"><span class="k">메모</span><span class="v memo-preview">${esc(first)}${c.memo.length > first.length ? " …" : ""}</span></div>`;
      }
      if (c.links.length) rows += `<div class="row"><span class="k">링크</span><span class="v">${linksHTML(c.links)}</span></div>`;
      rows += `</div>`;
    }
    const meta = [];
    if (c.has_record) {
      meta.push(`마지막 기록: <b>${fmtTime(c.last_at)}</b> · ${c.last_type === "manual" ? "수동" : "자동 저장"}`);
      if (c.draft_at && c.manual_at) meta.push(`수동 기록: ${fmtTime(c.manual_at)}${c.next_from === "manual" ? " (다음엔은 이 기록 기준)" : ""}`);
    }
    return `<article class="card" id="resume-card">
      <div class="card-head">
        <div><div class="eyebrow">[${esc(c.project.name)}] 지금 이어갈 작업</div>
        <h1 class="card-title">${esc(t.label)}</h1></div>
        ${c.draft_at ? `<span class="chip warn" title="멈춤 기록 버튼 없이 자동 저장된 초안이 있습니다">자동 초안</span>` : `<span class="chip accent">진행 중</span>`}
      </div>
      ${rows}
      ${meta.length ? `<div class="card-meta">${meta.map((m) => `<span>${m}</span>`).join("")}</div>` : ""}
      <div class="card-actions">
        <button class="btn primary lg" id="btn-stop">멈춤 기록 <kbd>S</kbd></button>
        <button class="btn lg" id="btn-prompt" ${c.has_record ? "" : "disabled"}>AI 프롬프트 보기/복사 <kbd class="hide-sm">C</kbd></button>
      </div>
    </article>`;
  }

  function tasksHTML(data) {
    if (!data.project) return "";
    const active = data.tasks.filter((t) => t.status !== "done");
    const done = data.tasks.filter((t) => t.status === "done");
    const item = (t) => `<div class="item ${t.status}">
      <div class="grow"><div class="t">${esc(t.label)}</div>
        <div class="s">${STATUS_LABEL[t.status]}${t.last_record_at ? ` · 마지막 기록 ${fmtTime(t.last_record_at)}` : " · 기록 없음"}</div></div>
      ${t.status === "active" ? `<span class="chip accent">진행 중</span>` : `<button class="btn sm" data-start="${t.id}">▶ 이 작업 시작</button>`}
    </div>`;
    return `<section class="section">
      <div class="section-head"><h2>${esc(data.project.name)}의 작업</h2><a class="btn ghost sm" href="#/history">전체 기록 보기</a></div>
      <div class="list">
        ${active.map(item).join("") || `<div class="item"><div class="grow s" style="color:var(--muted)">진행 중이거나 대기 중인 작업이 없습니다</div></div>`}
        ${done.length ? (state.showDone ? done.map(item).join("") + `<button class="more-toggle" id="toggle-done">완료 숨기기</button>` : `<button class="more-toggle" id="toggle-done">완료된 작업 ${done.length}개 보기</button>`) : ""}
        <form class="inline-form" id="add-task">
          <input class="input short" id="new-ticket" placeholder="번호 (예: 유지보수 #11)" maxlength="60">
          <input class="input" id="new-title" placeholder="새 작업 이름" maxlength="200" required>
          <button class="btn primary">추가하고 시작</button>
        </form>
      </div>
    </section>`;
  }

  async function renderHome() {
    let data;
    try {
      data = await loadHome();
    } catch (e) {
      if (e.status !== 401) $app.innerHTML = `<div class="boot">${esc(e.message)}<br><br><button class="btn" onclick="location.reload()">다시 시도</button></div>`;
      return;
    }
    const showBriefing = data.features.briefing && data.card && data.card.has_record;
    $app.innerHTML = `${topbar(data, "home")}<main><div class="wrap">
      ${showBriefing ? `<div class="briefing-row"><button class="btn sm" id="btn-brief">✦ 지금 상황 3줄 요약</button></div><div id="brief-box"></div>` : ""}
      ${cardHTML(data)}
      ${tasksHTML(data)}
    </div></main>`;
    bindTopbar(renderHome);

    const c = data.card;
    $("#btn-stop")?.addEventListener("click", () => go("#/stop"));
    $("#btn-prompt")?.addEventListener("click", () => showPrompt(c));
    $("#btn-brief")?.addEventListener("click", () => runBriefing(c, data));
    $("#focus-add-task")?.addEventListener("click", () => $("#new-title")?.focus());

    $("#first-project")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const name = $("#first-project-name").value.trim();
      if (!name) return;
      try {
        const p = await api("POST", "/api/projects", { name });
        state.projectId = p.id;
        toast("프로젝트를 만들었습니다. 이제 작업을 추가하세요");
        await renderHome();
        $("#new-title")?.focus();
      } catch (err) { toast(err.message, true); }
    });

    $("#quick-next")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const next = $("#quick-next-input").value.trim();
      if (!next) { $("#quick-next-input").focus(); return; }
      try {
        await api("POST", `/api/tasks/${c.task.id}/records`, {
          type: "manual", what: c.what, where: c.where, next, memo: c.memo, links: c.links, vcs_raw: c.vcs_raw,
        });
        toast("다음엔을 저장했습니다");
        renderHome();
      } catch (err) { toast(err.message, true); }
    });

    $$("[data-start]").forEach((b) => b.addEventListener("click", async () => {
      b.disabled = true;
      try {
        await api("POST", `/api/tasks/${b.dataset.start}/start`);
        toast("활성 작업을 바꿨습니다");
        renderHome();
        window.scrollTo({ top: 0, behavior: "smooth" });
      } catch (err) { toast(err.message, true); b.disabled = false; }
    }));
    $("#toggle-done")?.addEventListener("click", () => { state.showDone = !state.showDone; renderHome(); });

    $("#add-task")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const title = $("#new-title").value.trim();
      if (!title) return;
      try {
        await api("POST", `/api/projects/${data.project.id}/tasks`, { title, ticket_no: $("#new-ticket").value.trim(), start: true });
        toast("작업을 추가하고 시작했습니다");
        await renderHome();
        window.scrollTo({ top: 0, behavior: "smooth" });
      } catch (err) { toast(err.message, true); }
    });

    if (data.user.is_demo && c && !lsGet("guideDone")) startGuide();
  }

  function showPrompt(card) {
    const text = buildPrompt(card);
    const m = modal({
      title: "AI 프롬프트",
      body: `<p class="hint" style="margin:0 0 10px">opencode·ChatGPT·Claude 등 어떤 AI 도구에 붙여넣어도 됩니다.</p><pre id="prompt-text">${esc(text)}</pre>`,
      foot: `<button class="btn" data-close>닫기</button><button class="btn primary" id="copy-prompt">클립보드에 복사</button>`,
    });
    const btn = $("#copy-prompt", m.el);
    btn.focus();
    btn.addEventListener("click", async () => {
      if (await copyText(text)) { toast("프롬프트를 복사했습니다"); m.close(); }
      else toast("복사하지 못했습니다. 텍스트를 직접 선택해 복사하세요", true);
    });
  }

  async function quickCopyPrompt(card) {
    if (!card || !card.has_record) return;
    if (await copyText(buildPrompt(card))) toast("AI 프롬프트를 복사했습니다");
    else showPrompt(card);
  }

  async function runBriefing(card, data) {
    const box = $("#brief-box");
    const btn = $("#btn-brief");
    if (!data.user.is_demo && !lsGet("briefConsent")) {
      const ok = await confirmBox("복귀 브리핑 안내",
        "선택한 기록이 Google Gemini로 전송됩니다.<br><b>무료 키 사용 시 기밀·민감 정보는 넣지 마세요.</b><br><br><span class='hint'>전송 범위: 이 작업의 최근 기록 10건, 메모, 수정 파일 목록</span>",
        "동의하고 요약");
      if (!ok) return;
      lsSet("briefConsent", "1");
    }
    btn.disabled = true;
    box.innerHTML = `<div class="briefing"><span class="memo-preview">요약 중…</span></div>`;
    try {
      const r = await api("POST", `/api/tasks/${card.task.id}/briefing`);
      const lines = r.summary.split("\n").filter((l) => l.trim()).map((l) => `<div class="b-line">${esc(l)}</div>`).join("");
      const meta = [];
      if (r.demo) meta.push("체험 모드 · 미리 만든 요약 (API 호출 없음)");
      else meta.push(r.cached ? "기록이 바뀌지 않아 저장된 요약을 보여줍니다" : "방금 새로 요약했습니다");
      if (r.stale) meta.push("기록이 바뀌었지만 체험 모드에서는 새로 요약하지 않습니다");
      meta.push(`${esc(r.model)} · ${fmtTime(r.created_at)}`);
      box.innerHTML = `<div class="briefing">${lines}<div class="b-meta">${meta.map((m) => `<span>${m}</span>`).join("<span>·</span>")}</div></div>`;
    } catch (e) {
      box.innerHTML = `<div class="briefing error">${esc(e.status === 502 ? "요약을 가져오지 못했습니다. 잠시 후 다시 시도하세요" : e.message)}</div>`;
    } finally {
      btn.disabled = false;
    }
  }

  // 체험 모드 첫 방문 가이드
  function startGuide() {
    const steps = [
      ["#resume-card", "어제 멈춘 작업이 <b>카드 한 장</b>으로 바로 보입니다. 무엇을 · 어디까지 · 다음엔, 수정 중 파일과 브랜치까지."],
      ["#btn-prompt", "<b>AI 프롬프트</b>를 복사해 opencode 같은 AI 도구에 붙여넣으면 바로 이어서 작업합니다."],
      ["#btn-stop", "회의·호출로 급히 떠날 땐 <b>[멈춤 기록]</b> → 다음엔 한 줄 → <kbd>Enter</kbd>. 10초면 끝납니다."],
    ];
    let i = 0;
    let bubble = null;
    let hl = null;
    const end = () => {
      bubble?.remove();
      hl?.classList.remove("guide-hl");
      lsSet("guideDone", "1");
      window.removeEventListener("hashchange", end);
    };
    const show = () => {
      bubble?.remove();
      hl?.classList.remove("guide-hl");
      const [sel, text] = steps[i];
      hl = $(sel);
      if (!hl) { end(); return; }
      hl.classList.add("guide-hl");
      bubble = document.createElement("div");
      bubble.className = "guide-bubble";
      bubble.innerHTML = `<div class="step">${i + 1} / ${steps.length}</div><p>${text}</p>
        <div class="actions"><button class="btn ghost sm" data-g="close">닫기</button><button class="btn primary sm" data-g="next">${i < steps.length - 1 ? "다음" : "시작하기"}</button></div>`;
      document.body.appendChild(bubble);
      const r = hl.getBoundingClientRect();
      const bw = bubble.offsetWidth;
      const bh = bubble.offsetHeight;
      const left = Math.min(Math.max(16, r.left + window.scrollX), window.scrollX + document.documentElement.clientWidth - bw - 16);
      const below = r.bottom + bh + 16 < window.innerHeight || r.top < bh + 16;
      const top = below ? r.bottom + window.scrollY + 12 : r.top + window.scrollY - bh - 12;
      bubble.style.left = `${left}px`;
      bubble.style.top = `${top}px`;
      bubble.addEventListener("click", (e) => {
        const g = e.target.closest("[data-g]")?.dataset.g;
        if (g === "close") end();
        if (g === "next") { i += 1; if (i >= steps.length) end(); else show(); }
      });
    };
    window.addEventListener("hashchange", end);
    show();
  }

  // ------------------------------------------------------------ S2 멈춤 기록

  async function renderStop() {
    let data = state.home;
    try {
      if (!data) data = await loadHome();
    } catch (e) { return; }
    const card = data.card;
    if (!card) { toast("먼저 이어갈 작업을 시작하세요", true); go("#/"); return; }

    let draft;
    try {
      draft = await api("GET", `/api/tasks/${card.task.id}/draft`);
    } catch (e) { if (e.status !== 401) { toast(e.message, true); go("#/"); } return; }
    const latest = draft.latest;
    const restored = latest && latest.type === "auto";
    const carry = draft.carry;
    const init = {
      what: carry.what,
      where: carry.where,
      next: restored ? latest.next : "",
      memo: restored ? latest.memo : "",
      links: carry.links.join("\n"),
      vcs_raw: carry.vcs_raw,
    };
    const moreOpen = Boolean(init.memo);
    const carried = [init.vcs_raw && "VCS 상태", init.links && "링크"].filter(Boolean).join("·");

    $app.innerHTML = `${topbar(data, "stop")}<main><div class="wrap">
      <div class="stop-head">
        <div class="eyebrow">[${esc(draft.project.name)}] 멈춤 기록</div>
        <h1>${esc(draft.task.label)}</h1>
      </div>
      ${restored ? `<div class="loaded-note">자동 저장된 초안(${fmtTime(latest.created_at)})을 불러왔습니다.</div>` : ""}
      <form id="stop-form" autocomplete="off">
        <div class="field">
          <label class="label" for="f-next">다음엔<span class="req">*</span></label>
          <input class="input next-input" id="f-next" maxlength="2000" placeholder="다음에 할 일 한 줄 — Enter로 저장" value="${esc(init.next)}">
          <div id="next-error" class="hint hidden" style="color:var(--danger)">다음엔을 한 줄 적거나 [그냥 저장]을 누르세요.</div>
        </div>
        <div class="field">
          <label class="label" for="f-what">무엇을</label>
          <input class="input" id="f-what" maxlength="2000" placeholder="예: 환불 금액 계산 오류 수정" value="${esc(init.what)}">
        </div>
        <div class="field">
          <label class="label" for="f-where">어디까지 <span class="hint" style="display:inline">파일·함수·줄 등</span></label>
          <textarea class="textarea" id="f-where" rows="2" maxlength="2000" placeholder="예: RefundService.py calc_refund() — 부분 환불 분기 작성 완료">${esc(init.where)}</textarea>
        </div>
        <details class="more" ${moreOpen ? "open" : ""}>
          <summary><span>메모 · 관련 링크 · git/svn status 붙여넣기${carried ? ` <span class="chip" style="margin-left:4px">이전 ${carried} 유지</span>` : ""}</span></summary>
          <div class="inner">
            <div class="field"><label class="label" for="f-memo">메모 <span class="hint" style="display:inline">메일·회의록·일정을 그대로 붙여넣기</span></label>
              <textarea class="textarea" id="f-memo" rows="3" maxlength="20000">${esc(init.memo)}</textarea></div>
            <div class="field"><label class="label" for="f-links">관련 링크 <span class="hint" style="display:inline">한 줄에 하나</span></label>
              <textarea class="textarea" id="f-links" rows="2" maxlength="5000" placeholder="https://…">${esc(init.links)}</textarea></div>
            <div class="field"><label class="label" for="f-vcs">VCS 상태 <span class="hint" style="display:inline">git status · git status --short · svn status 결과</span></label>
              <textarea class="textarea mono" id="f-vcs" rows="4" maxlength="20000" placeholder="On branch fix/refund-7&#10;	modified:   RefundService.py">${esc(init.vcs_raw)}</textarea>
              <div class="vcs-preview" id="vcs-preview"></div></div>
          </div>
        </details>
        <div class="stop-actions">
          <button class="btn primary lg" type="submit" id="save-btn">저장 <kbd>Enter</kbd></button>
          <button class="btn lg" type="button" id="save-empty">그냥 저장</button>
          <button class="btn ghost lg" type="button" id="cancel-btn">취소 <kbd class="hide-sm">Esc</kbd></button>
        </div>
        <p class="hint">"다음엔"을 쓸 시간도 없으면 <b>[그냥 저장]</b>. 버튼을 깜빡해도 입력 내용은 5분마다, 그리고 창을 닫거나 탭을 바꿀 때 자동 저장됩니다.</p>
      </form>
    </div></main>
    <div class="autosave"><div class="wrap"><span class="dot" id="as-dot"></span><span id="as-text">자동 저장 대기 중 · 변경하면 5분마다 저장</span></div></div>`;
    bindTopbar(() => go("#/"));

    const f = {
      next: $("#f-next"), what: $("#f-what"), where: $("#f-where"),
      memo: $("#f-memo"), links: $("#f-links"), vcs: $("#f-vcs"),
    };
    const taskId = card.task.id;
    const url = `/api/tasks/${taskId}/records`;
    const collect = () => ({
      what: f.what.value.trim(),
      where: f.where.value.trim(),
      next: f.next.value.trim(),
      memo: f.memo.value.trim(),
      links: f.links.value.split("\n").map((s) => s.trim()).filter(Boolean),
      vcs_raw: f.vcs.value.trim(),
    });
    const ser = () => JSON.stringify(collect());
    const baseline = ser();
    let lastSaved = baseline;
    let submitted = false;
    const setStatus = (cls, text) => { $("#as-dot").className = "dot " + cls; $("#as-text").textContent = text; };
    const shouldSave = () => { const s = ser(); return !submitted && s !== baseline && s !== lastSaved; };

    async function autosave() {
      if (!shouldSave()) return;
      const s = ser();
      setStatus("busy", "자동 저장 중…");
      try {
        await api("POST", url, { ...collect(), type: "auto" });
        lastSaved = s;
        setStatus("ok", `자동 저장됨 · ${nowHM()}`);
      } catch (e) {
        setStatus("err", `자동 저장 실패 · ${e.message}`);
      }
    }
    // 창 닫기·탭 전환·페이지 이동: 응답을 기다릴 수 없으므로 sendBeacon(실패 시 keepalive fetch)
    // 앱 안에서 화면을 옮길 때는 다음 화면이 초안을 읽을 수 있도록 응답을 기다린다 (inApp=true)
    function flush(inApp = false) {
      if (!shouldSave()) return Promise.resolve();
      const s = ser();
      const payload = JSON.stringify({ ...collect(), type: "auto" });
      lastSaved = s;
      setStatus("ok", `자동 저장됨 · ${nowHM()}`);
      const viaFetch = () => fetch(url, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: payload, keepalive: true, credentials: "same-origin",
      }).catch(() => {});
      if (inApp) return viaFetch();
      let sent = false;
      try { sent = navigator.sendBeacon(url, new Blob([payload], { type: "application/json" })); } catch (e) { sent = false; }
      return sent ? Promise.resolve() : viaFetch();
    }
    const onVis = () => { if (document.visibilityState === "hidden") flush(); };
    const onHide = () => flush();
    const timer = setInterval(autosave, AUTOSAVE_MS);
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("pagehide", onHide);
    const onKey = (e) => { if (e.key === "Escape" && !document.querySelector(".modal-bg")) go("#/"); };
    document.addEventListener("keydown", onKey);
    pageCleanup = () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("pagehide", onHide);
      document.removeEventListener("keydown", onKey);
      return flush(true);
    };

    // 입력이 생기면 상태 문구 갱신
    $("#stop-form").addEventListener("input", () => {
      $("#next-error").classList.add("hidden");
      if (!/ok|busy|err/.test($("#as-dot").className)) setStatus("", "변경됨 · 5분마다, 창을 닫을 때 자동 저장");
    });

    // VCS 미리보기
    let vcsTimer;
    const preview = async () => {
      const text = f.vcs.value.trim();
      const box = $("#vcs-preview");
      if (!text) { box.innerHTML = ""; return; }
      try {
        const r = await api("POST", "/api/vcs/parse", { text });
        if (!r.files.length && !r.branch) {
          box.innerHTML = `<span class="hint">형식을 알아보지 못했습니다 — 원문 그대로 저장됩니다.</span>`;
        } else {
          box.innerHTML = `<div class="hint" style="margin-bottom:6px">${r.vcs === "unknown" ? "" : `<span class="chip ok">${r.vcs.toUpperCase()} 감지</span> `}${r.branch ? `브랜치 <span class="branch">${esc(r.branch)}</span> · ` : ""}파일 ${r.files.length}개</div>${filesHTML(r.files)}`;
        }
      } catch (e) { box.innerHTML = ""; }
    };
    f.vcs.addEventListener("input", () => { clearTimeout(vcsTimer); vcsTimer = setTimeout(preview, 350); });
    if (init.vcs_raw) preview();

    const save = async (allowEmpty) => {
      const body = collect();
      if (!body.next && !allowEmpty) {
        $("#next-error").classList.remove("hidden");
        f.next.focus();
        return;
      }
      $("#save-btn").disabled = true;
      $("#save-empty").disabled = true;
      try {
        await api("POST", url, { ...body, type: "manual" });
        submitted = true;
        state.home = null;
        toast(body.next ? "멈춤 기록을 저장했습니다" : "저장했습니다 · 돌아오면 다음엔을 적어주세요");
        go("#/");
      } catch (e) {
        toast(`저장하지 못했습니다: ${e.message}`, true);
        $("#save-btn").disabled = false;
        $("#save-empty").disabled = false;
      }
    };
    $("#stop-form").addEventListener("submit", (e) => { e.preventDefault(); save(false); });
    $("#save-empty").addEventListener("click", () => save(true));
    $("#cancel-btn").addEventListener("click", () => go("#/"));
    f.next.focus();
  }

  // ------------------------------------------------------------ S3 히스토리

  function recordHTML(r) {
    const rows = [];
    if (r.what) rows.push(`<div class="row"><span class="k">무엇을</span><span class="v">${esc(r.what)}</span></div>`);
    if (r.where) rows.push(`<div class="row"><span class="k">어디까지</span><span class="v">${esc(r.where)}</span></div>`);
    rows.push(`<div class="row"><span class="k">다음엔</span><span class="v">${r.next ? `<b>${esc(r.next)}</b>` : `<span style="color:var(--faint)">(기록 없음)</span>`}</span></div>`);
    if (r.vcs_files.length) rows.push(`<div class="row"><span class="k">파일</span><span class="v">${filesHTML(r.vcs_files)}</span></div>`);
    if (r.vcs_branch) rows.push(`<div class="row"><span class="k">브랜치</span><span class="v branch">${esc(r.vcs_branch)}</span></div>`);
    if (r.links.length) rows.push(`<div class="row"><span class="k">링크</span><span class="v">${linksHTML(r.links)}</span></div>`);
    return `<div class="rec">
      <div class="rec-head"><span>${fmtTime(r.created_at)}</span><span class="chip ${r.type === "manual" ? "accent" : ""}">${r.type === "manual" ? "수동 기록" : "자동 저장"}</span></div>
      <div class="rows">${rows.join("")}</div>
      ${r.memo ? `<details class="raw"><summary>메모</summary><pre>${esc(r.memo)}</pre></details>` : ""}
      ${r.vcs_raw ? `<details class="raw"><summary>VCS 상태 원문</summary><pre>${esc(r.vcs_raw)}</pre></details>` : ""}
    </div>`;
  }

  function autoLineHTML(r) {
    const one = [r.what, r.where, r.next].filter(Boolean).join(" · ") || "(빈 초안)";
    return `<button class="rec-auto" data-rec="${r.id}"><span class="chip">자동 저장</span><span class="one">${esc(one)}</span><span>${fmtTime(r.created_at)}</span></button>`;
  }

  async function renderTaskBody(task, container) {
    container.innerHTML = `<div class="memo-preview">불러오는 중…</div>`;
    let r;
    try { r = await api("GET", `/api/tasks/${task.id}/records`); } catch (e) { container.innerHTML = `<div class="form-error">${esc(e.message)}</div>`; return; }
    const tools = [];
    if (task.status !== "active") tools.push(`<button class="btn primary sm" data-act="start">▶ 이 작업 시작</button>`);
    if (task.status !== "done") tools.push(`<button class="btn sm" data-act="done">완료 처리</button>`);
    if (task.status === "done") tools.push(`<button class="btn sm" data-act="reopen">대기로 되돌리기</button>`);
    tools.push(`<button class="btn sm" data-act="rename">이름 수정</button>`);
    const byId = new Map(r.records.map((x) => [String(x.id), x]));
    container.innerHTML = `<div class="task-tools">${tools.join("")}</div>
      ${r.records.length ? r.records.map((x) => (x.type === "manual" ? recordHTML(x) : autoLineHTML(x))).join("") : `<div class="memo-preview">아직 기록이 없습니다.</div>`}`;
    $$(".rec-auto", container).forEach((b) => b.addEventListener("click", () => {
      b.outerHTML = recordHTML(byId.get(b.dataset.rec));
    }));
    $$("[data-act]", container).forEach((b) => b.addEventListener("click", async () => {
      const act = b.dataset.act;
      try {
        if (act === "start") {
          await api("POST", `/api/tasks/${task.id}/start`);
          toast("활성 작업을 바꿨습니다");
          go("#/");
          return;
        }
        if (act === "done") await api("PATCH", `/api/tasks/${task.id}`, { status: "done" });
        if (act === "reopen") await api("PATCH", `/api/tasks/${task.id}`, { status: "waiting" });
        if (act === "rename") {
          const title = await askText("작업 이름 수정", "작업 이름", task.title);
          if (!title) return;
          const ticket = await askText("유지보수 번호 수정", "번호 (비우면 없음)", task.ticket_no);
          await api("PATCH", `/api/tasks/${task.id}`, { title, ticket_no: ticket ?? "" });
        }
        toast("변경했습니다");
        renderHistory();
      } catch (e) { toast(e.message, true); }
    }));
  }

  async function renderHistory() {
    let data;
    try { data = await loadHome(); } catch (e) { return; }
    const groups = [["active", "진행 중"], ["waiting", "대기"], ["done", "완료"]];
    const taskItem = (t) => `<div class="list" style="margin-bottom:8px">
      <div class="item task-row ${t.status}" data-task="${t.id}" role="button" tabindex="0" aria-expanded="${state.openTasks.has(t.id)}">
        <div class="grow"><div class="t">${esc(t.label)}</div>
          <div class="s">${t.last_record_at ? `마지막 기록 ${fmtTime(t.last_record_at)}` : "기록 없음"}</div></div>
        <span class="chip ${t.status === "active" ? "accent" : t.status === "done" ? "ok" : ""}">${STATUS_LABEL[t.status]}</span>
        <span style="color:var(--faint)">${state.openTasks.has(t.id) ? "▴" : "▾"}</span>
      </div>
      <div class="task-body ${state.openTasks.has(t.id) ? "" : "hidden"}" data-body="${t.id}"></div>
    </div>`;
    $app.innerHTML = `${topbar(data, "history")}<main><div class="wrap">
      ${data.project ? `<div class="section-head"><h2 style="font-size:20px">${esc(data.project.name)} · 작업 히스토리</h2>
        <button class="btn ghost sm" id="rename-project">프로젝트 이름 수정</button></div>
        <p class="hint" style="margin-top:-4px">작업을 누르면 기록이 최신순으로 펼쳐집니다. 수동 기록은 크게, 자동 저장은 한 줄로 접혀 있습니다.</p>
        ${groups.map(([st, label]) => {
          const ts = data.tasks.filter((t) => t.status === st);
          return ts.length ? `<div class="group-title">${label} ${ts.length}</div>${ts.map(taskItem).join("")}` : "";
        }).join("") || `<div class="card empty"><div class="big">작업이 없습니다</div><p>시작 화면에서 작업을 추가하세요.</p><a class="btn primary" href="#/">시작 화면으로</a></div>`}`
        : `<div class="card empty"><div class="big">프로젝트가 없습니다</div><a class="btn primary" href="#/">시작 화면으로</a></div>`}
    </div></main>`;
    bindTopbar(renderHistory);

    $("#rename-project")?.addEventListener("click", async () => {
      const name = await askText("프로젝트 이름 수정", "프로젝트 이름", data.project.name);
      if (!name) return;
      try { await api("PATCH", `/api/projects/${data.project.id}`, { name }); renderHistory(); } catch (e) { toast(e.message, true); }
    });

    const tasks = new Map(data.tasks.map((t) => [t.id, t]));
    const toggle = (row) => {
      const id = Number(row.dataset.task);
      const body = $(`[data-body="${id}"]`);
      const open = body.classList.toggle("hidden") === false;
      row.setAttribute("aria-expanded", String(open));
      row.lastElementChild.textContent = open ? "▴" : "▾";
      if (open) { state.openTasks.add(id); renderTaskBody(tasks.get(id), body); } else state.openTasks.delete(id);
    };
    $$(".task-row").forEach((row) => {
      row.addEventListener("click", () => toggle(row));
      row.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(row); } });
    });
    state.openTasks.forEach((id) => {
      const body = $(`[data-body="${id}"]`);
      if (body && tasks.has(id)) renderTaskBody(tasks.get(id), body);
      else state.openTasks.delete(id);
    });
  }

  // ------------------------------------------------------------ 단축키 (시작 화면)

  document.addEventListener("keydown", (e) => {
    const route = location.hash || "#/";
    if (route !== "#/" || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
    if (e.target.closest("input, textarea, select, [contenteditable]") || document.querySelector(".modal-bg")) return;
    const card = state.home && state.home.card;
    if (!card) return;
    // 한글 입력 상태에서도 동작하도록 e.code 사용
    if (e.code === "KeyS") { e.preventDefault(); go("#/stop"); }
    if (e.code === "KeyC") { e.preventDefault(); quickCopyPrompt(card); }
  });

  // ------------------------------------------------------------ router

  async function render() {
    if (pageCleanup) { const c = pageCleanup; pageCleanup = null; await c(); }
    document.querySelectorAll(".modal-bg, .guide-bubble").forEach((el) => el.remove());
    const route = location.hash || "#/";
    window.scrollTo(0, 0);
    if (route === "#/login") return renderLogin();
    if (route === "#/stop") return renderStop();
    if (route === "#/history") return renderHistory();
    return renderHome();
  }

  window.addEventListener("hashchange", render);
  render();
})();

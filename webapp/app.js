/* Итоги года — мини-апп.
 *
 * Экраны: загрузка → (подписка) → «удержи палец» → слайды-истории → финал.
 * Уточки — живые TGS-стикеры через lottie, поверх — снег и искры на canvas.
 * Открыть без Telegram можно с ?demo — подставятся тестовые итоги.
 */
(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => Array.from(root.querySelectorAll(s));
  const DEMO = new URLSearchParams(location.search).has("demo");
  const YEAR = 2026;

  // ----------------------------------------------------------- темы
  const THEMES = {
    festive: ["#3b82ff", "#7c3aed"],
    hold: ["#37b6f6", "#1f6fe0"],
    blue: ["#2aabee", "#1e5fd8"],
    purple: ["#9b6bff", "#5b2fd0"],
    green: ["#2fd17f", "#11905a"],
    orange: ["#ffab40", "#f05a28"],
    red: ["#ff5f6d", "#c9184a"],
    pink: ["#ff7eb6", "#b33bd6"],
    teal: ["#1fd1c1", "#0f74b8"],
    night: ["#2a3a8c", "#3a1670"],
    ice: ["#7dd6ff", "#3b7fe3"],
    fire: ["#ff8a00", "#b0102e"],
    dark: ["#3a3f52", "#15171f"],
    gold: ["#ffc53d", "#e0662a"],
  };

  // ----------------------------------------------------------- утилиты
  const fmt = (n) => Number(n).toLocaleString("ru-RU");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const plural = (n, one, few, many) => {
    n = Math.abs(n) % 100;
    const d = n % 10;
    if (n > 10 && n < 20) return many;
    if (d > 1 && d < 5) return few;
    if (d === 1) return one;
    return many;
  };
  const haptic = (kind = "light") => {
    try {
      const h = tg && tg.HapticFeedback;
      if (!h) return;
      if (kind === "success" || kind === "error" || kind === "warning") h.notificationOccurred(kind);
      else if (kind === "select") h.selectionChanged();
      else h.impactOccurred(kind);
    } catch (_) { /* старый клиент */ }
  };
  const can = (v) => !!(tg && tg.isVersionAtLeast && tg.isVersionAtLeast(v));
  let toastTimer = 0;
  const toast = (text) => {
    const el = $("#toast");
    unmountIn(el); el.innerHTML = text; mountIn(el);
    el.classList.add("on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("on"), 2200);
  };
  const openLink = (url) => {
    if (!url) return;
    if (tg && /^https:\/\/t\.me\//.test(url)) tg.openTelegramLink(url);
    else if (tg) tg.openLink(url);
    else window.open(url, "_blank");
  };

  // ----------------------------------------------------------- Telegram
  function setupTelegram() {
    if (!tg) return;
    try {
      tg.ready();
      tg.expand();
      if (can("7.7")) tg.disableVerticalSwipes();
      const mobile = ["android", "ios"].includes(tg.platform);
      if (mobile && can("8.0")) tg.requestFullscreen();
      if (can("6.2")) tg.enableClosingConfirmation && tg.disableClosingConfirmation();
    } catch (_) { /* ок */ }
  }

  // ----------------------------------------------------------- фон
  // Обои в духе чатов Telegram: четыре цветные точки, цвет каждого пикселя —
  // смесь по расстоянию до них. Считаем на маленьком холсте и растягиваем:
  // получается мягкий «жидкий» градиент. При смене слайда точки делают шаг
  // по кругу, а цвета плавно перетекают.
  const wall = (() => {
    const c = $("#wall");
    const W = 50, H = 90;
    c.width = W; c.height = H;
    const ctx = c.getContext("2d");
    const img = ctx.createImageData(W, H);
    // 8 позиций по кругу, точки стоят через одну
    const SPOTS = [[0.80, 0.10], [0.60, 0.20], [0.35, 0.25], [0.25, 0.60], [0.20, 0.90], [0.40, 0.80], [0.65, 0.75], [0.75, 0.40]];
    let phase = 0, shown = 0;
    let from = null, to = null, mixT = 1;
    const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
    const lerp = (a, b, t) => a + (b - a) * t;
    const mixC = (a, b, t) => a.map((v, i) => lerp(v, b[i], t));
    const shade = (c, k) => c.map((v) => Math.max(0, Math.min(255, k > 0 ? v + (255 - v) * k : v * (1 + k))));
    function palette(colors) {
      const a = hex(colors[0]), b = hex(colors[1]);
      return [shade(a, 0.18), mixC(a, b, 0.35), shade(b, -0.08), shade(mixC(a, b, 0.7), 0.1)];
    }
    function spot(i, ph) {
      const k = (i * 2 + ph) % 8;
      const k2 = (k + 1) % 8;
      const f = ph - Math.floor(ph);
      const p = SPOTS[Math.floor(k)], q = SPOTS[Math.floor(k2)];
      return [lerp(p[0], q[0], f), lerp(p[1], q[1], f)];
    }
    function draw(cols, ph) {
      const pts = [0, 1, 2, 3].map((i) => spot(i, ph));
      const d = img.data;
      let o = 0;
      for (let y = 0; y < H; y++) {
        const ny = y / H;
        for (let x = 0; x < W; x++) {
          const nx = x / W;
          let r = 0, g = 0, b = 0, sum = 0;
          for (let i = 0; i < 4; i++) {
            const dx = nx - pts[i][0], dy = (ny - pts[i][1]) * 0.6;
            let w = Math.max(0, 0.9 - Math.sqrt(dx * dx + dy * dy));
            w = w * w * w * w;
            r += cols[i][0] * w; g += cols[i][1] * w; b += cols[i][2] * w; sum += w;
          }
          d[o++] = r / sum; d[o++] = g / sum; d[o++] = b / sum; d[o++] = 255;
        }
      }
      ctx.putImageData(img, 0, 0);
    }
    let last = 0;
    function loop(now) {
      const dt = Math.min(0.05, (now - last) / 1000 || 0);
      last = now;
      shown += (phase - shown) * Math.min(1, dt * 2.6) + dt * 0.02; // шаг + еле заметный дрейф
      phase += dt * 0.02;
      if (mixT < 1) mixT = Math.min(1, mixT + dt * 1.6);
      const t = mixT * mixT * (3 - 2 * mixT);
      const cols = from ? [0, 1, 2, 3].map((i) => mixC(from[i], to[i], t)) : to;
      if (cols) draw(cols, ((shown % 8) + 8) % 8);
      requestAnimationFrame(loop);
    }
    requestAnimationFrame(loop);
    return {
      set(colors) {
        const next = palette(colors);
        if (!to) { to = next; mixT = 1; return; }
        const t = mixT * mixT * (3 - 2 * mixT);
        from = [0, 1, 2, 3].map((i) => mixC(from ? from[i] : to[i], to[i], from ? t : 1));
        to = next; mixT = 0;
        phase = Math.round(phase) + 1; // точки шагают по кругу, как в Telegram
      },
      step() { phase = Math.round(phase) + 1; },
    };
  })();

  let currentTheme = "";
  function setTheme(name) {
    const colors = Array.isArray(name) ? name : THEMES[name] || THEMES.festive;
    const key = colors.join();
    if (key === currentTheme) return;
    currentTheme = key;
    wall.set(colors);
    document.body.style.background = colors[1];
    try {
      if (tg && can("6.1")) {
        tg.setHeaderColor(colors[0]);
        tg.setBackgroundColor(colors[1]);
      }
      if (tg && can("7.10")) tg.setBottomBarColor(colors[1]);
    } catch (_) { /* ок */ }
  }

  // ----------------------------------------------------------- lottie
  const jsonCache = new Map();
  function loadJson(url) {
    if (!jsonCache.has(url)) jsonCache.set(url, fetch(url).then((r) => (r.ok ? r.json() : null)).catch(() => null));
    return jsonCache.get(url);
  }
  function stickerUrl(code) {
    return code.startsWith("g:") ? `/app/gifts/${code.slice(2)}.json` : `/app/stickers/${code}.json`;
  }
  function mount(el) {
    const code = el.dataset.sticker;
    if (!code || el._anim || el._loading) return;
    el._loading = true;
    loadJson(stickerUrl(code)).then((data) => {
      el._loading = false;
      if (!data || el._anim || !el.isConnected || el._dead) return;
      el._anim = lottie.loadAnimation({
        container: el, renderer: "svg", loop: true, autoplay: true,
        animationData: typeof structuredClone === "function" ? structuredClone(data) : JSON.parse(JSON.stringify(data)),
        rendererSettings: { preserveAspectRatio: "xMidYMid meet", progressiveLoad: true },
      });
    });
    el._dead = false;
  }
  function unmount(el) {
    el._dead = true;
    if (el._anim) { el._anim.destroy(); el._anim = null; }
  }
  const mountIn = (root) => $$(".lottie", root).forEach(mount);
  const unmountIn = (root) => $$(".lottie", root).forEach(unmount);
  const preload = (codes) => codes.forEach((c) => loadJson(stickerUrl(c)));

  // ----------------------------------------------------------- снег
  // Настоящие снежинки: шестилучевые кристаллы, нарисованные заранее в
  // спрайты (дёшево рисовать каждый кадр). Мелкие размытые точки — дальний план.
  function flakeSprite(size, kind) {
    const c = document.createElement("canvas");
    const pad = 4;
    c.width = c.height = size + pad * 2;
    const x = c.getContext("2d");
    const r = size / 2;
    x.translate(r + pad, r + pad);
    x.strokeStyle = "#fff";
    x.lineCap = "round";
    x.shadowColor = "rgba(190,225,255,.9)";
    x.shadowBlur = size / 9;
    x.lineWidth = Math.max(1, size / 18);
    for (let i = 0; i < 6; i++) {
      x.save();
      x.rotate((i * Math.PI) / 3);
      x.beginPath();
      x.moveTo(0, 0); x.lineTo(0, -r);
      // веточки: у каждого вида свой рисунок
      const twigs = kind === 0 ? [[0.45, 0.32], [0.72, 0.22]]
        : kind === 1 ? [[0.3, 0.2], [0.55, 0.3], [0.8, 0.16]]
        : [[0.6, 0.38]];
      for (const [at, len] of twigs) {
        x.moveTo(0, -r * at); x.lineTo(-r * len, -r * (at + len * 0.8));
        x.moveTo(0, -r * at); x.lineTo(r * len, -r * (at + len * 0.8));
      }
      x.stroke();
      if (kind === 2) { // ромбик на конце луча
        x.beginPath();
        x.moveTo(0, -r * 0.82); x.lineTo(r * 0.1, -r * 0.92); x.lineTo(0, -r); x.lineTo(-r * 0.1, -r * 0.92); x.closePath();
        x.stroke();
      }
      x.restore();
    }
    x.beginPath(); x.arc(0, 0, Math.max(1, size / 14), 0, Math.PI * 2); x.fillStyle = "#fff"; x.fill();
    return c;
  }

  function startSnow() {
    const c = $("#snow");
    const ctx = c.getContext("2d");
    let w, h, flakes;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const sprites = [];
    for (const size of [14, 20, 28]) for (let k = 0; k < 3; k++) sprites.push(flakeSprite(size * dpr, k));
    function resize() {
      w = c.width = innerWidth * dpr;
      h = c.height = innerHeight * dpr;
      c.style.width = innerWidth + "px";
      c.style.height = innerHeight + "px";
      const area = innerWidth * innerHeight;
      const crystals = Math.round(area / 16000);
      const dots = Math.round(area / 9000);
      flakes = [];
      for (let i = 0; i < dots; i++) flakes.push({
        dot: true, x: Math.random() * w, y: Math.random() * h,
        r: (Math.random() * 1.6 + 0.6) * dpr, s: (Math.random() * 0.35 + 0.2) * dpr,
        a: Math.random() * 0.35 + 0.2, p: Math.random() * Math.PI * 2,
      });
      for (let i = 0; i < crystals; i++) {
        const sp = sprites[Math.floor(Math.random() * sprites.length)];
        const scale = Math.random() * 0.5 + 0.55;
        flakes.push({
          sp, scale, x: Math.random() * w, y: Math.random() * h,
          s: (0.35 + scale * 0.7) * dpr, a: 0.55 + Math.random() * 0.4,
          p: Math.random() * Math.PI * 2, rot: Math.random() * Math.PI, vr: (Math.random() - 0.5) * 0.02,
        });
      }
    }
    resize();
    addEventListener("resize", resize);
    let t = 0;
    (function frame() {
      t += 0.01;
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "#fff";
      for (const f of flakes) {
        f.y += f.s;
        f.x += Math.sin(t + f.p) * (f.dot ? 0.3 : 0.5) * dpr;
        if (f.dot) {
          if (f.y > h + 5) { f.y = -5; f.x = Math.random() * w; }
          ctx.globalAlpha = f.a;
          ctx.beginPath(); ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2); ctx.fill();
          continue;
        }
        const size = f.sp.width * f.scale;
        if (f.y > h + size) { f.y = -size; f.x = Math.random() * w; }
        f.rot += f.vr;
        ctx.globalAlpha = f.a;
        ctx.save();
        ctx.translate(f.x, f.y);
        ctx.rotate(f.rot);
        ctx.drawImage(f.sp, -size / 2, -size / 2, size, size);
        ctx.restore();
      }
      ctx.globalAlpha = 1;
      requestAnimationFrame(frame);
    })();
  }

  // ----------------------------------------------------------- частицы
  const fx = (() => {
    const c = $("#fx");
    const ctx = c.getContext("2d");
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    let parts = [];
    let emitters = [];
    let running = false;
    function resize() {
      c.width = innerWidth * dpr; c.height = innerHeight * dpr;
      c.style.width = innerWidth + "px"; c.style.height = innerHeight + "px";
    }
    resize();
    addEventListener("resize", resize);
    const GOLD = ["#ffe08a", "#ffd166", "#fff", "#ffb347"];
    const CONF = ["#ffd166", "#ff6b9d", "#5ee7ff", "#7cff9a", "#ffffff", "#b388ff"];

    function star(x, y, r) {
      const k = r * 0.3;
      ctx.beginPath();
      ctx.moveTo(x, y - r); ctx.lineTo(x + k, y - k); ctx.lineTo(x + r, y); ctx.lineTo(x + k, y + k);
      ctx.lineTo(x, y + r); ctx.lineTo(x - k, y + k); ctx.lineTo(x - r, y); ctx.lineTo(x - k, y - k);
      ctx.closePath(); ctx.fill();
    }
    function loop() {
      ctx.clearRect(0, 0, c.width, c.height);
      for (const e of emitters) {
        e.life--;
        const el = e.el && e.el.getBoundingClientRect();
        const ex = el ? (el.left + el.width * e.fx) * dpr : e.x;
        const ey = el ? (el.top + el.height * e.fy) * dpr : e.y;
        for (let i = 0; i < e.rate; i++) {
          const a = Math.random() * Math.PI * 2;
          const v = (Math.random() * 3.2 + 1.2) * dpr;
          parts.push({ k: "spark", x: ex, y: ey, vx: Math.cos(a) * v, vy: Math.sin(a) * v - dpr, g: 0.07 * dpr, life: 1, d: Math.random() * 0.03 + 0.025, r: (Math.random() * 2.4 + 1.4) * dpr, c: GOLD[(Math.random() * GOLD.length) | 0] });
        }
      }
      emitters = emitters.filter((e) => e.life > 0 && (!e.el || e.el.isConnected));
      for (const p of parts) {
        p.x += p.vx; p.y += p.vy; p.vy += p.g; p.vx *= 0.985; p.life -= p.d;
        if (p.k === "conf") p.rot += p.vr;
      }
      parts = parts.filter((p) => p.life > 0 && p.y < c.height + 40);
      for (const p of parts) {
        ctx.globalAlpha = Math.max(0, Math.min(1, p.life));
        ctx.fillStyle = p.c;
        if (p.k === "spark") {
          star(p.x, p.y, p.r * (0.6 + p.life * 0.6));
        } else {
          ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot);
          ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h * Math.abs(Math.cos(p.rot * 1.7)));
          ctx.restore();
        }
      }
      ctx.globalAlpha = 1;
      if (parts.length || emitters.length) requestAnimationFrame(loop);
      else { running = false; ctx.clearRect(0, 0, c.width, c.height); }
    }
    function kick() { if (!running) { running = true; requestAnimationFrame(loop); } }
    return {
      /** Бенгальский огонь, привязанный к элементу (fx, fy — доля от его размеров). */
      sparkler(el, fxr = 0.8, fyr = 0.2, frames = 99999, rate = 3) {
        emitters.push({ el, fx: fxr, fy: fyr, life: frames, rate });
        kick();
      },
      stopAll() { emitters = []; },
      burst(x, y, n = 70) {
        for (let i = 0; i < n; i++) {
          const a = (Math.PI * 2 * i) / n + Math.random() * 0.2;
          const v = (Math.random() * 5 + 3) * dpr;
          parts.push({ k: "spark", x: x * dpr, y: y * dpr, vx: Math.cos(a) * v, vy: Math.sin(a) * v, g: 0.06 * dpr, life: 1, d: 0.014 + Math.random() * 0.012, r: (Math.random() * 2.6 + 1.6) * dpr, c: GOLD[(Math.random() * GOLD.length) | 0] });
        }
        kick();
      },
      confetti(n = 140) {
        for (let i = 0; i < n; i++) {
          const fromLeft = i % 2 === 0;
          const x = (fromLeft ? -10 : innerWidth + 10) * dpr;
          const y = innerHeight * (0.35 + Math.random() * 0.3) * dpr;
          const a = fromLeft ? -Math.PI / 4 - Math.random() * 0.6 : (-3 * Math.PI) / 4 + Math.random() * 0.6;
          const v = (Math.random() * 9 + 7) * dpr;
          parts.push({ k: "conf", x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, g: 0.18 * dpr, life: 1.6, d: 0.006, w: (Math.random() * 6 + 5) * dpr, h: (Math.random() * 8 + 6) * dpr, rot: Math.random() * 6, vr: Math.random() * 0.3 - 0.15, c: CONF[(Math.random() * CONF.length) | 0] });
        }
        kick();
      },
    };
  })();

  // ----------------------------------------------------------- счётчики
  function countUp(el) {
    const to = Number(el.dataset.count);
    if (!isFinite(to)) return;
    const dur = Math.min(1800, 700 + Math.log10(Math.max(10, to)) * 300);
    const start = performance.now();
    const suffix = el.dataset.suffix || "";
    const step = (now) => {
      const t = Math.min(1, (now - start) / dur);
      const e = 1 - Math.pow(1 - t, 4);
      el.innerHTML = fmt(Math.round(to * e)) + suffix;
      if (t < 1) requestAnimationFrame(step);
      else if (to > 0) haptic("select");
    };
    el.innerHTML = "0" + suffix;
    setTimeout(() => requestAnimationFrame(step), 260);
  }

  // ----------------------------------------------------------- экраны
  function show(id) {
    $$(".screen").forEach((s) => {
      const on = s.id === id;
      if (on && !s.classList.contains("active")) { s.classList.add("active"); if (id !== "s-story") mountIn(s); }
      if (!on && s.classList.contains("active")) { s.classList.remove("active"); unmountIn(s); }
    });
  }

  // ----------------------------------------------------------- API
  async function api(path, body = {}) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ init: tg ? tg.initData : "", ...body }),
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(json.error || "http " + res.status), { code: json.error });
    return json;
  }

  // ----------------------------------------------------------- подписка
  function renderSponsors(list) {
    const box = $("#sponsorList");
    box.innerHTML = list.map((s, i) => `
      <div class="sponsor" style="animation-delay:${i * 70}ms" data-i="${i}">
        <div class="num">${i + 1}</div>
        <div class="name">${esc(s.title)}</div>
        <button class="go" data-link="${esc(s.link)}">Подписаться</button>
      </div>`).join("");
    $$(".go", box).forEach((b) => b.addEventListener("click", () => {
      haptic("light");
      openLink(b.dataset.link);
      b.closest(".sponsor").classList.add("done");
      b.innerHTML = `${ic("003")}Открыто`; mountIn(b);
    }));
  }

  async function check() {
    const btn = $("#checkBtn");
    btn.disabled = true;
    $("#checkHint").textContent = "Проверяем…";
    try {
      const r = await api("/api/check");
      if (r.state === "ready") {
        haptic("success");
        $("#checkHint").textContent = "";
        ready(r.data);
      } else {
        haptic("error");
        renderSponsors(r.sponsors);
        $("#checkHint").textContent = "Подпишись на все каналы и нажми ещё раз";
      }
    } catch (e) {
      haptic("error");
      $("#checkHint").textContent = "Не получилось проверить. Попробуй ещё раз";
    } finally {
      btn.disabled = false;
    }
  }

  // ----------------------------------------------------------- удержание
  let DATA = null;
  function ready(data) {
    DATA = data;
    setTheme("festive");
    setTheme("hold"); show("s-hold");
    preload(["039", "031", "010", "018", "032", "029"].concat(data.nominations.map((n) => n.sticker)));
  }

  function setupHold() {
    const hold = $("#hold");
    const ring = $("#holdRing");
    const lit = $$("#fingerLit path");
    const hint = $("#holdHint");
    const HINTS = ["Сканирую отпечаток…", "Собираю твой год…", "Ещё чуть-чуть…"];
    const paint = (p) => lit.forEach((path, i) => {
      // дуги отпечатка загораются от центра к краям, как Touch ID
      const k = Math.max(0, Math.min(1, p * 1.7 - i * 0.04));
      path.style.strokeDashoffset = 1 - k;
      path.style.opacity = k > 0.002 ? 1 : 0; // без «точек» от круглых концов у пустых линий
    });
    const LEN = 578;
    const NEED = 1200;
    let t0 = 0, raf = 0, lastTick = 0;
    const reset = () => {
      cancelAnimationFrame(raf);
      hold.classList.remove("pressing");
      ring.style.transition = "stroke-dashoffset .4s ease";
      ring.style.strokeDashoffset = LEN;
      lit.forEach((path) => { path.style.transition = "stroke-dashoffset .4s ease"; });
      paint(0);
      hint.textContent = "Удерживай палец на отпечатке";
    };
    const tick = (now) => {
      const p = Math.min(1, (now - t0) / NEED);
      ring.style.strokeDashoffset = LEN * (1 - p);
      paint(p);
      hint.textContent = HINTS[Math.min(HINTS.length - 1, Math.floor(p * HINTS.length))];
      if (now - lastTick > 140) { haptic("select"); lastTick = now; }
      if (p >= 1) {
        hold.classList.remove("pressing");
        const r = hold.getBoundingClientRect();
        fx.burst(r.left + r.width / 2, r.top + r.height / 2, 90);
        haptic("success");
        setTimeout(startStory, 380);
        return;
      }
      raf = requestAnimationFrame(tick);
    };
    hold.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      if (!DATA) return;
      hold.setPointerCapture && hold.setPointerCapture(e.pointerId);
      hold.classList.add("pressing");
      ring.style.transition = "none";
      lit.forEach((path) => { path.style.transition = "none"; });
      t0 = performance.now();
      lastTick = 0;
      haptic("medium");
      raf = requestAnimationFrame(tick);
    });
    // pointerleave не слушаем: палец захвачен, а кнопка при нажатии уменьшается —
    // иначе прогресс сбрасывался бы, когда палец у края.
    ["pointerup", "pointercancel", "lostpointercapture"].forEach((ev) => hold.addEventListener(ev, () => {
      if (performance.now() - t0 < NEED) reset();
    }));
    hold.addEventListener("contextmenu", (e) => e.preventDefault());
  }

  // ----------------------------------------------------------- слайды
  const hero = (code, extra = "") => `<div class="hero">${extra}<div class="lottie" data-sticker="${code}"></div></div>`;
  const ic = (code) => `<span class="ico lottie" data-sticker="${code}"></span>`;
  const pill = (text) => `<div class="pill eyebrow">${text}</div>`;

  // ---- Виджеты в стиле iOS
  const RING_COLORS = [["#ff2d6f", "#ff7a59"], ["#a6ff3b", "#3ce07a"], ["#3ce8ff", "#3b9bff"]];
  let ringId = 0;
  // rings: [{p: 0..1}], до трёх колец как у «Активности»
  function ringSvg(rings, size = 100) {
    const id = ++ringId;
    const sw = 11, gap = 2;
    let defs = "", body = "";
    rings.forEach((r, i) => {
      const rad = 50 - sw / 2 - i * (sw + gap);
      const len = 2 * Math.PI * rad;
      const [c1, c2] = RING_COLORS[i % 3];
      defs += `<linearGradient id="rg${id}_${i}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${c1}"/><stop offset="1" stop-color="${c2}"/></linearGradient>`;
      body += `<circle cx="50" cy="50" r="${rad}" fill="none" stroke="${c1}" stroke-opacity=".22" stroke-width="${sw}"/>
        <circle class="rg" data-p="${Math.max(0.02, Math.min(1, r.p))}" data-len="${len}" cx="50" cy="50" r="${rad}" fill="none" stroke="url(#rg${id}_${i})" stroke-width="${sw}" stroke-linecap="round" stroke-dasharray="${len}" stroke-dashoffset="${len}"/>`;
    });
    return `<svg class="ring-svg" viewBox="0 0 100 100" width="${size}" height="${size}"><defs>${defs}</defs><g transform="rotate(-90 50 50)">${body}</g></svg>`;
  }
  const widget = (code, label, num, sub, extra = "") => `
    <div class="w ${extra}"><div class="w-top">${ic(code)}<span>${label}</span></div>
      <div class="w-num">${num}</div>${sub ? `<div class="w-sub">${sub}</div>` : ""}</div>`;
  const ringWidget = (label, rings, center, sub) => `
    <div class="w w-ring"><div class="w-top"><span>${label}</span></div>
      <div class="w-ring-body">${ringSvg(rings, 70)}<b>${center}</b></div>${sub ? `<div class="w-sub">${sub}</div>` : ""}</div>`;
  function yearProgress(year) {
    const now = Date.now();
    const a = Date.UTC(year, 0, 1), b = Date.UTC(year + 1, 0, 1);
    return Math.max(0, Math.min(1, (now - a) / (b - a)));
  }

  // Список настоящих фактов: [иконка, подпись, значение].
  const factList = (rows) => `<div class="facts">${rows.filter(Boolean).map(([code, label, value], i) => `
    <div class="fact" style="animation-delay:${360 + i * 80}ms">${ic(code)}<span>${label}</span><b>${value}</b></div>`).join("")}</div>`;
  const MONTH_LETTERS = ["Я", "Ф", "М", "А", "М", "И", "И", "А", "С", "О", "Н", "Д"];
  const PLATFORMS = { ios: "iPhone", android: "Android", android_x: "Android", tdesktop: "Telegram Desktop", macos: "Telegram для Mac", web: "Telegram Web", weba: "Telegram Web", webk: "Telegram Web", unigram: "Unigram" };
  const WEEKDAYS = ["воскресенье", "понедельник", "вторник", "среду", "четверг", "пятницу", "субботу"];

  function momentNow() {
    const now = new Date();
    const h = now.getHours();
    const time = `${String(h).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
    const part = h < 6 ? ["глубокой ночью", "025", "night", "Пока все спят, ты подводишь итоги. Настоящая ночная уточка"]
      : h < 12 ? ["с утра пораньше", "002", "ice", "Кофе, будильник и итоги года. Отличное начало дня"]
      : h < 18 ? ["посреди дня", "040", "teal", "Маленький перерыв, чтобы вспомнить весь год"]
      : ["вечером", "028", "orange", "Самое уютное время, чтобы оглянуться назад"];
    const platform = (tg && PLATFORMS[tg.platform]) || "";
    return { time, part, platform, weekday: WEEKDAYS[now.getDay()], dark: tg && tg.platform !== "unknown" ? tg.colorScheme === "dark" : null, version: tg && tg.version && tg.platform !== "unknown" ? tg.version : "" };
  }

  function planCell(d) {
    const plan = d.plan;
    if (!plan || !plan.chosen.length) return "";
    const titles = plan.chosen.map((k) => (plan.catalog.find((g) => g.key === k) || {}).title).filter(Boolean);
    return `${ic("030")}<div class="w-col"><div class="w-sub">план на ${plan.year}</div><b class="plan-line">${titles.map(esc).join(" · ")}</b></div>`;
  }

  function buildSlides(d) {
    const S = [];
    const name = esc(d.user.name);

    // 1. Привет
    const avatar = d.user.photo
      ? `<div class="avatar-wrap"><img class="avatar" src="${esc(d.user.photo)}" alt=""></div>`
      : `<div class="avatar-letter">${esc((d.user.name || "?").slice(0, 1).toUpperCase())}</div>`;
    S.push({
      theme: "festive",
      html: `${pill(`ИТОГИ ${d.year}`)}
        <div class="hero">${avatar}<div class="corner-duck"><div class="lottie" data-sticker="039"></div></div></div>
        <h2>Привет, ${name}!</h2>
        <p class="sub">Год пролетел незаметно. Давай вспомним, каким был твой ${d.year} в Telegram</p>`,
      enter: () => fx.confetti(),
    });

    // 2. Возраст аккаунта
    const a = d.age;
    S.push({
      theme: "blue",
      html: `${pill(`${ic("031")}В TELEGRAM С ${esc(a.since).toUpperCase()}`)}
        ${hero("031")}
        <h2>${esc(a.title)}</h2>
        <p class="sub">${esc(a.sub)}</p>
        <div class="value" data-count="${a.days}">0</div>
        <div class="cap">${esc(a.word)} вместе с Telegram${d.older != null ? ` · аккаунт старше, чем у ${d.older}% тех, кто смотрит итоги` : ""}</div>`,
    });

    // 2а. Тогда и сейчас — сколько людей было в Telegram, когда ты пришёл
    if (a.users_now) {
      const early = a.earlier || 0;
      S.push({
        theme: early >= 50 ? "dark" : "ice",
        html: `${pill(`${ic("004")}ДЕРЖИСЬ КРЕПЧЕ`)}
          ${hero("004")}
          <h2>${early >= 10 ? `Ты здесь раньше ${early}% людей` : "Ты на волне новичков"}</h2>
          <p class="sub">Когда ты пришёл(-ла), в Telegram было около <b>${fmt(a.users_then)} млн</b> человек. Сейчас — больше <b>${fmt(Math.floor(a.users_now / 50) * 50)} млн</b></p>
          <div class="value" data-count="${a.hours}">0</div>
          <div class="cap">${plural(a.hours, "час", "часа", "часов")} ты с Telegram. Оценка по официальным данным об аудитории</div>`,
        enter: () => haptic("heavy"),
        spark: [0.82, 0.18],
      });
    }

    // 2б. День рождения
    const bd = d.bday;
    if (bd && d.birthday) {
      S.push({
        theme: "orange",
        html: `${pill(`${ic("039")}ДЕНЬ РОЖДЕНИЯ`)}
          ${hero("024")}
          <h2>${esc(d.birthday)}</h2>
          <p class="sub">Знак — ${esc(bd.sign)}${bd.turns ? `. Скоро исполнится ${bd.turns}` : ""}</p>
          ${bd.left === 0
            ? `<div class="value gold">Сегодня!</div><div class="cap">С днём рождения от всех уточек!</div>`
            : `<div class="value" data-count="${bd.left}">0</div><div class="cap">${plural(bd.left, "день", "дня", "дней")} до праздника — друзья уже могут выбирать подарок</div>`}`,
        enter: () => { if (bd.left === 0) fx.confetti(160); },
        spark: [0.82, 0.18],
      });
    }

    // 3. Premium
    if (d.premium) {
      S.push({
        theme: "gold",
        html: `${pill(`${ic("010")}TELEGRAM PREMIUM`)}
          ${hero("010")}
          <h2>Premium-легенда</h2>
          <p class="sub">Весь год со звёздочкой у имени. Анимированные эмодзи, истории без лимитов — ты знаешь толк</p>
          <div class="value gold">Premium</div>`,
        spark: [0.82, 0.18],
      });
    } else {
      S.push({
        theme: "teal",
        html: `${pill(`${ic("010")}TELEGRAM PREMIUM`)}
          ${hero("012")}
          <h2>Без Premium — но с душой</h2>
          <p class="sub">Звёздочки у имени пока нет. Может, 2027 — тот самый год?</p>
          <div class="extra"><button class="btn btn-ghost js-premium" style="width:100%">${ic("010")}Посмотреть Premium</button></div>`,
      });
    }

    // 3б. Профиль
    const p = d.profile;
    if (p) {
      const checks = [p.photos > 0, p.bio, p.status, !!p.channel, !!d.user.username || p.usernames > 0];
      const fill = Math.round(100 * checks.filter(Boolean).length / checks.length);
      S.push({
        theme: "teal",
        html: `${pill(`${ic("027")}ТВОЙ ПРОФИЛЬ`)}
          <div class="hero hero-sm"><div class="lottie" data-sticker="027"></div></div>
          <h2>Профиль заполнен на ${fill}%</h2>
          <p class="sub">${fill >= 80 ? "Профиль мечты — всё на месте" : fill >= 50 ? "Неплохо! Пара штрихов — и будет идеально" : "Есть куда расти: фото, описание и статус"}</p>
          <div class="extra">${factList([
            p.photos != null && ["028", "Аватарок в профиле", fmt(p.photos)],
            p.usernames > 1 ? ["010", "Юзернеймов", fmt(p.usernames)] : ["010", "Юзернейм", d.user.username ? "@" + esc(d.user.username) : "нет"],
            ["021", "Эмодзи-статус", p.status ? "есть" : "нет"],
            ["036", "Описание «О себе»", p.bio ? "заполнено" : "пусто"],
            ["013", "Личный канал", p.channel ? esc(p.channel) : "нет"],
            p.music && ["039", "Музыка в профиле", "есть"],
            p.paid > 0 && ["018", "Платные сообщения", `${fmt(p.paid)} звёзд`],
          ])}</div>`,
      });
    }

    // 4. Подарки
    const g = d.gifts;
    if (g && g.now > 0) {
      const month = g.best_month ? `<div class="cap">Больше всего подарков пришло в ${esc(g.best_month.name)} — ${g.best_month.count}</div>` : "";
      S.push({
        theme: "pink",
        html: `${pill(`${ic("018")}ПОДАРКИ ГОДА`)}
          ${hero("018")}
          <h2>${g.year > 0 ? "Тебя любят!" : "Коллекция подарков"}</h2>
          <p class="sub">${g.year > 0 ? `За ${d.year} тебе подарили` : "Подарков в профиле"}</p>
          <div class="value" data-count="${g.year > 0 ? g.year : g.now}" data-suffix="${g.year > 0 ? "<small>новых</small>" : ""}">0</div>
          <div class="extra">
            <div class="compare">
              <div class="box"><b>${fmt(g.before)}</b><span>на 1 января</span></div>
              <div class="arrow">→</div>
              <div class="box hot"><b>${fmt(g.now)}</b><span>сейчас</span></div>
            </div>
          </div>
          ${month}`,
      });
      // 4б. Подарки по месяцам
      const months = g.months || [];
      const peak = Math.max(0, ...months);
      if (peak > 0) {
        const bars = months.map((n, i) => `
          <div class="bar${n === peak ? " peak" : ""}"><em>${n || ""}</em><i data-h="${Math.max(4, (n / peak) * 100)}" style="transition-delay:${i * 45}ms"></i><span>${MONTH_LETTERS[i]}</span></div>`).join("");
        S.push({
          theme: "purple",
          html: `${pill(`${ic("017")}ПОДАРКИ ПО МЕСЯЦАМ`)}
            <div class="hero hero-sm"><div class="lottie" data-sticker="017"></div></div>
            <h2>Твой год в подарках</h2>
            <p class="sub">${g.best_month ? `Пик — в ${esc(g.best_month.name)}: ${g.best_month.count} ${plural(g.best_month.count, "подарок", "подарка", "подарков")}` : "Каждый столбик — подарки за месяц"}</p>
            <div class="extra"><div class="w wide w-chart">
              <div class="w-top">${ic("018")}<span>Подарки</span><em>${d.year}</em></div>
              <div class="chart">${bars}</div>
            </div></div>`,
          enter: (el) => setTimeout(() => $$(".chart i", el).forEach((b) => (b.style.height = b.dataset.h + "%")), 60),
          leave: (el) => $$(".chart i", el).forEach((b) => (b.style.height = "0")),
        });
      }

      // 4в. Кто дарит
      if (g.top_sender || g.hidden) {
        const ts = g.top_sender;
        S.push({
          theme: "red",
          html: `${pill(`${ic("024")}КТО ТЕБЯ ЛЮБИТ`)}
            <div class="hero hero-sm"><div class="lottie" data-sticker="024"></div></div>
            <h2>${ts ? `Главный даритель — ${esc(ts.name)}` : "Тайные поклонники"}</h2>
            <p class="sub">${ts ? `${ts.count} ${plural(ts.count, "подарок", "подарка", "подарков")} от одного человека. Вот это любовь!` : "Все подарки пришли анонимно. Кто же это?.."}</p>
            <div class="extra">${factList([
              ["029", "Разных дарителей", fmt(g.senders || 0)],
              ["023", "Анонимных подарков", fmt(g.hidden || 0)],
              ["036", "С тёплой подписью", fmt(g.with_text || 0)],
            ])}</div>`,
        });
      }

      // 4г. Коллекция
      S.push({
        theme: "gold",
        html: `${pill(`${ic("030")}КОЛЛЕКЦИЯ`)}
          <div class="hero hero-sm"><div class="lottie" data-sticker="030"></div></div>
          <h2>${g.value ? "Стоимость коллекции" : "Твоя коллекция"}</h2>
          ${g.value ? `<div class="value-row"><div class="value" data-count="${g.value}">0</div><span class="ico ico-lg lottie" data-sticker="010"></span></div><div class="cap">звёзд стоят обычные подарки в профиле${g.rub ? ` — это примерно <b>${fmt(g.rub)} ₽</b>` : ""}</div>` : ""}
          <div class="extra">${factList([
            ["018", "Всего подарков", fmt(g.now)],
            g.kinds && ["027", "Разных видов", fmt(g.kinds)],
            ["010", "NFT-подарков", fmt(g.nft || 0)],
            ["014", "Лимитированных", fmt(g.limited || 0)],
            g.stars_year ? ["039", `Звёзд в подарках за ${d.year}`, fmt(g.stars_year)] : null,
          ])}</div>`,
      });

      const top = g.top;
      if (top && top.sticker) {
        S.push({
          theme: top.colors || "purple",
          html: `${pill(`${ic("039")}${top.nft ? "ЛУЧШИЙ ПОДАРОК · NFT" : "ЛУЧШИЙ ПОДАРОК"}`)}
            ${hero("g:" + top.sticker)}
            <h2>${esc(top.title)}</h2>
            <p class="sub">${top.date ? `Получен ${esc(top.date)}` : "Самый ценный подарок в твоей коллекции"}</p>
            ${top.stars ? `<div class="value-row"><div class="value gold" data-count="${top.stars}">0</div><span class="ico ico-lg lottie" data-sticker="010"></span></div>` : ""}
            ${top.rarity ? `<div class="value gold">${String(top.rarity).replace(".", ",")}%</div><div class="cap">${top.model ? `модель «${esc(top.model)}» — ` : ""}такая есть только у ${String(top.rarity).replace(".", ",")}% владельцев этого подарка</div>` : ""}
            ${g.nft ? `<div class="cap">Всего NFT-подарков: ${g.nft}</div>` : ""}`,
          spark: [0.85, 0.2],
        });
      }
    } else if (g) {
      S.push({
        theme: "pink",
        html: `${pill(`${ic("018")}ПОДАРКИ ГОДА`)}
          ${hero("006")}
          <h2>Подарков пока нет</h2>
          <p class="sub">Это нужно срочно исправить! Отправь друзьям ссылку — пусть знают, что ты ждёшь ${ic("018")}</p>`,
      });
    }

    // 5. Уровень
    const r = d.rating;
    if (r && r.level) {
      const nextLine = r.next ? `<div class="meter-row"><span>ещё ${fmt(r.left)} до уровня ${r.level + 1}</span><span>${Math.round(r.progress * 100)}%</span></div>` : `<div class="meter-row"><span>Максимум!</span><span>100%</span></div>`;
      S.push({
        theme: "purple",
        html: `${pill(`${ic("032")}УРОВЕНЬ В TELEGRAM`)}
          ${hero("032", `<div class="badge-level">${r.level}</div>`)}
          <h2>${r.was && r.was < r.level ? `Был ${r.was} — стал ${r.level}!` : `Уровень ${r.level}`}</h2>
          <p class="sub">Уровень растёт от звёзд, которые ты тратишь на подарки и платные функции</p>
          <div class="extra">
            <div class="w wide w-row">
              <div class="w-ring-body">${ringSvg([{ p: r.progress }], 78)}<b>${Math.round(r.progress * 100)}%</b></div>
              <div class="w-col">
                <div class="w-top">${ic("032")}<span>Уровень ${r.level}</span></div>
                <div class="w-num">${r.next ? fmt(r.left) : "MAX"}</div>
                <div class="w-sub">${r.next ? `очков до уровня ${r.level + 1}` : "ты на вершине"}</div>
              </div>
            </div>
          </div>`,
      });
    }

    // 6. Место
    S.push({
      theme: "green",
      html: `${pill(`${ic("029")}ТЫ НЕ ОДИН`)}
        ${hero("029")}
        <h2>Ты в компании!</h2>
        <p class="sub">Итоги ${d.year} уже смотрят тысячи людей. Твой номер в этой очереди</p>
        <div class="value" data-count="${d.rank}" data-suffix="">0</div>
        <div class="cap">${d.invited ? `По твоей ссылке пришло ${d.invited} ${plural(d.invited, "друг", "друга", "друзей")} ${ic("024")}` : "Позови друзей — пусть тоже узнают свои итоги"}</div>`,
    });

    // 6б. Прямо сейчас
    const m = momentNow();
    S.push({
      theme: m.part[2],
      html: `${pill(`${ic("027")}ПРЯМО СЕЙЧАС`)}
        ${hero(m.part[1])}
        <h2>Итоги — ${m.part[0]}</h2>
        <p class="sub">${m.part[3]}</p>
        <div class="extra">${factList([
          ["031", "Открыл(-а) итоги", `в ${m.weekday}, ${m.time}`],
          m.platform && ["032", "Устройство", m.platform],
          m.dark != null && ["025", "Тема Telegram", m.dark ? "тёмная" : "светлая"],
          m.version && ["030", "Версия мини-аппов", m.version],
        ])}</div>`,
    });

    // 6в. Шуточная статистика «по версии уточек» — по слайду на цифру
    const FUN = {
      ignore: { theme: "purple", title: "Мастер игнора", text: (n) => `Столько ${plural(n, "день", "дня", "дней")} ты не отвечал(-а) на сообщения. Кто-то до сих пор ждёт` },
      fast: { theme: "red", title: "Скорость света", text: () => "Твой самый быстрый ответ. Собеседник даже не успел заблокировать экран" },
      ahah: { theme: "orange", title: "Главный хохотун", text: () => "Столько раз ты написал(-а) «ахахах». Смешно было не всегда" },
      night: { theme: "night", title: "Ночная смена", text: () => "Сообщений после 3 ночи. Сон — для слабых" },
      voice: { theme: "teal", title: "Подкастер года", text: (n) => `Минут голосовых — это ${Math.max(1, Math.round(n / 60))} ч. Целый сезон подкаста` },
      read: { theme: "blue", title: "Прочитал(-а) и забыл(-а)", text: () => "Две синие галочки — твоя суперсила. Ответ уже в пути. Наверное" },
    };
    (d.fun || []).forEach((f) => {
      const m = FUN[f.key];
      if (!m) return;
      const num = typeof f.value === "number";
      S.push({
        theme: m.theme,
        html: `${pill(`${ic("037")}ПО ВЕРСИИ УТОЧЕК`)}
          ${hero(f.sticker)}
          <h2>${esc(m.title)}</h2>
          ${num
            ? `<div class="value" data-count="${f.value}" data-suffix="<small>${esc(f.unit)}</small>">0</div>`
            : `<div class="value gold">${esc(f.value)}<small>${esc(f.unit)}</small></div>`}
          <p class="sub">${esc(m.text(num ? f.value : 0))}</p>`,
        spark: f.key === "fast" ? [0.82, 0.2] : null,
        enter: f.key === "ahah" ? () => fx.confetti(90) : null,
      });
    });

    // 7. Номинации
    d.nominations.forEach((n, i) => {
      S.push({
        theme: n.theme,
        html: `${pill(`${ic("028")}НОМИНАЦИЯ ГОДА · ${i + 1}/${d.nominations.length}`)}
          ${hero(n.sticker, '<div class="trophy-ring"></div>')}
          <div class="cap" style="margin-top:0">Уточки посовещались и присудили тебе</div>
          <h2 class="gold" style="font-size:clamp(28px,8.6vw,40px)">${esc(n.title)}</h2>
          <p class="sub">${esc(n.text)}</p>`,
        spark: [0.8, 0.15],
      });
    });

    // 8. Предсказание
    S.push({
      theme: "night",
      html: `${pill(`${ic("025")}ПРЕДСКАЗАНИЕ НА ${d.year + 1}`)}
        ${hero("005")}
        <h2>Что тебя ждёт?</h2>
        <p class="sub" style="margin-bottom:16px">Уточка заглянула в будущее. Нажми на карточку</p>
        <div class="oracle js-oracle"><div class="oracle-in">
          <div class="oracle-face oracle-front">${ic("010")}Открыть предсказание${ic("010")}</div>
          <div class="oracle-face oracle-back">${esc(d.prediction)}</div>
        </div></div>`,
      leave: (el) => { const o = $(".js-oracle", el); if (o) o.classList.remove("open"); },
    });

    // 8б. Прошлогодний план (появится в итогах следующего года)
    const plan = d.plan;
    const byKey = {};
    if (plan) plan.catalog.forEach((goal) => (byKey[goal.key] = goal));
    if (plan && plan.last && plan.last.length) {
      S.push({
        theme: "green",
        html: `${pill(`${ic("031")}ТВОЙ ПЛАН НА ${d.year}`)}
          ${hero("003")}
          <h2>Год назад ты загадал(-а)</h2>
          <p class="sub">Вспомни, что из этого получилось</p>
          <div class="extra">${factList(plan.last.filter((k) => byKey[k]).map((k) => [byKey[k].sticker, esc(byKey[k].title), ""]))}</div>`,
      });
    }

    // 8в. План на следующий год
    if (plan) {
      const chips = plan.catalog.map((goal) => `
        <button class="goal js-goal${plan.chosen.includes(goal.key) ? " on" : ""}" data-k="${goal.key}">${ic(goal.sticker)}<span>${esc(goal.title)}</span></button>`).join("");
      S.push({
        theme: "festive",
        html: `${pill(`${ic("030")}ПЛАН НА ${plan.year}`)}
          <h2 style="margin-top:12px">Что берём в ${plan.year}?</h2>
          <p class="sub">Выбери до ${plan.max} целей. Уточки запомнят и через год спросят, получилось ли</p>
          <div class="extra">
            <div class="goals">${chips}</div>
            <button class="btn btn-white js-plan-save" style="width:100%;margin-top:12px">${ic("003")}<span class="js-plan-label">${plan.chosen.length ? "План сохранён" : "Сохранить план"}</span></button>
          </div>`,
      });
    }

    // 9. Финал
    const yp = yearProgress(d.year);
    const cells = [
      widget("031", "Telegram", fmt(a.days), `${esc(a.word)} вместе`),
      g && g.now
        ? widget("018", "Подарки", fmt(g.now), g.year > 0 ? `+${fmt(g.year)} за ${d.year}` : plural(g.now, "подарок", "подарка", "подарков"))
        : widget("029", "Итоги", `№${fmt(d.rank)}`, "в очереди"),
      r && r.level
        ? ringWidget(`Уровень ${r.level}`, [{ p: r.progress }, { p: Math.min(1, (d.invited || 0) / 5) }], r.level, `${d.invited || 0} ${plural(d.invited || 0, "друг", "друга", "друзей")} позвал(-а)`)
        : widget("029", "Друзья", fmt(d.invited || 0), "позвал(-а)"),
      ringWidget(`${d.year}`, [{ p: yp }], `${Math.round(yp * 100)}%`, "года позади"),
      `<div class="w wide w-nom">${ic(d.nominations[0].sticker)}<div class="w-col"><div class="w-sub">главная номинация</div><b class="gold">${esc(d.nominations[0].title)}</b></div></div>`,
      `<div class="w wide js-plan-cell"${plan && plan.chosen.length ? "" : " hidden"}>${planCell(d)}</div>`,
    ].join("");
    S.push({
      theme: "festive",
      final: true,
      html: `${pill(`С НАСТУПАЮЩИМ ${d.year + 1}!`)}
        <div class="final-head"><div class="final-duck"><div class="lottie" data-sticker="039"></div></div><h2>Вот это был год!</h2></div>
        <div class="extra" style="margin-top:8px">
          <div class="widgets">${cells}</div>
          <div class="final-actions">
            <button class="btn btn-gold js-story">${ic("030")}Выложить в историю</button>
            <button class="btn btn-white js-send">${ic("036")}Отправить друзьям</button>
            <button class="btn btn-ghost js-invite">${ic("029")}Пригласить друзей</button>
          </div>
        </div>`,
      enter: () => { fx.confetti(180); haptic("success"); },
      spark: [0.85, 0.2],
    });
    return S;
  }

  let SLIDES = [];
  let idx = 0;
  function startStory() {
    SLIDES = buildSlides(DATA);
    const box = $("#slides");
    box.innerHTML = SLIDES.map((s, i) => `<div class="slide" data-i="${i}">${s.html}</div>`).join("");
    $("#bars").innerHTML = SLIDES.map(() => "<i><b></b></i>").join("");
    bindSlideButtons();
    show("s-story");
    idx = -1;
    go(0);
  }

  function go(i) {
    if (i < 0 || i >= SLIDES.length || i === idx) return;
    const els = $$(".slide");
    const back = i < idx;
    const prev = els[idx];
    if (prev) {
      prev.classList.remove("active");
      prev.classList.toggle("left", !back);
      const ps = SLIDES[idx];
      if (ps && ps.leave) ps.leave(prev);
      setTimeout(() => { if (!prev.classList.contains("active")) unmountIn(prev); }, 520);
    }
    fx.stopAll();
    idx = i;
    const s = SLIDES[i];
    const el = els[i];
    el.classList.remove("left");
    // Перезапуск CSS-анимаций при повторном показе.
    void el.offsetWidth;
    el.classList.add("active");
    setTheme(s.theme);
    mountIn(el);
    if (els[i + 1]) $$(".lottie", els[i + 1]).forEach((l) => loadJson(stickerUrl(l.dataset.sticker)));
    $$(".value[data-count]", el).forEach(countUp);
    $$(".rg", el).forEach((c, k) => {
      c.style.transition = "none";
      c.style.strokeDashoffset = c.dataset.len;
      void c.getBoundingClientRect();
      c.style.transition = `stroke-dashoffset 1.4s cubic-bezier(.2,.8,.2,1) ${0.35 + k * 0.12}s`;
      c.style.strokeDashoffset = c.dataset.len * (1 - c.dataset.p);
    });
    if (s.enter) s.enter(el);
    if (s.spark) {
      const h = $(".hero", el);
      if (h) setTimeout(() => { if (idx === i) fx.sparkler(h, s.spark[0], s.spark[1], 99999, 2); }, 500);
    }
    $$("#bars i").forEach((b, k) => b.classList.toggle("done", k <= i));
    const nav = $("#nav");
    nav.classList.toggle("final", !!s.final);
    $("#nextBtn span").textContent = s.final ? "Смотреть заново" : i === SLIDES.length - 2 ? "К итогам" : "Продолжить";
    $("#prevBtn").disabled = i === 0;
    haptic(back ? "soft" : "light");
  }

  function next() {
    if (SLIDES[idx] && SLIDES[idx].final) { go(0); return; }
    go(idx + 1);
  }

  function setupGestures() {
    const box = $("#slides");
    let x0 = 0, y0 = 0, tStart = 0;
    box.addEventListener("pointerdown", (e) => { x0 = e.clientX; y0 = e.clientY; tStart = Date.now(); });
    box.addEventListener("pointerup", (e) => {
      if (e.target.closest("button, a, .js-oracle")) return;
      const dx = e.clientX - x0, dy = e.clientY - y0;
      if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) { dx < 0 ? next() : go(idx - 1); return; }
      if (Math.abs(dx) < 10 && Math.abs(dy) < 10 && Date.now() - tStart < 400) {
        if (SLIDES[idx] && SLIDES[idx].final) return;
        e.clientX < innerWidth * 0.3 ? go(idx - 1) : next();
      }
    });
    $("#nextBtn").addEventListener("click", next);
    $("#prevBtn").addEventListener("click", () => go(idx - 1));
    addEventListener("keydown", (e) => {
      if (!$("#s-story").classList.contains("active")) return;
      if (e.key === "ArrowRight" || e.key === " ") next();
      if (e.key === "ArrowLeft") go(idx - 1);
    });
  }

  // ----------------------------------------------------------- поделиться
  function bindSlideButtons() {
    const box = $("#slides");
    box.addEventListener("click", async (e) => {
      const b = e.target.closest("button, .js-oracle");
      if (!b) return;
      if (b.classList.contains("js-oracle")) {
        if (!b.classList.contains("open")) {
          b.classList.add("open");
          haptic("success");
          const r = b.getBoundingClientRect();
          fx.burst(r.left + r.width / 2, r.top + r.height / 2, 60);
        }
        return;
      }
      if (b.classList.contains("js-goal")) { toggleGoal(b); return; }
      if (b.classList.contains("js-plan-save")) { await savePlan(b); return; }
      if (b.classList.contains("js-premium")) { openLink("https://t.me/premium"); return; }
      if (b.classList.contains("js-invite")) { invite(); return; }
      if (b.classList.contains("js-send")) { await sendToFriends(b); return; }
      if (b.classList.contains("js-story")) { await toStory(b); }
    });
  }

  function toggleGoal(b) {
    const plan = DATA.plan;
    const k = b.dataset.k;
    const on = plan.chosen.includes(k);
    if (!on && plan.chosen.length >= plan.max) {
      haptic("error");
      b.classList.remove("nope"); void b.offsetWidth; b.classList.add("nope");
      toast(`Можно выбрать ${plan.max} цели`);
      return;
    }
    plan.chosen = on ? plan.chosen.filter((x) => x !== k) : [...plan.chosen, k];
    b.classList.toggle("on", !on);
    haptic(on ? "light" : "medium");
    if (!on) { const r = b.getBoundingClientRect(); fx.burst(r.left + r.width / 2, r.top + r.height / 2, 18); }
    const label = $(".js-plan-label");
    if (label) label.textContent = "Сохранить план";
  }

  async function savePlan(b) {
    const plan = DATA.plan;
    if (!plan.chosen.length) { toast("Выбери хотя бы одну цель"); haptic("error"); return; }
    b.disabled = true;
    try {
      if (!DEMO) await api("/api/plan", { goals: plan.chosen });
      haptic("success");
      const r = b.getBoundingClientRect();
      fx.burst(r.left + r.width / 2, r.top, 70);
      toast(`${ic("003")}План на ${plan.year} сохранён!`);
      $(".js-plan-label").textContent = "План сохранён";
      const cell = $(".js-plan-cell");
      if (cell) { cell.innerHTML = planCell(DATA); cell.hidden = false; mountIn(cell); }
    } catch (_) {
      toast("Не получилось, попробуй ещё раз");
    } finally {
      b.disabled = false;
    }
  }

  function invite() {
    haptic("light");
    const link = (DATA.links && DATA.links.invite) || "";
    const text = `Посмотри свои итоги ${DATA.year} в Telegram! 🦆`;
    openLink(`https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(text)}`);
  }

  async function sendToFriends(btn) {
    haptic("light");
    if (DEMO) { toast("В Telegram тут откроется выбор чата"); return; }
    if (!can("8.0")) { invite(); return; }
    btn.disabled = true;
    const old = btn.innerHTML;
    btn.textContent = "Готовим карточку…";
    try {
      const r = await api("/api/share");
      tg.shareMessage(r.id, (ok) => { if (ok) { haptic("success"); toast(`${ic("039")}Отправлено!`); } });
    } catch (e) {
      if (e.code === "subscribe") { relock(); return; }
      toast("Не получилось, попробуй ещё раз");
    } finally {
      btn.disabled = false;
      btn.innerHTML = old;
      mountIn(btn);
    }
  }

  // Отписался от спонсора или не запускал бота — заново через проверку (ОП).
  function relock() {
    toast("Подпишись на каналы спонсоров, чтобы делиться итогами");
    fx.stopAll();
    show("s-loading");
    boot2();
  }

  async function toStory(btn) {
    haptic("light");
    if (DEMO) { toast("В Telegram тут откроется редактор истории"); return; }
    if (!can("7.8")) { toast("Обнови Telegram, чтобы выкладывать истории"); return; }
    btn.disabled = true;
    const old = btn.innerHTML;
    btn.textContent = "Готовим видео…";
    try {
      const r = await api("/api/story");
      const opts = { text: `Мои итоги ${DATA.year} в Telegram 🦆` };
      const premium = tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.is_premium;
      if (premium) opts.widget_link = { url: r.link, name: "Узнать свои итоги" };
      tg.shareToStory(r.url, opts);
    } catch (e) {
      if (e.code === "subscribe") { relock(); return; }
      toast("Не получилось, попробуй ещё раз");
    } finally {
      btn.disabled = false;
      btn.innerHTML = old;
      mountIn(btn);
    }
  }

  // ----------------------------------------------------------- старт
  function demoData() {
    return {
      year: YEAR,
      user: { id: 1, name: "Ваня", username: "vanya", photo: null },
      age: { days: 1347, hours: 32328, users_then: 780, users_now: 1120, earlier: 30, since: "мар 2023", year: 2023, title: "Бывалый", sub: "Ты видел(-а) Telegram без историй и подарков", word: "дней" },
      premium: true,
      gifts: { now: 17, before: 3, year: 14, stars_year: 1250, nft: 2, top: null, best_month: { name: "феврале", count: 6 },
        months: [1, 6, 0, 1, 2, 0, 1, 0, 1, 2, 0], value: 2150, limited: 4, with_text: 9, hidden: 2, kinds: 11, senders: 7,
        top_sender: { name: "Маша", count: 5 }, rub: 3870 },
      bday: { sign: "Дева", left: 308, turns: 24 },
      profile: { usernames: 1, bio: true, status: true, channel: "Ванин блог", paid: 0, colors: true, music: false, photos: 12 },
      older: 64,
      fun: [
        { key: "ignore", sticker: "023", label: "Игнорил(-а) сообщения", value: 87, unit: "дн." },
        { key: "fast", sticker: "030", label: "Самый быстрый ответ", value: "0,8", unit: "сек" },
        { key: "ahah", sticker: "037", label: "Написал(-а) «ахахах»", value: 3120, unit: "раз" },
        { key: "night", sticker: "025", label: "Сообщений после 3:00", value: 412, unit: "шт." },
        { key: "voice", sticker: "036", label: "Голосовых наговорил(-а)", value: 640, unit: "мин" },
        { key: "read", sticker: "027", label: "Прочитал(-а) и не ответил(-а)", value: 1480, unit: "раз" },
      ],
      plan: { year: YEAR + 1, max: 3, chosen: [], last: [], catalog: [
        { key: "travel", title: "Больше путешествовать", sticker: "040" }, { key: "sport", title: "Спорт и здоровье", sticker: "030" },
        { key: "sleep", title: "Высыпаться", sticker: "025" }, { key: "money", title: "Накопить денег", sticker: "018" },
        { key: "love", title: "Найти любовь", sticker: "024" }, { key: "friends", title: "Чаще видеть друзей", sticker: "029" },
        { key: "learn", title: "Выучить новое", sticker: "017" }, { key: "job", title: "Рост в работе", sticker: "032" },
        { key: "project", title: "Свой проект", sticker: "013" }, { key: "calm", title: "Меньше стресса", sticker: "011" },
        { key: "phone", title: "Меньше телефона", sticker: "027" }, { key: "read", title: "Читать книги", sticker: "012" },
      ] },
      rating: { level: 7, points: 1840, next: 2500, left: 660, progress: 0.62 },
      birthday: "13 сентября",
      rank: 4812, invited: 3,
      nominations: [
        { key: "night", title: "Ночная уточка", text: "Лучшие мысли приходят к тебе в 3 часа ночи — и сразу в чат", sticker: "025", theme: "night" },
        { key: "speed", title: "Скорость света", text: "Отвечаешь раньше, чем тебе дописали", sticker: "030", theme: "red" },
        { key: "zen", title: "Мастер дзена", text: "Сто непрочитанных? Ты спокоен как уточка в пруду", sticker: "011", theme: "green" },
      ],
      prediction: "В 2027 тебе подарят подарок, о котором ты даже не мечтал(-а)",
      links: { invite: "https://t.me/", channel: "", bot: "https://t.me/" },
    };
  }

  async function boot() {
    setupTelegram();
    setTheme("festive");
    startSnow();
    setupHold();
    setupGestures();
    $$(".year").forEach((e) => (e.textContent = YEAR));
    $("#checkBtn").addEventListener("click", check);
    $("#retryBtn").addEventListener("click", () => { show("s-loading"); boot2(); });
    show("s-loading");
    boot2();
  }

  async function boot2() {
    if (DEMO) {
      const q = new URLSearchParams(location.search);
      if (q.has("shot")) document.documentElement.classList.add("shot");
      const screen = q.get("screen");
      if (screen === "subscribe") {
        renderSponsors([{ title: "Чё там?", link: "#" }, { title: "Уточки News", link: "#" }, { title: "Ещё один канал", link: "#" }]);
        setTheme("blue"); show("s-subscribe"); return;
      }
      setTimeout(() => {
        ready(demoData());
        if (q.has("slide")) { startStory(); go(Number(q.get("slide")) || 0); }
      }, q.has("slide") ? 0 : 900);
      return;
    }
    if (!tg || !tg.initData) {
      $("#errorText").textContent = "Открой итоги через бота в Telegram";
      show("s-error");
      return;
    }
    try {
      const started = Date.now();
      const r = await api("/api/start");
      // Даём уточке долететь — мгновенное мигание экрана загрузки выглядит как сбой.
      await new Promise((ok) => setTimeout(ok, Math.max(0, 900 - (Date.now() - started))));
      if (r.state === "start") {
        // Человек не нажимал /start — ведём в бота и закрываем мини-апп.
        setTheme("blue");
        $("#startBtn").onclick = () => {
          haptic("light");
          try { tg.openTelegramLink(r.link); } catch (_) { openLink(r.link); }
          setTimeout(() => { try { tg.close(); } catch (_) { /* ок */ } }, 300);
        };
        show("s-start");
      } else if (r.state === "subscribe") {
        setTheme("blue");
        renderSponsors(r.sponsors);
        show("s-subscribe");
      } else {
        ready(r.data);
      }
    } catch (e) {
      $("#errorText").textContent = e.code === "bad_init" ? "Сессия устарела — закрой и открой итоги заново" : "Попробуй открыть итоги ещё раз";
      show("s-error");
    }
  }

  boot();
})();

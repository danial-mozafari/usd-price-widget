// ---------------------------------------------------------------------------
// تنظیمات
// ---------------------------------------------------------------------------

// آدرس بک‌اند روی Render
const API_BASE = "https://usd-price-widget-eu.onrender.com";

const numberFmt = new Intl.NumberFormat("en-US");
const timeFmt = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

function formatPrice(n) {
  if (n === null || n === undefined || n === 0) return "--";
  return numberFmt.format(Math.round(n));
}

function formatSigned(n) {
  if (n === null || n === undefined) return "--";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n}%`;
}

// ---------------------------------------------------------------------------
// دارک / لایت مود
// ---------------------------------------------------------------------------

const themeToggleBtn = document.getElementById("themeToggle");
const savedTheme = localStorage.getItem("usd_widget_theme") || "dark";

function applyTheme(theme) {
  if (theme === "light") {
    document.documentElement.setAttribute("data-theme", "light");
    themeToggleBtn.textContent = "☀️";
  } else {
    document.documentElement.removeAttribute("data-theme");
    themeToggleBtn.textContent = "🌙";
  }

  localStorage.setItem("usd_widget_theme", theme);
}

applyTheme(savedTheme);

themeToggleBtn.addEventListener("click", () => {
  const current = localStorage.getItem("usd_widget_theme") || "dark";
  applyTheme(current === "dark" ? "light" : "dark");
  if (typeof drawChart === "function" && lastHistory.length) {
    drawChart(lastHistory);
  }
});

// ---------------------------------------------------------------------------
// نمودار حرفه‌ای (کاملاً با Canvas خام، بدون هیچ کتابخونه‌ی بیرونی)
// ---------------------------------------------------------------------------
// چرا بدون کتابخونه: تجربه نشون داد CDN های خارجی (jsdelivr, cdnjs) توی
// ایران فیلترن. این نمودار قیمت + ساعت رو نشون میده، رنگش با دارک/لایت
// مود هماهنگه، و با لمس/کلیک قیمت دقیق هر نقطه رو نشون میده.

const chartCanvas = document.getElementById("priceChart");
const chartCtx = chartCanvas.getContext("2d");
const chartTooltip = document.getElementById("chartTooltip");
const chartRangeChangeEl = document.getElementById("chartRangeChange");

let lastHistory = [];
let hoverIndex = null;

function cssVar(name) {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
}

function drawChart(history) {
  if (!history || history.length === 0) return;

  const dpr = window.devicePixelRatio || 1;
  const displayWidth = chartCanvas.clientWidth;
  const displayHeight = chartCanvas.clientHeight || 150;

  if (
    chartCanvas.width !== Math.round(displayWidth * dpr) ||
    chartCanvas.height !== Math.round(displayHeight * dpr)
  ) {
    chartCanvas.width = Math.round(displayWidth * dpr);
    chartCanvas.height = Math.round(displayHeight * dpr);
  }

  chartCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  chartCtx.clearRect(0, 0, displayWidth, displayHeight);

  const recent = history.slice(-40);
  const prices = recent.map((h) => h.p);
  const minPrice = Math.min(...prices);
  const maxPrice = Math.max(...prices);
  const range = maxPrice - minPrice || 1;

  const padLeft = 52;
  const padRight = 8;
  const padTop = 10;
  const padBottom = 20;

  const plotWidth = displayWidth - padLeft - padRight;
  const plotHeight = displayHeight - padTop - padBottom;

  const stepX =
    recent.length > 1 ? plotWidth / (recent.length - 1) : plotWidth;

  const points = prices.map((p, i) => ({
    x: padLeft + i * stepX,
    y: padTop + plotHeight - ((p - minPrice) / range) * plotHeight,
  }));

  const textDim = cssVar("--text-dim") || "#94a3b8";
  const upColor = cssVar("--accent-up") || "#34d399";
  const downColor = cssVar("--accent-down") || "#f87171";

  // روند این بازه: اگه آخرین قیمت از اولین قیمتِ همین بازه بیشتر باشه، سبز؛
  // وگرنه قرمز - دقیقاً مثل اپ‌های صرافی حرفه‌ای
  const trendUp = prices[prices.length - 1] >= prices[0];
  const trendColor = trendUp ? upColor : downColor;

  const rangeChangePct =
    prices[0] !== 0
      ? (((prices[prices.length - 1] - prices[0]) / prices[0]) * 100).toFixed(2)
      : "0.00";

  if (chartRangeChangeEl) {
    chartRangeChangeEl.textContent = `${trendUp ? "+" : ""}${rangeChangePct}%`;
    chartRangeChangeEl.classList.remove("up", "down");
    chartRangeChangeEl.classList.add(trendUp ? "up" : "down");
  }

  // --- خطوط راهنمای افقی نقطه‌چین + برچسب قیمت (۴ سطح) ---
  const gridLevels = [
    maxPrice,
    minPrice + (range * 2) / 3,
    minPrice + range / 3,
    minPrice,
  ];

  chartCtx.font = "9px sans-serif";
  chartCtx.fillStyle = textDim;
  chartCtx.textBaseline = "middle";
  chartCtx.textAlign = "left";
  chartCtx.setLineDash([3, 4]);

  gridLevels.forEach((level) => {
    const y = padTop + plotHeight - ((level - minPrice) / range) * plotHeight;

    chartCtx.beginPath();
    chartCtx.moveTo(padLeft, y);
    chartCtx.lineTo(displayWidth - padRight, y);
    chartCtx.strokeStyle = "rgba(148, 163, 184, 0.18)";
    chartCtx.lineWidth = 1;
    chartCtx.stroke();

    chartCtx.fillText(numberFmt.format(Math.round(level)), 0, y);
  });

  chartCtx.setLineDash([]);

  // --- برچسب‌های زمان روی محور افقی (اول، وسط، آخر) ---
  chartCtx.textAlign = "center";
  chartCtx.textBaseline = "top";

  const timeIndexes = [0, Math.floor(recent.length / 2), recent.length - 1];
  timeIndexes.forEach((i) => {
    if (!recent[i]) return;
    const label = timeFmt.format(new Date(recent[i].t));
    chartCtx.fillText(label, points[i].x, displayHeight - padBottom + 4);
  });

  // --- ناحیه‌ی زیر خط (گرادیانی، هم‌رنگ با روند) ---
  const gradient = chartCtx.createLinearGradient(0, padTop, 0, displayHeight - padBottom);
  const glowRgb = trendUp ? "52, 211, 153" : "248, 113, 113";
  gradient.addColorStop(0, `rgba(${glowRgb}, 0.32)`);
  gradient.addColorStop(1, `rgba(${glowRgb}, 0)`);

  function drawSmoothPath() {
    chartCtx.beginPath();
    chartCtx.moveTo(points[0].x, points[0].y);
    for (let i = 1; i < points.length; i++) {
      const prev = points[i - 1];
      const curr = points[i];
      const midX = (prev.x + curr.x) / 2;
      chartCtx.quadraticCurveTo(prev.x, prev.y, midX, (prev.y + curr.y) / 2);
    }
    chartCtx.lineTo(points[points.length - 1].x, points[points.length - 1].y);
  }

  drawSmoothPath();
  chartCtx.lineTo(points[points.length - 1].x, displayHeight - padBottom);
  chartCtx.lineTo(points[0].x, displayHeight - padBottom);
  chartCtx.closePath();
  chartCtx.fillStyle = gradient;
  chartCtx.fill();

  drawSmoothPath();
  chartCtx.strokeStyle = trendColor;
  chartCtx.lineWidth = 2.5;
  chartCtx.lineJoin = "round";
  chartCtx.lineCap = "round";
  chartCtx.stroke();

  // --- نقطه‌ی برجسته برای آخرین قیمت ---
  const lastPoint = points[points.length - 1];
  chartCtx.beginPath();
  chartCtx.arc(lastPoint.x, lastPoint.y, 4.5, 0, Math.PI * 2);
  chartCtx.fillStyle = trendColor;
  chartCtx.shadowColor = trendColor;
  chartCtx.shadowBlur = 10;
  chartCtx.fill();
  chartCtx.shadowBlur = 0;

  chartCtx.beginPath();
  chartCtx.arc(lastPoint.x, lastPoint.y, 2, 0, Math.PI * 2);
  chartCtx.fillStyle = "#ffffff";
  chartCtx.fill();

  // --- نقطه و خط راهنما، وقتی کاربر لمس/کلیک کرده ---
  if (hoverIndex !== null && points[hoverIndex]) {
    const hp = points[hoverIndex];

    chartCtx.beginPath();
    chartCtx.moveTo(hp.x, padTop);
    chartCtx.lineTo(hp.x, displayHeight - padBottom);
    chartCtx.strokeStyle = "rgba(148, 163, 184, 0.4)";
    chartCtx.lineWidth = 1;
    chartCtx.stroke();

    chartCtx.beginPath();
    chartCtx.arc(hp.x, hp.y, 4, 0, Math.PI * 2);
    chartCtx.fillStyle = "#ffffff";
    chartCtx.fill();
    chartCtx.strokeStyle = accent;
    chartCtx.lineWidth = 2;
    chartCtx.stroke();
  }
}

function updateChartTooltip() {
  if (hoverIndex === null || !lastHistory.length) {
    chartTooltip.classList.remove("show");
    return;
  }

  const recent = lastHistory.slice(-40);
  const point = recent[hoverIndex];
  if (!point) return;

  const timeLabel = timeFmt.format(new Date(point.t));
  chartTooltip.textContent = `${numberFmt.format(Math.round(point.p))} تومان - ${timeLabel}`;
  chartTooltip.classList.add("show");
}

function handleChartPointer(clientX) {
  const rect = chartCanvas.getBoundingClientRect();
  const x = clientX - rect.left;

  const recent = lastHistory.slice(-40);
  if (!recent.length) return;

  const padLeft = 52;
  const padRight = 8;
  const plotWidth = rect.width - padLeft - padRight;
  const stepX = recent.length > 1 ? plotWidth / (recent.length - 1) : plotWidth;

  let index = Math.round((x - padLeft) / stepX);
  index = Math.max(0, Math.min(recent.length - 1, index));

  hoverIndex = index;
  drawChart(lastHistory);
  updateChartTooltip();
}

chartCanvas.addEventListener("mousemove", (e) => handleChartPointer(e.clientX));
chartCanvas.addEventListener("mouseleave", () => {
  hoverIndex = null;
  drawChart(lastHistory);
  updateChartTooltip();
});
chartCanvas.addEventListener("touchstart", (e) => handleChartPointer(e.touches[0].clientX), { passive: true });
chartCanvas.addEventListener("touchmove", (e) => handleChartPointer(e.touches[0].clientX), { passive: true });
chartCanvas.addEventListener("touchend", () => {
  hoverIndex = null;
  drawChart(lastHistory);
  updateChartTooltip();
});

window.addEventListener("resize", () => drawChart(lastHistory));

function updateChart(history) {
  if (!history || history.length === 0) return;

  try {
    lastHistory = history;
    drawChart(history);
  } catch (e) {
    console.warn("رسم نمودار با خطا مواجه شد:", e);
  }
}

// ---------------------------------------------------------------------------
// هشدارهای قیمتی
// ---------------------------------------------------------------------------

const ALERTS_KEY = "usd_widget_alerts";

const alertListEl = document.getElementById("alertList");
const alertDirectionEl = document.getElementById("alertDirection");
const alertValueEl = document.getElementById("alertValue");
const alertAddBtn = document.getElementById("alertAdd");

function loadAlerts() {
  try {
    return JSON.parse(localStorage.getItem(ALERTS_KEY)) || [];
  } catch {
    return [];
  }
}

function saveAlerts(alerts) {
  localStorage.setItem(ALERTS_KEY, JSON.stringify(alerts));
}

function renderAlerts() {
  const alerts = loadAlerts();

  alertListEl.innerHTML = "";

  if (alerts.length === 0) {
    alertListEl.innerHTML =
      '<span class="alert-empty">هنوز هشداری تنظیم نکردی</span>';
    return;
  }

  alerts.forEach((a) => {
    const chip = document.createElement("div");
    chip.className = "alert-chip";

    const arrow = a.direction === "gte" ? "≥" : "≤";
    const fired = a.firedAt ? " ✅" : "";

    chip.innerHTML =
      `<span>${arrow} ${numberFmt.format(a.value)}${fired}</span>`;

    const removeBtn = document.createElement("button");
    removeBtn.textContent = "×";

    removeBtn.onclick = () => {
      const remaining = loadAlerts().filter((x) => x.id !== a.id);

      saveAlerts(remaining);
      renderAlerts();
      setupPushAndSync(remaining);
    };

    chip.appendChild(removeBtn);
    alertListEl.appendChild(chip);
  });
}

alertAddBtn.addEventListener("click", () => {
  const value = parseFloat(alertValueEl.value);

  if (!value || value <= 0) return;

  const alerts = loadAlerts();

  alerts.push({
    id: Date.now().toString(),
    direction: alertDirectionEl.value,
    value,
    firedAt: null,
  });

  saveAlerts(alerts);

  alertValueEl.value = "";

  renderAlerts();
  setupPushAndSync(alerts);
});

function checkAlerts(currentPrice) {
  const alerts = loadAlerts();
  let changed = false;

  alerts.forEach((a) => {
    if (a.firedAt) return;

    const hit =
      a.direction === "gte"
        ? currentPrice >= a.value
        : currentPrice <= a.value;

    if (hit) {
      a.firedAt = new Date().toISOString();
      changed = true;

      const arrow =
        a.direction === "gte"
          ? "به یا بالاتر از"
          : "به یا پایین‌تر از";

      showToast(
        `💵 دلار رسید ${arrow} ${numberFmt.format(a.value)} تومان`
      );

      playBeep();

      if (navigator.vibrate) {
        navigator.vibrate([120, 60, 120]);
      }

      if (
        "Notification" in window &&
        Notification.permission === "granted"
      ) {
        try {
          new Notification("قیمت دلار", {
            body: `دلار به ${numberFmt.format(currentPrice)} تومان رسید`,
            icon: "/icon-192.png",
          });
        } catch (e) {}
      }
    }
  });

  if (changed) {
    saveAlerts(alerts);
    renderAlerts();
  }
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------

const toastEl = document.getElementById("toast");
let toastTimer = null;

function showToast(message) {
  toastEl.textContent = message;
  toastEl.classList.add("show");

  clearTimeout(toastTimer);

  toastTimer = setTimeout(
    () => toastEl.classList.remove("show"),
    6000
  );
}

function playBeep() {
  try {
    const AudioCtx =
      window.AudioContext || window.webkitAudioContext;

    const ctx = new AudioCtx();

    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.type = "sine";
    osc.frequency.value = 880;

    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(
      0.001,
      ctx.currentTime + 0.4
    );

    osc.connect(gain);
    gain.connect(ctx.destination);

    osc.start();
    osc.stop(ctx.currentTime + 0.4);
  } catch (e) {}
}

renderAlerts();

// ---------------------------------------------------------------------------
// Web Push
// ---------------------------------------------------------------------------

function urlBase64ToUint8Array(base64String) {
  const padding =
    "=".repeat((4 - (base64String.length % 4)) % 4);

  const base64 =
    (base64String + padding)
      .replace(/-/g, "+")
      .replace(/_/g, "/");

  const rawData = atob(base64);

  return Uint8Array.from(
    [...rawData].map((c) => c.charCodeAt(0))
  );
}

async function setupPushAndSync(alerts) {
  if (
    !("serviceWorker" in navigator) ||
    !("PushManager" in window)
  ) {
    console.warn("این مرورگر از Web Push پشتیبانی نمی‌کنه.");
    return;
  }

  try {
    if (
      "Notification" in window &&
      Notification.permission === "default"
    ) {
      await Notification.requestPermission();
    }

    if (
      "Notification" in window &&
      Notification.permission !== "granted"
    ) {
      showToast("⚠️ اجازه‌ی نوتیفیکیشن داده نشد");
      return;
    }

    const registration =
      await navigator.serviceWorker.ready;

    let subscription =
      await registration.pushManager.getSubscription();

    const oldEndpoint = subscription ? subscription.endpoint : null;

    // همیشه اشتراک قدیمی رو پاک می‌کنیم و یه اشتراک تازه با کلید فعلی
    // سرور می‌سازیم - چون اگه کلید سرور عوض شده باشه، اشتراک قدیمی
    // دیگه معتبر نیست (باعث خطای VapidPkHashMismatch میشه)
    if (subscription) {
      await subscription.unsubscribe();
    }

    const res = await fetch(
      `${API_BASE}/api/vapid-public-key`
    );

    const { key } = await res.json();

    subscription =
      await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey:
          urlBase64ToUint8Array(key),
      });

    const subRes = await fetch(`${API_BASE}/api/subscribe`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        subscription,
        alerts,
      }),
    });

    // سرور مرجع اصلیه: اگه یه هشدار قبلاً از سمت سرور فایر شده بود،
    // وضعیتش رو همینجا با گوشی هماهنگ می‌کنیم تا دوباره محلی هم
    // نوتیف نده (جلوگیری از پیام تکراری)
    const subData = await subRes.json().catch(() => null);
    if (subData && subData.alerts) {
      saveAlerts(subData.alerts);
      renderAlerts();
    }

    // حالا که وضعیت جدید با موفقیت ثبت و sync شد، خیالمون راحته که
    // endpoint قدیمی رو از سرور هم پاک کنیم تا روی هم جمع نشن
    if (oldEndpoint) {
      fetch(`${API_BASE}/api/unsubscribe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ endpoint: oldEndpoint }),
      }).catch(() => {});
    }
  } catch (err) {
    console.error("خطا در راه‌اندازی Push:", err);

    showToast(
      "⚠️ ثبت نوتیفیکیشن ناموفق بود (جزئیات در Console)"
    );
  }
}

if (loadAlerts().length > 0) {
  setupPushAndSync(loadAlerts());
}

// ---------------------------------------------------------------------------
// گرفتن قیمت
// ---------------------------------------------------------------------------

const priceEl = document.getElementById("price");
const changeBadgeEl =
  document.getElementById("changeBadge");

const changeValueEl =
  document.getElementById("changeValue");

const updatedEl =
  document.getElementById("updated");

const statOpenEl =
  document.getElementById("statOpen");

const statYesterdayEl =
  document.getElementById("statYesterday");

const statHighEl =
  document.getElementById("statHigh");

const statLowEl =
  document.getElementById("statLow");

let lastPrice = null;

async function fetchPrice() {
  try {
    const res = await fetch(
      `${API_BASE}/api/price`
    );

    const data = await res.json();

    priceEl.textContent = formatPrice(data.price);

    priceEl.classList.add("bump");

    setTimeout(
      () => priceEl.classList.remove("bump"),
      250
    );

    priceEl.classList.remove("up", "down");

    if (lastPrice !== null) {
      if (data.price > lastPrice) {
        priceEl.classList.add("up");
      } else if (data.price < lastPrice) {
        priceEl.classList.add("down");
      }
    }

    lastPrice = data.price;

    changeBadgeEl.classList.remove(
      "up",
      "down",
      "flat"
    );

    if (data.change_percent > 0) {
      changeBadgeEl.classList.add("up");
    } else if (data.change_percent < 0) {
      changeBadgeEl.classList.add("down");
    } else {
      changeBadgeEl.classList.add("flat");
    }

    changeValueEl.textContent =
      formatSigned(data.change_percent);

    if (data.updated_at) {
      updatedEl.textContent =
        "آخرین آپدیت: " +
        timeFmt.format(
          new Date(data.updated_at)
        );
    }

    statOpenEl.textContent =
      formatPrice(data.today_open);

    statYesterdayEl.textContent =
      formatPrice(data.yesterday_close);

    statHighEl.textContent =
      formatPrice(data.today_high);

    statLowEl.textContent =
      formatPrice(data.today_low);

    updateChart(data.history);

    checkAlerts(data.price);
  } catch (err) {
    updatedEl.textContent =
      "قطع ارتباط با سرور...";
  }
}

fetchPrice();

setInterval(fetchPrice, 3000);

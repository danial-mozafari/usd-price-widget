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
});

// ---------------------------------------------------------------------------
// نمودار
// ---------------------------------------------------------------------------
// نکته‌ی مهم: اگه به هر دلیلی (فیلترینگ، قطعی شبکه) کتابخونه‌ی Chart.js از
// CDN لود نشه، نباید کل صفحه بخوابه. این بخش رو کاملاً محافظت‌شده نوشتیم تا
// اگه نمودار درست نشه، بقیه‌ی برنامه (خصوصاً گرفتن قیمت) بدون مشکل کار کنه.

let chart = null;

if (typeof Chart === "undefined") {
  console.warn("Chart.js لود نشد - نمودار غیرفعال میشه ولی بقیه‌ی برنامه کار می‌کنه");
} else {
  try {
    const chartCanvas = document.getElementById("priceChart");
    const ctx = chartCanvas.getContext("2d");

    const gradient = ctx.createLinearGradient(0, 0, 0, 110);
    gradient.addColorStop(0, "rgba(129, 140, 248, 0.35)");
    gradient.addColorStop(1, "rgba(129, 140, 248, 0)");

    chart = new Chart(ctx, {
      type: "line",
      data: {
        labels: [],
        datasets: [{
          data: [],
          borderColor: "#818cf8",
          backgroundColor: gradient,
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.35,
          fill: true,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 400 },
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false },
        },
        scales: {
          x: { display: false },
          y: { display: false },
        },
      },
    });
  } catch (e) {
    console.warn("ساخت نمودار با خطا مواجه شد:", e);
    chart = null;
  }
}

function updateChart(history) {
  if (!chart || !history || history.length === 0) return;

  try {
    const recent = history.slice(-40);
    chart.data.labels = recent.map((_, i) => i);
    chart.data.datasets[0].data = recent.map((h) => h.p);
    chart.update("none");
  } catch (e) {
    console.warn("آپدیت نمودار با خطا مواجه شد:", e);
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

    if (!subscription) {
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
    }

    await fetch(`${API_BASE}/api/subscribe`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        subscription,
        alerts,
      }),
    });
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

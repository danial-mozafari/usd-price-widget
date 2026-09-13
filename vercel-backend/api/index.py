"""
USD Price Backend - نسخه‌ی Vercel (Serverless)
=================================================
برخلاف نسخه‌ی Render، این نسخه یه برنامه‌ی همیشه-روشن نیست - هر بار که
یه درخواست میاد (یا cron-job.org به /api/tick سر می‌زنه)، یه‌بار اجرا
میشه و تموم. برای همین، همه‌چیز (قیمت فعلی، تاریخچه، آمار روزانه،
اشتراک‌های Push) باید روی Supabase ذخیره بشه - هیچ متغیر حافظه‌ای بین
دو تا درخواست دووم نمیاره.

جریان کار:
- cron-job.org هر چند دقیقه یه‌بار /api/tick رو صدا می‌زنه → قیمت جدید
  گرفته میشه، توی Supabase ذخیره میشه، هشدارها چک میشن.
- مرورگر/اپ هر چند ثانیه /api/price رو صدا می‌زنه → فقط آخرین مقداری
  که توی Supabase ذخیره شده رو می‌خونه و برمی‌گردونه (سریع و بدون فچ).
"""

import os
import json
import threading
from collections import deque
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request
from pywebpush import webpush, WebPushException
from py_vapid import Vapid01 as Vapid

try:
    from zoneinfo import ZoneInfo
    TEHRAN_TZ = ZoneInfo("Asia/Tehran")
except Exception:
    TEHRAN_TZ = timezone.utc

app = Flask(__name__)


# =========================
# CORS
# =========================
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


# =========================
# Environment
# =========================
VAPID_CLAIM_EMAIL = os.environ.get(
    "VAPID_CLAIM_EMAIL",
    "mailto:push-notifications@usd-price-widget.app"
)

USE_REAL_API = os.environ.get("USE_REAL_API", "0").strip() == "1"
BRSAPI_KEY = os.environ.get("BRSAPI_KEY", "").strip()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "").strip()

# روی سرورلس، دیسک دائمی نداریم - کلید VAPID باید همیشه از env var بیاد
# (همون کلیدی که قبلاً روی Render هم استفاده می‌کردیم، تا اشتراک‌های
# قبلی گوشی‌ها خراب نشن)
VAPID_PRIVATE_KEY_PEM = os.environ.get("VAPID_PRIVATE_KEY_PEM", "").strip()
VAPID_PRIVATE_FILE = "/tmp/vapid_private_key.pem"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

HISTORY_MAXLEN = 200


# =========================
# Supabase (تنها منبع حافظه)
# =========================

def _supabase_headers():
    return {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def kv_get(key, default):
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        print(f"[kv_get:{key}] SUPABASE_URL یا SUPABASE_SECRET_KEY خالیه")
        return default
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/kv_store",
            headers=_supabase_headers(),
            params={"key": f"eq.{key}", "select": "value"},
            timeout=10,
        )
        if resp.status_code >= 300:
            print(f"[kv_get:{key}] status={resp.status_code} body={resp.text[:300]}")
            return default
        rows = resp.json()
        print(f"[kv_get:{key}] موفق - {len(rows)} ردیف پیدا شد")
        if rows:
            return rows[0]["value"]
    except Exception as e:
        print(f"[kv_get:{key}] exception: {e}")
    return default


def kv_set(key, value):
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        print(f"[kv_set:{key}] SUPABASE_URL یا SUPABASE_SECRET_KEY خالیه")
        return {"ok": False, "reason": "missing_config"}
    try:
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/kv_store",
            headers={**_supabase_headers(), "Prefer": "resolution=merge-duplicates"},
            params={"on_conflict": "key"},
            json={"key": key, "value": value},
            timeout=10,
        )
        if resp.status_code >= 300:
            print(f"[kv_set:{key}] status={resp.status_code} body={resp.text[:300]}")
            return {"ok": False, "status": resp.status_code, "body": resp.text[:300]}
        print(f"[kv_set:{key}] موفق status={resp.status_code}")
        return {"ok": True, "status": resp.status_code}
    except Exception as e:
        print(f"[kv_set:{key}] exception: {e}")
        return {"ok": False, "exception": str(e)}


def load_state():
    return kv_get("current_state", {
        "price": 0,
        "currency": "USD/IRT",
        "change_percent": 0.0,
        "updated_at": None,
        "source": "simulator",
    })


def load_history():
    return kv_get("price_history", [])


def load_daily_data():
    return kv_get("daily_data", {
        "date": None,
        "today_open": None,
        "yesterday_close": None,
        "last_price": None,
        "today_high": None,
        "today_low": None,
    })


def load_subscriptions():
    return kv_get("subscriptions", [])


def save_subscriptions(subs):
    kv_set("subscriptions", subs)


# =========================
# VAPID
# =========================

def ensure_vapid_key_file() -> Vapid:
    if not VAPID_PRIVATE_KEY_PEM:
        raise RuntimeError(
            "VAPID_PRIVATE_KEY_PEM تنظیم نشده. روی Vercel این env var رو "
            "دقیقاً با همون مقداری که روی Render داشتی ست کن."
        )
    with open(VAPID_PRIVATE_FILE, "w") as f:
        f.write(VAPID_PRIVATE_KEY_PEM.replace("\\n", "\n"))
    return Vapid.from_file(VAPID_PRIVATE_FILE)


def get_vapid_public_key_b64() -> str:
    import base64
    vapid_instance = ensure_vapid_key_file()
    numbers = vapid_instance.public_key.public_numbers()
    x = numbers.x.to_bytes(32, "big")
    y = numbers.y.to_bytes(32, "big")
    raw = b"\x04" + x + y
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("utf-8")


def send_push(subscription_info, title, body):
    try:
        ensure_vapid_key_file()
        webpush(
            subscription_info=subscription_info,
            data=json.dumps({"title": title, "body": body}),
            vapid_private_key=VAPID_PRIVATE_FILE,
            vapid_claims={"sub": VAPID_CLAIM_EMAIL},
        )
        print(f"[send_push] موفق - به {subscription_info.get('endpoint', '?')[:60]}... فرستاده شد")
        return True
    except WebPushException as e:
        response = getattr(e, "response", None)
        status = getattr(response, "status_code", None)
        body_text = getattr(response, "text", None)
        print(f"[send_push] error status={status} body={body_text}: {e}")
        return status not in (404, 410)
    except Exception as e:
        print(f"[send_push] خطای غیرمنتظره: {e}")
        return True


# =========================
# منطق قیمت
# =========================

def fetch_real_price() -> float:
    if not BRSAPI_KEY:
        raise RuntimeError("BRSAPI_KEY تنظیم نشده.")

    url = f"https://Api.BrsApi.ir/Market/Gold_Currency.php?key={BRSAPI_KEY}"
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("currency") or data.get("gold_currency") or []
    else:
        items = []

    for item in items:
        symbol = str(item.get("symbol", "")).upper()
        name_en = str(item.get("name_en", "")).lower()
        if symbol == "USD" or "dollar" in name_en:
            price_str = str(item.get("price", "")).replace(",", "").strip()
            if not price_str:
                break
            return float(price_str)

    raise ValueError("آیتم دلار (USD) توی پاسخ API پیدا نشد.")


def update_daily_tracking(daily, new_price):
    today = datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d")

    if daily["date"] is None:
        daily["date"] = today
        daily["today_open"] = new_price
        daily["today_high"] = new_price
        daily["today_low"] = new_price
    elif daily["date"] != today:
        daily["yesterday_close"] = daily["last_price"]
        daily["date"] = today
        daily["today_open"] = new_price
        daily["today_high"] = new_price
        daily["today_low"] = new_price
    else:
        daily["today_high"] = max(daily["today_high"] or new_price, new_price)
        daily["today_low"] = min(daily["today_low"] or new_price, new_price)

    daily["last_price"] = new_price
    return daily


def check_and_fire_alerts(new_price):
    subscriptions = load_subscriptions()
    changed = False
    still_valid = []

    for sub in subscriptions:
        alive = True
        for alert in sub.get("alerts", []):
            if alert.get("firedAt"):
                continue
            hit = (
                new_price >= alert["value"] if alert["direction"] == "gte"
                else new_price <= alert["value"]
            )
            if hit:
                ok = send_push(
                    sub["subscription"],
                    "قیمت دلار",
                    f"دلار به {int(new_price):,} تومان رسید",
                )
                if not ok:
                    alive = False
                    break
                alert["firedAt"] = datetime.now(timezone.utc).isoformat()
                changed = True
        if alive:
            still_valid.append(sub)
        else:
            changed = True

    if changed:
        save_subscriptions(still_valid)


# =========================
# روت‌ها
# =========================

@app.route("/api/price")
def api_price():
    state = load_state()
    history = load_history()
    daily = load_daily_data()

    payload = dict(state)
    payload["history"] = history
    payload["today_open"] = daily.get("today_open")
    payload["yesterday_close"] = daily.get("yesterday_close")
    payload["today_high"] = daily.get("today_high")
    payload["today_low"] = daily.get("today_low")

    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@app.route("/api/tick")
def api_tick():
    """این روت رو cron-job.org هر چند دقیقه یه‌بار صدا می‌زنه."""
    state = load_state()
    old_price = state.get("price", 0)

    try:
        new_price = fetch_real_price() if USE_REAL_API else old_price
    except Exception as e:
        print(f"[api_tick] error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

    change_percent = 0.0
    if old_price:
        change_percent = round((new_price - old_price) / old_price * 100, 3)

    now_iso = datetime.now(timezone.utc).isoformat()

    daily = load_daily_data()
    daily = update_daily_tracking(daily, new_price)
    debug_daily_save = kv_set("daily_data", daily)

    history = load_history()
    history.append({"t": now_iso, "p": new_price})
    history = history[-HISTORY_MAXLEN:]
    debug_history_save = kv_set("price_history", history)

    new_state = {
        "price": new_price,
        "currency": "USD/IRT",
        "change_percent": change_percent,
        "updated_at": now_iso,
        "source": "real_api" if USE_REAL_API else "simulator",
    }
    debug_state_save = kv_set("current_state", new_state)

    check_and_fire_alerts(new_price)

    return jsonify({
        "ok": True,
        "price": new_price,
        "debug_daily_save": debug_daily_save,
        "debug_history_save": debug_history_save,
        "debug_state_save": debug_state_save,
        "supabase_url_set": bool(SUPABASE_URL),
        "supabase_key_set": bool(SUPABASE_SECRET_KEY),
    })


@app.route("/api/vapid-public-key")
def api_vapid_public_key():
    return jsonify({"key": get_vapid_public_key_b64()})


@app.route("/api/subscribe", methods=["POST", "OPTIONS"])
def api_subscribe():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(force=True, silent=True) or {}
    subscription_info = body.get("subscription")
    alerts = body.get("alerts", [])

    if not subscription_info or "endpoint" not in subscription_info:
        return jsonify({"ok": False, "error": "invalid subscription"}), 400

    subscriptions = load_subscriptions()

    existing_fired = {}
    for s in subscriptions:
        for a in s.get("alerts", []):
            if a.get("firedAt"):
                existing_fired[a["id"]] = a["firedAt"]

    merged_alerts = []
    for a in alerts:
        a = dict(a)
        if a["id"] in existing_fired and not a.get("firedAt"):
            a["firedAt"] = existing_fired[a["id"]]
        merged_alerts.append(a)
    alerts = merged_alerts

    subscriptions = [
        s for s in subscriptions
        if s["subscription"]["endpoint"] != subscription_info["endpoint"]
    ]
    subscriptions.append({"subscription": subscription_info, "alerts": alerts})
    save_subscriptions(subscriptions)

    return jsonify({"ok": True, "alerts": alerts})


@app.route("/api/unsubscribe", methods=["POST", "OPTIONS"])
def api_unsubscribe():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(force=True, silent=True) or {}
    endpoint = body.get("endpoint")

    subscriptions = load_subscriptions()
    subscriptions = [
        s for s in subscriptions
        if s["subscription"]["endpoint"] != endpoint
    ]
    save_subscriptions(subscriptions)

    return jsonify({"ok": True})

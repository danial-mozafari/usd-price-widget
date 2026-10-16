```python
"""
USD Price Backend - Vercel Serverless
قیمت دلار از Iran Market Data و ذخیره‌سازی در Supabase
"""

import os
import json
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

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "").strip()

VAPID_PRIVATE_KEY_PEM = os.environ.get(
    "VAPID_PRIVATE_KEY_PEM", ""
).strip()

VAPID_PRIVATE_FILE = "/tmp/vapid_private_key.pem"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

HISTORY_MAXLEN = 200

PRICE_API_URL = (
    "https://raw.githubusercontent.com/"
    "iran-market/iran-market.github.io/main/data/popular.json"
)


# =========================
# Supabase
# =========================

def _supabase_headers():
    return {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def kv_get(key, default):
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        print(f"[kv_get:{key}] Supabase config missing")
        return default

    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/kv_store",
            headers=_supabase_headers(),
            params={"key": f"eq.{key}", "select": "value"},
            timeout=10,
        )

        if resp.status_code >= 300:
            print(
                f"[kv_get:{key}] status={resp.status_code} "
                f"body={resp.text[:300]}"
            )
            return default

        rows = resp.json()

        if rows:
            return rows[0]["value"]

    except Exception as e:
        print(f"[kv_get:{key}] exception: {e}")

    return default


def kv_set(key, value):
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        print(f"[kv_set:{key}] Supabase config missing")
        return {"ok": False, "reason": "missing_config"}

    try:
        resp = requests.post(
            f"{SUPABASE_URL}/rest/v1/kv_store",
            headers={
                **_supabase_headers(),
                "Prefer": "resolution=merge-duplicates",
            },
            params={"on_conflict": "key"},
            json={"key": key, "value": value},
            timeout=10,
        )

        if resp.status_code >= 300:
            print(
                f"[kv_set:{key}] status={resp.status_code} "
                f"body={resp.text[:300]}"
            )
            return {
                "ok": False,
                "status": resp.status_code,
                "body": resp.text[:300],
            }

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
        "source": "iran_market",
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
    return kv_set("subscriptions", subs)


# =========================
# VAPID / Push Notifications
# =========================

def ensure_vapid_key_file():
    if not VAPID_PRIVATE_KEY_PEM:
        raise RuntimeError("VAPID_PRIVATE_KEY_PEM تنظیم نشده است.")

    with open(VAPID_PRIVATE_FILE, "w", encoding="utf-8") as f:
        f.write(VAPID_PRIVATE_KEY_PEM.replace("\\n", "\n"))

    return Vapid.from_file(VAPID_PRIVATE_FILE)


def get_vapid_public_key_b64():
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

        return True

    except WebPushException as e:
        response = getattr(e, "response", None)
        status = getattr(response, "status_code", None)

        print(f"[send_push] status={status}: {e}")
        return status not in (404, 410)

    except Exception as e:
        print(f"[send_push] error: {e}")
        return True


# =========================
# دریافت قیمت واقعی دلار
# =========================

def fetch_real_price() -> float:
    resp = requests.get(
        PRICE_API_URL,
        headers=REQUEST_HEADERS,
        timeout=20,
    )
    resp.raise_for_status()

    data = resp.json()

    if not isinstance(data, dict):
        raise ValueError("ساختار پاسخ منبع قیمت نامعتبر است.")

    items = data.get("data", [])

    if not isinstance(items, list):
        raise ValueError("فهرست قیمت‌ها معتبر نیست.")

    for item in items:
        if not isinstance(item, dict):
            continue

        if item.get("symbol") != "USD_IRR_FREE":
            continue

        if item.get("stale") is True:
            raise ValueError("داده قیمت دلار قدیمی است.")

        raw_price = item.get("price")

        try:
            price = float(str(raw_price).replace(",", "").strip())
        except (TypeError, ValueError):
            raise ValueError("قیمت دلار قابل تبدیل به عدد نیست.")

        if price <= 0:
            raise ValueError("قیمت دریافت‌شده نامعتبر است.")

        return price

    raise ValueError("نماد USD_IRR_FREE در پاسخ منبع پیدا نشد.")


# =========================
# آمار روزانه
# =========================

def update_daily_tracking(daily, new_price):
    today = datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d")

    if daily.get("date") is None:
        daily["date"] = today
        daily["today_open"] = new_price
        daily["today_high"] = new_price
        daily["today_low"] = new_price

    elif daily.get("date") != today:
        daily["yesterday_close"] = daily.get("last_price")
        daily["date"] = today
        daily["today_open"] = new_price
        daily["today_high"] = new_price
        daily["today_low"] = new_price

    else:
        daily["today_high"] = max(
            daily.get("today_high") or new_price,
            new_price,
        )
        daily["today_low"] = min(
            daily.get("today_low") or new_price,
            new_price,
        )

    daily["last_price"] = new_price
    return daily


# =========================
# بررسی هشدارها
# =========================

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
                new_price >= alert["value"]
                if alert["direction"] == "gte"
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

                alert["firedAt"] = datetime.now(
                    timezone.utc
                ).isoformat()

                changed = True

        if alive:
            still_valid.append(sub)
        else:
            changed = True

    if changed:
        save_subscriptions(still_valid)


# =========================
# API: قیمت فعلی
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
    response.headers["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0"
    )

    return response


# =========================
# API: دریافت و ذخیره قیمت جدید
# =========================

@app.route("/api/tick")
def api_tick():
    state = load_state()
    old_price = float(state.get("price") or 0)

    # همیشه قیمت واقعی دریافت می‌شود؛ به USE_REAL_API وابسته نیست.
    try:
        new_price = fetch_real_price()
    except Exception as e:
        print(f"[api_tick] error: {e}")
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500

    change_percent = 0.0

    if old_price > 0:
        change_percent = round(
            (new_price - old_price) / old_price * 100,
            3,
        )

    now_iso = datetime.now(timezone.utc).isoformat()

    daily = load_daily_data()
    daily = update_daily_tracking(daily, new_price)
    debug_daily_save = kv_set("daily_data", daily)

    history = load_history()

    if not isinstance(history, list):
        history = []

    history.append({
        "t": now_iso,
        "p": new_price,
    })
    history = history[-HISTORY_MAXLEN:]

    debug_history_save = kv_set("price_history", history)

    new_state = {
        "price": new_price,
        "currency": "USD/IRT",
        "change_percent": change_percent,
        "updated_at": now_iso,
        "source": "iran_market",
    }

    debug_state_save = kv_set("current_state", new_state)

    check_and_fire_alerts(new_price)

    return jsonify({
        "ok": True,
        "price": new_price,
        "source": "iran_market",
        "debug_daily_save": debug_daily_save,
        "debug_history_save": debug_history_save,
        "debug_state_save": debug_state_save,
        "supabase_url_set": bool(SUPABASE_URL),
        "supabase_key_set": bool(SUPABASE_SECRET_KEY),
    })


# =========================
# API: کلید عمومی VAPID
# =========================

@app.route("/api/vapid-public-key")
def api_vapid_public_key():
    return jsonify({"key": get_vapid_public_key_b64()})


# =========================
# API: اشتراک Push
# =========================

@app.route("/api/subscribe", methods=["POST", "OPTIONS"])
def api_subscribe():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(force=True, silent=True) or {}
    subscription_info = body.get("subscription")
    alerts = body.get("alerts", [])

    if not subscription_info or "endpoint" not in subscription_info:
        return jsonify({
            "ok": False,
            "error": "invalid subscription",
        }), 400

    subscriptions = load_subscriptions()

    existing_fired = {}

    for sub in subscriptions:
        for alert in sub.get("alerts", []):
            if alert.get("firedAt"):
                existing_fired[alert["id"]] = alert["firedAt"]

    merged_alerts = []

    for alert in alerts:
        alert = dict(alert)

        if (
            alert["id"] in existing_fired
            and not alert.get("firedAt")
        ):
            alert["firedAt"] = existing_fired[alert["id"]]

        merged_alerts.append(alert)

    subscriptions = [
        sub for sub in subscriptions
        if sub["subscription"]["endpoint"]
        != subscription_info["endpoint"]
    ]

    subscriptions.append({
        "subscription": subscription_info,
        "alerts": merged_alerts,
    })

    save_subscriptions(subscriptions)

    return jsonify({
        "ok": True,
        "alerts": merged_alerts,
    })


# =========================
# API: لغو اشتراک Push
# =========================

@app.route("/api/unsubscribe", methods=["POST", "OPTIONS"])
def api_unsubscribe():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(force=True, silent=True) or {}
    endpoint = body.get("endpoint")

    subscriptions = load_subscriptions()

    subscriptions = [
        sub for sub in subscriptions
        if sub["subscription"]["endpoint"] != endpoint
    ]

    save_subscriptions(subscriptions)

    return jsonify({"ok": True})
```

```python
import os
import json
import base64
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


# Vercel Flask entry point
app = Flask(__name__)


# =========================
# Configuration
# =========================

VAPID_CLAIM_EMAIL = os.environ.get(
    "VAPID_CLAIM_EMAIL",
    "mailto:push-notifications@usd-price-widget.app",
)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
VAPID_PRIVATE_KEY_PEM = os.environ.get(
    "VAPID_PRIVATE_KEY_PEM", ""
).strip()

VAPID_PRIVATE_FILE = "/tmp/vapid_private_key.pem"
HISTORY_MAXLEN = 200

PRICE_API_URL = (
    "https://raw.githubusercontent.com/"
    "iran-market/iran-market.github.io/main/data/popular.json"
)

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 USDPriceWidget/1.0"
}


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
# Home / Health check
# =========================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "ok": True,
        "service": "usd-widget-backend",
        "endpoints": [
            "/api/price",
            "/api/tick",
            "/api/vapid-public-key",
            "/api/subscribe",
            "/api/unsubscribe",
        ],
    })


@app.route("/api/health", methods=["GET"])
def api_health():
    return jsonify({
        "ok": True,
        "supabase_url_set": bool(SUPABASE_URL),
        "supabase_key_set": bool(SUPABASE_SECRET_KEY),
    })


# =========================
# Supabase helpers
# =========================

def _supabase_headers():
    return {
        "apikey": SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def kv_get(key, default):
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        print(f"[kv_get:{key}] Supabase configuration missing")
        return default

    try:
        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/kv_store",
            headers=_supabase_headers(),
            params={"key": f"eq.{key}", "select": "value"},
            timeout=10,
        )

        if response.status_code >= 300:
            print(
                f"[kv_get:{key}] status={response.status_code} "
                f"body={response.text[:300]}"
            )
            return default

        rows = response.json()

        if rows:
            return rows[0].get("value", default)

    except Exception as exc:
        print(f"[kv_get:{key}] error: {exc}")

    return default


def kv_set(key, value):
    if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
        return {"ok": False, "reason": "missing_config"}

    try:
        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/kv_store",
            headers={
                **_supabase_headers(),
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            params={"on_conflict": "key"},
            json={"key": key, "value": value},
            timeout=10,
        )

        if response.status_code >= 300:
            print(
                f"[kv_set:{key}] status={response.status_code} "
                f"body={response.text[:300]}"
            )
            return {
                "ok": False,
                "status": response.status_code,
                "body": response.text[:300],
            }

        return {"ok": True, "status": response.status_code}

    except Exception as exc:
        print(f"[kv_set:{key}] error: {exc}")
        return {"ok": False, "exception": str(exc)}


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


def save_subscriptions(subscriptions):
    return kv_set("subscriptions", subscriptions)


# =========================
# VAPID / Push notifications
# =========================

def ensure_vapid_key_file():
    if not VAPID_PRIVATE_KEY_PEM:
        raise RuntimeError("VAPID_PRIVATE_KEY_PEM تنظیم نشده است.")

    with open(VAPID_PRIVATE_FILE, "w", encoding="utf-8") as file:
        file.write(VAPID_PRIVATE_KEY_PEM.replace("\\n", "\n"))

    return Vapid.from_file(VAPID_PRIVATE_FILE)


def get_vapid_public_key_b64():
    vapid = ensure_vapid_key_file()
    numbers = vapid.public_key.public_numbers()

    raw = (
        b"\x04"
        + numbers.x.to_bytes(32, "big")
        + numbers.y.to_bytes(32, "big")
    )

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

    except WebPushException as exc:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        print(f"[send_push] status={status}: {exc}")

        # Expired subscriptions should be removed.
        return status not in (404, 410)

    except Exception as exc:
        print(f"[send_push] error: {exc}")
        return False


# =========================
# Fetch real USD price
# =========================

def fetch_real_price() -> float:
    response = requests.get(
        PRICE_API_URL,
        headers=REQUEST_HEADERS,
        timeout=20,
    )
    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, dict):
        raise ValueError("ساختار پاسخ منبع قیمت نامعتبر است.")

    items = payload.get("data", [])

    if not isinstance(items, list):
        raise ValueError("فهرست قیمت‌ها معتبر نیست.")

    for item in items:
        if not isinstance(item, dict):
            continue

        if item.get("symbol") != "USD_IRR_FREE":
            continue

        if item.get("stale") is True:
            raise ValueError("داده قیمت دلار قدیمی است.")

        try:
            price = float(
                str(item.get("price", "")).replace(",", "").strip()
            )
        except (TypeError, ValueError):
            raise ValueError("قیمت دریافت‌شده معتبر نیست.")

        if price <= 0:
            raise ValueError("قیمت دریافت‌شده صفر یا نامعتبر است.")

        return price

    raise ValueError("نماد USD_IRR_FREE در پاسخ منبع پیدا نشد.")


# =========================
# Daily statistics
# =========================

def update_daily_tracking(daily, new_price):
    today = datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d")

    if daily.get("date") != today:
        if daily.get("date") is not None:
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
# Price alerts
# =========================

def check_and_fire_alerts(new_price):
    subscriptions = load_subscriptions()

    if not isinstance(subscriptions, list):
        return

    changed = False
    remaining = []

    for sub in subscriptions:
        alive = True

        for alert in sub.get("alerts", []):
            if alert.get("firedAt"):
                continue

            try:
                target = float(alert["value"])
                direction = alert["direction"]
            except (KeyError, TypeError, ValueError):
                continue

            if direction not in ("gte", "lte"):
                continue

            hit = (
                new_price >= target
                if direction == "gte"
                else new_price <= target
            )

            if not hit:
                continue

            success = send_push(
                sub["subscription"],
                "قیمت دلار",
                f"دلار به {int(new_price):,} تومان رسید",
            )

            if not success:
                alive = False
                break

            alert["firedAt"] = datetime.now(
                timezone.utc
            ).isoformat()
            changed = True

        if alive:
            remaining.append(sub)
        else:
            changed = True

    if changed:
        save_subscriptions(remaining)


# =========================
# GET /api/price
# =========================

@app.route("/api/price", methods=["GET"])
def api_price():
    state = load_state()
    history = load_history()
    daily = load_daily_data()

    if not isinstance(state, dict):
        state = {}

    if not isinstance(history, list):
        history = []

    if not isinstance(daily, dict):
        daily = {}

    payload = {
        "price": state.get("price", 0),
        "currency": state.get("currency", "USD/IRT"),
        "change_percent": state.get("change_percent", 0.0),
        "updated_at": state.get("updated_at"),
        "source": state.get("source", "iran_market"),
        "history": history,
        "today_open": daily.get("today_open"),
        "yesterday_close": daily.get("yesterday_close"),
        "today_high": daily.get("today_high"),
        "today_low": daily.get("today_low"),
    }

    response = jsonify(payload)
    response.headers["Cache-Control"] = (
        "no-store, no-cache, must-revalidate, max-age=0"
    )
    return response


# =========================
# GET /api/tick
# =========================

@app.route("/api/tick", methods=["GET"])
def api_tick():
    state = load_state()

    if not isinstance(state, dict):
        state = {}

    try:
        old_price = float(state.get("price") or 0)
    except (TypeError, ValueError):
        old_price = 0.0

    try:
        new_price = fetch_real_price()
    except Exception as exc:
        print(f"[api_tick] price fetch failed: {exc}")
        return jsonify({
            "ok": False,
            "error": "دریافت قیمت از منبع ناموفق بود.",
            "details": str(exc),
        }), 502

    change_percent = 0.0

    if old_price > 0:
        change_percent = round(
            ((new_price - old_price) / old_price) * 100,
            3,
        )

    now_iso = datetime.now(timezone.utc).isoformat()

    daily = load_daily_data()
    if not isinstance(daily, dict):
        daily = {}

    daily = update_daily_tracking(daily, new_price)
    daily_result = kv_set("daily_data", daily)

    history = load_history()
    if not isinstance(history, list):
        history = []

    history.append({"t": now_iso, "p": new_price})
    history = history[-HISTORY_MAXLEN:]
    history_result = kv_set("price_history", history)

    new_state = {
        "price": new_price,
        "currency": "USD/IRT",
        "change_percent": change_percent,
        "updated_at": now_iso,
        "source": "iran_market",
    }

    state_result = kv_set("current_state", new_state)

    if state_result.get("ok"):
        try:
            check_and_fire_alerts(new_price)
        except Exception as exc:
            print(f"[api_tick] alert check failed: {exc}")

    return jsonify({
        "ok": True,
        "price": new_price,
        "source": "iran_market",
        "debug_daily_save": daily_result,
        "debug_history_save": history_result,
        "debug_state_save": state_result,
        "supabase_url_set": bool(SUPABASE_URL),
        "supabase_key_set": bool(SUPABASE_SECRET_KEY),
    })


# =========================
# GET /api/vapid-public-key
# =========================

@app.route("/api/vapid-public-key", methods=["GET"])
def api_vapid_public_key():
    try:
        return jsonify({"key": get_vapid_public_key_b64()})
    except Exception as exc:
        print(f"[api_vapid_public_key] error: {exc}")
        return jsonify({
            "ok": False,
            "error": "خطا در ساخت کلید عمومی VAPID",
        }), 500


# =========================
# POST /api/subscribe
# =========================

@app.route("/api/subscribe", methods=["POST", "OPTIONS"])
def api_subscribe():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(silent=True) or {}
    subscription_info = body.get("subscription")
    alerts = body.get("alerts", [])

    if (
        not isinstance(subscription_info, dict)
        or not subscription_info.get("endpoint")
    ):
        return jsonify({
            "ok": False,
            "error": "invalid subscription",
        }), 400

    if not isinstance(alerts, list):
        return jsonify({
            "ok": False,
            "error": "alerts must be a list",
        }), 400

    subscriptions = load_subscriptions()
    if not isinstance(subscriptions, list):
        subscriptions = []

    existing_fired = {}

    for sub in subscriptions:
        if not isinstance(sub, dict):
            continue

        for alert in sub.get("alerts", []):
            if not isinstance(alert, dict):
                continue

            if alert.get("firedAt"):
                existing_fired[alert.get("id")] = alert["firedAt"]

    merged_alerts = []

    for alert in alerts:
        if not isinstance(alert, dict):
            continue

        alert = dict(alert)
        alert_id = alert.get("id")

        if alert_id in existing_fired and not alert.get("firedAt"):
            alert["firedAt"] = existing_fired[alert_id]

        merged_alerts.append(alert)

    endpoint = subscription_info["endpoint"]

    subscriptions = [
        sub for sub in subscriptions
        if not (
            isinstance(sub, dict)
            and sub.get("subscription", {}).get("endpoint") == endpoint
        )
    ]

    subscriptions.append({
        "subscription": subscription_info,
        "alerts": merged_alerts,
    })

    save_result = save_subscriptions(subscriptions)

    if not save_result.get("ok"):
        return jsonify({
            "ok": False,
            "error": "ذخیره‌سازی اشتراک ناموفق بود.",
        }), 500

    return jsonify({
        "ok": True,
        "alerts": merged_alerts,
    })


# =========================
# POST /api/unsubscribe
# =========================

@app.route("/api/unsubscribe", methods=["POST", "OPTIONS"])
def api_unsubscribe():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(silent=True) or {}
    endpoint = body.get("endpoint")

    if not endpoint:
        return jsonify({
            "ok": False,
            "error": "endpoint is required",
        }), 400

    subscriptions = load_subscriptions()
    if not isinstance(subscriptions, list):
        subscriptions = []

    subscriptions = [
        sub for sub in subscriptions
        if not (
            isinstance(sub, dict)
            and sub.get("subscription", {}).get("endpoint") == endpoint
        )
    ]

    save_result = save_subscriptions(subscriptions)

    if not save_result.get("ok"):
        return jsonify({
            "ok": False,
            "error": "ذخیره‌سازی ناموفق بود.",
        }), 500

    return jsonify({"ok": True})
```

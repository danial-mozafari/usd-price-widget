```python
"""
USD Price Dashboard - Flask backend
"""

import os
import json
import time
import random
import threading
from collections import deque
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template, request
from pywebpush import webpush, WebPushException
from py_vapid import Vapid01 as Vapid

try:
    from zoneinfo import ZoneInfo
    TEHRAN_TZ = ZoneInfo("Asia/Tehran")
except Exception:
    TEHRAN_TZ = timezone.utc

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DAILY_FILE = os.path.join(BASE_DIR, "daily_data.json")
VAPID_PRIVATE_FILE = os.path.join(BASE_DIR, "vapid_private_key.pem")
SUBSCRIPTIONS_FILE = os.path.join(BASE_DIR, "subscriptions.json")

VAPID_CLAIM_EMAIL = os.environ.get(
    "VAPID_CLAIM_EMAIL",
    "mailto:example@example.com"
)

USE_REAL_API = os.environ.get("USE_REAL_API", "0") == "1"
BRSAPI_KEY = os.environ.get("BRSAPI_KEY", "").strip()

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

POLL_INTERVAL_SECONDS = 60 if USE_REAL_API else 3
HISTORY_MAXLEN = 500

state_lock = threading.Lock()

state = {
    "price": 0,
    "currency": "USD/IRT",
    "change_percent": 0.0,
    "updated_at": None,
    "source": "simulator",
    "today_open": None,
    "yesterday_close": None,
    "today_high": None,
    "today_low": None,
}

price_history = deque(maxlen=HISTORY_MAXLEN)

# ---------------------------------------------------------------------------
# Daily data
# ---------------------------------------------------------------------------

def load_daily_data():
    if os.path.exists(DAILY_FILE):
        try:
            with open(DAILY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    return {
        "date": None,
        "today_open": None,
        "yesterday_close": None,
        "last_price": None,
        "today_high": None,
        "today_low": None,
    }


def save_daily_data(data):
    try:
        with open(DAILY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"[save_daily_data] error: {e}")


daily = load_daily_data()

# ---------------------------------------------------------------------------
# VAPID
# ---------------------------------------------------------------------------

subscriptions_lock = threading.Lock()


def ensure_vapid_keys() -> Vapid:
    env_key = os.environ.get("VAPID_PRIVATE_KEY_PEM", "").strip()

    if env_key:
        with open(VAPID_PRIVATE_FILE, "w") as f:
            f.write(env_key.replace("\\n", "\n"))

    elif not os.path.exists(VAPID_PRIVATE_FILE):
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization

        private_key = ec.generate_private_key(ec.SECP256R1())

        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        with open(VAPID_PRIVATE_FILE, "wb") as f:
            f.write(pem)

        print(f"[vapid] کلید جدید ساخته شد: {VAPID_PRIVATE_FILE}")

    return Vapid.from_file(VAPID_PRIVATE_FILE)


vapid_instance = ensure_vapid_keys()


def get_vapid_public_key_b64() -> str:
    import base64

    numbers = vapid_instance.public_key.public_numbers()

    x = numbers.x.to_bytes(32, "big")
    y = numbers.y.to_bytes(32, "big")

    raw = b"\x04" + x + y

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("utf-8")


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

def load_subscriptions():
    if os.path.exists(SUBSCRIPTIONS_FILE):
        try:
            with open(SUBSCRIPTIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    return []


def save_subscriptions(subs):
    try:
        with open(SUBSCRIPTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(subs, f, ensure_ascii=False)
    except Exception as e:
        print(f"[save_subscriptions] error: {e}")


subscriptions = load_subscriptions()


# ---------------------------------------------------------------------------
# Push notifications
# ---------------------------------------------------------------------------

def send_push(subscription_info: dict, title: str, body: str) -> bool:
    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps({
                "title": title,
                "body": body
            }),
            vapid_private_key=VAPID_PRIVATE_FILE,
            vapid_claims={
                "sub": VAPID_CLAIM_EMAIL
            },
        )

        return True

    except WebPushException as e:
        status = getattr(e.response, "status_code", None)

        print(
            f"[send_push] خطا (status={status}): {e}"
        )

        return status not in (404, 410)

    except Exception as e:
        print(
            f"[send_push] خطای غیرمنتظره: {e}"
        )

        return True


def check_and_fire_alerts(new_price: float):

    with subscriptions_lock:

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

                    alert["firedAt"] = (
                        datetime.now(timezone.utc).isoformat()
                    )

                    changed = True

            if alive:
                still_valid.append(sub)
            else:
                changed = True

        if changed:
            subscriptions[:] = still_valid
            save_subscriptions(subscriptions)


# ---------------------------------------------------------------------------
# Daily tracking
# ---------------------------------------------------------------------------

def update_daily_tracking(new_price: float):

    today = datetime.now(
        TEHRAN_TZ
    ).strftime("%Y-%m-%d")

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

        daily["today_high"] = max(
            daily["today_high"] or new_price,
            new_price
        )

        daily["today_low"] = min(
            daily["today_low"] or new_price,
            new_price
        )

    daily["last_price"] = new_price

    save_daily_data(daily)


# ---------------------------------------------------------------------------
# Price
# ---------------------------------------------------------------------------

def fetch_simulated_price(previous_price: float) -> float:

    if previous_price == 0:
        previous_price = 68500

    drift = random.uniform(-40, 40)

    return round(
        previous_price + drift,
        0
    )


def fetch_real_price() -> float:

    if not BRSAPI_KEY:
        raise RuntimeError(
            "BRSAPI_KEY تنظیم نشده."
        )

    url = (
        "https://Api.BrsApi.ir/"
        f"Market/Gold_Currency.php?key={BRSAPI_KEY}"
    )

    resp = requests.get(
        url,
        headers=REQUEST_HEADERS,
        timeout=5
    )

    resp.raise_for_status()

    data = resp.json()

    if isinstance(data, list):
        items = data

    elif isinstance(data, dict):
        items = (
            data.get("currency")
            or data.get("gold_currency")
            or []
        )

    else:
        items = []

    for item in items:

        symbol = str(
            item.get("symbol", "")
        ).upper()

        name_en = str(
            item.get("name_en", "")
        ).lower()

        if symbol == "USD" or "dollar" in name_en:

            price_str = str(
                item.get("price", "")
            ).replace(",", "").strip()

            if not price_str:
                break

            return float(price_str)

    raise ValueError(
        "آیتم دلار (USD) توی پاسخ API پیدا نشد."
    )


# ---------------------------------------------------------------------------
# Background updater
# ---------------------------------------------------------------------------

def price_updater_loop():

    while True:

        try:

            with state_lock:
                old_price = state["price"]

            if USE_REAL_API:
                new_price = fetch_real_price()
            else:
                new_price = fetch_simulated_price(old_price)

            print(
                f"[price_updater_loop] قیمت گرفته شد: {new_price}"
            )

            change_percent = 0.0

            if old_price:

                change_percent = round(
                    (new_price - old_price)
                    / old_price
                    * 100,
                    3
                )

            update_daily_tracking(new_price)

            now_iso = datetime.now(
                timezone.utc
            ).isoformat()

            with state_lock:

                state["price"] = new_price

                state["change_percent"] = (
                    change_percent
                )

                state["updated_at"] = (
                    now_iso
                )

                state["source"] = (
                    "real_api"
                    if USE_REAL_API
                    else "simulator"
                )

                state["today_open"] = (
                    daily.get("today_open")
                )

                state["yesterday_close"] = (
                    daily.get("yesterday_close")
                )

                state["today_high"] = (
                    daily.get("today_high")
                )

                state["today_low"] = (
                    daily.get("today_low")
                )

                price_history.append({
                    "t": now_iso,
                    "p": new_price
                })

            check_and_fire_alerts(
                new_price
            )

        except Exception as e:

            print(
                f"[price_updater_loop] error: {e}"
            )

        time.sleep(
            POLL_INTERVAL_SECONDS
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/price")
def api_price():

    with state_lock:

        payload = dict(state)

        payload["history"] = list(
            price_history
        )

    return jsonify(payload)


@app.route("/api/vapid-public-key")
def api_vapid_public_key():

    return jsonify({
        "key": get_vapid_public_key_b64()
    })


@app.route("/api/subscribe", methods=["POST"])
def api_subscribe():

    body = request.get_json(
        force=True,
        silent=True
    ) or {}

    subscription_info = body.get(
        "subscription"
    )

    alerts = body.get(
        "alerts",
        []
    )

    if (
        not subscription_info
        or "endpoint" not in subscription_info
    ):

        return jsonify({
            "ok": False,
            "error": "invalid subscription"
        }), 400

    with subscriptions_lock:

        subscriptions[:] = [
            s for s in subscriptions
            if s["subscription"]["endpoint"]
            != subscription_info["endpoint"]
        ]

        subscriptions.append({
            "subscription": subscription_info,
            "alerts": alerts
        })

        save_subscriptions(
            subscriptions
        )

    return jsonify({
        "ok": True
    })


@app.route("/api/unsubscribe", methods=["POST"])
def api_unsubscribe():

    body = request.get_json(
        force=True,
        silent=True
    ) or {}

    endpoint = body.get(
        "endpoint"
    )

    with subscriptions_lock:

        subscriptions[:] = [
            s for s in subscriptions
            if s["subscription"]["endpoint"]
            != endpoint
        ]

        save_subscriptions(
            subscriptions
        )

    return jsonify({
        "ok": True
    })


# ---------------------------------------------------------------------------
# Start background thread
# ---------------------------------------------------------------------------

_updater_thread = threading.Thread(
    target=price_updater_loop,
    daemon=True
)

_updater_thread.start()


if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=True,
        use_reloader=False
    )
```

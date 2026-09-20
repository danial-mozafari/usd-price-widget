# 💵 DollarLive

**Real-time USD/Toman price tracker** — a free Progressive Web App with a live
price chart, custom price alerts, and real push notifications, built to stay
online and reachable without a VPN.

🔗 **Live app:** https://danial-mozafari.github.io/usd-price-widget/

![DollarLive screenshot](docs/Screenshot%202026-09-17%20220119.png)

---

## ✨ Features

- **Live price** — updated automatically, with an up/down colored indicator
- **Live chart** — drawn entirely with the raw Canvas API (no charting
  library), showing price and time on hover/tap, colored by trend
- **Daily stats** — today's open, today's high/low, yesterday's close
- **Custom price alerts** — "notify me when price goes above/below X"
- **Real push notifications** — delivered by the browser/OS via Web Push
  (VAPID), even when the app is completely closed
- **Installable PWA** — works like a native app on Android and iOS home
  screens
- **Dark / light themes** — iOS-inspired glass design

## 🏗️ Architecture

This project is intentionally split into two independently deployed pieces,
so that the whole thing keeps working even where individual hosting
providers get blocked or rate-limited:

```
docs/            → frontend (HTML/CSS/JS), served by GitHub Pages
vercel-backend/  → backend (Python/Flask), deployed as Vercel serverless
                   functions
```

| Layer          | Service         | Why                                             |
|----------------|-----------------|--------------------------------------------------|
| Frontend       | GitHub Pages    | Free, reliably reachable, zero config            |
| Backend        | Vercel (Python) | Free serverless functions, no "sleeping" server  |
| Database       | Supabase        | Free Postgres, stores price history, daily stats, and push subscriptions |
| Scheduler      | cron-job.org    | Pings `/api/tick` every ~15 min to fetch a fresh price and check alerts |
| Price source   | BrsApi.ir       | Free USD/IRT market data API                     |
| Notifications  | Web Push (VAPID)| Real OS-level notifications, no native app needed |

Because the backend is serverless (no long-running process), **all state**
(current price, price history, daily open/high/low, push subscriptions)
lives in Supabase instead of in memory — every function call reads and
writes from there.

## 📁 Project structure

```
docs/
  index.html          the single page (mobile + desktop responsive)
  style.css            glass/iOS-inspired dark & light themes
  main.js              price polling, chart rendering, alerts, push subscribe
  manifest.json         PWA manifest
  service-worker.js     receives Web Push events, shows notifications
  icon-192.png / icon-512.png

vercel-backend/
  api/index.py          Flask app: all routes below
  vercel.json            region pinned to fra1 (needed to reach BrsApi.ir reliably)
  requirements.txt
```

## 🔌 API routes (backend)

| Route                    | Method | Purpose                                             |
|---------------------------|--------|------------------------------------------------------|
| `/api/price`              | GET    | Returns current price, history, and daily stats     |
| `/api/tick`                | GET    | Fetches a fresh price, updates Supabase, fires alerts (called by cron-job.org) |
| `/api/vapid-public-key`    | GET    | Returns the VAPID public key for push subscription   |
| `/api/subscribe`           | POST   | Registers/updates a device's push subscription + alerts |
| `/api/unsubscribe`         | POST   | Removes a device's push subscription                 |

## ⚙️ Running your own copy

**Backend (Vercel):**
1. Fork this repo, create a new Vercel project with **Root Directory** set to
   `vercel-backend`
2. Add these environment variables:
   - `USE_REAL_API=1`
   - `BRSAPI_KEY` — a free key from [brsapi.ir](https://brsapi.ir)
   - `SUPABASE_URL`, `SUPABASE_SECRET_KEY` — from your own Supabase project
   - `VAPID_PRIVATE_KEY_PEM` — generate once (the app can generate one for
     you on first run and print it to the logs), then pin it as an env var
     so it never changes between deploys
3. In Supabase, create this table:
   ```sql
   create table if not exists kv_store (
     key text primary key,
     value jsonb not null,
     updated_at timestamptz not null default now()
   );
   ```
4. Point a free scheduler (e.g. [cron-job.org](https://cron-job.org)) at
   `https://<your-vercel-domain>/api/tick` every 10–15 minutes

**Frontend (GitHub Pages):**
1. Enable GitHub Pages on this repo, source: `main` branch, `/docs` folder
2. In `docs/main.js`, set `API_BASE` to your own Vercel backend URL

## ⚠️ Known limitations

- **iOS Web Push** is a relatively new and still-inconsistent platform
  feature (iOS 16.4+, installed home-screen app only). Notifications work,
  but iOS occasionally delivers them with a delay outside Claude's control.
- Vercel's free plan runs functions in a single region (`fra1` here) — this
  was chosen because it reliably reaches BrsApi.ir; other regions may time
  out.

## 🛠️ Built with

Python · Flask · Supabase · Vercel · GitHub Pages · Web Push (VAPID) ·
vanilla HTML/CSS/JS (no frontend framework, no chart library)

---

Made by [Danial Mozafari](https://danial-mozafari.github.io/danial-mozafari/)

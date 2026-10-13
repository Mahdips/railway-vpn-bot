# نسخه Railway ربات فروش سرویس (Container Edition)

[![زبان: فارسی](https://img.shields.io/badge/%D8%B2%D8%A8%D8%A7%D9%86-%D9%81%D8%A7%D8%B1%D8%B3%DB%8C-blue)](#%D9%81%D8%A7%D8%B1%D8%B3%DB%8C)
[![language: English](https://img.shields.io/badge/language-English-blue)](#english)

---

## English

A Telegram bot for selling services (subscriptions, game accounts, virtual
services) with an admin panel built into the bot: order review, receipt
approval, broadcast, user management, discount codes, an AI "secretary" for
customer replies, and a trial-account pool.

This is the **container edition** for Railway and any single-port PaaS. The
same bot ships as a VPS/systemd edition for a virtual machine — they are
kept separate on purpose; do not mix their deployment files.

What changed for the container:

1. `BOT_TOKEN` comes from the environment and the process exits with a clear
   message when it is missing (a container has no interactive console).
2. A tiny HTTP server runs on `$PORT` so the platform health check passes —
   the bot is long-polling and opens no port of its own.
3. Every data file lives under `DATA_DIR` so it can be mounted on a volume.
   **Without a volume, every redeploy wipes orders, users, and settings.**
4. `ADMIN_ID` has **no default**. A hardcoded fallback would silently route
   another operator's order notifications and receipts to a stranger.

### Deploy

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Push this folder to a GitHub repo (public or private).
3. In Railway: **New Project → Deploy from GitHub repo**.
4. Under **Variables** add:
   - `BOT_TOKEN` — the token from BotFather
   - `ADMIN_ID` — the numeric Telegram ID of the primary admin (get it from
     [@userinfobot](https://t.me/userinfobot)); required, the bot will not
     start without it
   - `DATA_DIR` — `/data`
   - optional: `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL` for the AI
     secretary
5. Under **Settings → Volumes** create a volume mounted at `/data`.
6. When the deploy finishes, the logs should read `Bot is running...`.

If the logs say `ADMIN_ID تنظیم نشده` or `BOT_TOKEN تنظیم نشده`, a required
variable is missing.

---

## فارسی

این پوشه‌ی استقرار ربات روی **Railway** (یا هر PaaS تک‌پورتی) است. کد اصلی
`( newfile.py` بدون تغییر در منطقbusiness ‌باقی مانده است؛ فقط چند چیز برای محیط
کانتینر اضافه/تغییر کرده:

1. `BOT_TOKEN` از متغیرهای محیطی خوانده می‌شود و به جای `input()` تعاملی، در صورت
   نبودن آن برنامه با یک پیام واضح متوقف می‌شود (کانتینر کنسول تعاملی ندارد).
2. یک سرور HTTP بسیار کوچک روی پورت `PORT$` اجرا می‌شود تا چک سلامت Railway پاس
   شود؛ ربات polling هیچ پورتی باز نمی‌کند.
3. تمام فایل‌های داده به مسیر `DATA_DIR` منتقل شده‌اند تا روی یک Volume سوار شوند.
   **بدون این Volume، هر استقرار جدید تمام سفارش‌ها، کاربران و تنظیمات را پاک می‌کند.**
4. `ADMIN_ID` **پیش‌فرض ندارد**. یک مقدار ثابت باعث می‌شد سفارش‌ها و رسیدهای
   یک اپراتور دیگر بی‌صدا به حساب تلگرام یک غریبه ارسال شود.

## فایل‌ها

| فایل | کاربرد |
| --- | --- |
| `bot.py` | کد کامل ربات (نسخه‌ی کانتینر، بدون تغییر منطق) |
| `requirements.txt` | فقط `python-telegram-bot==22.8` (همه‌ی بقیه‌ی نیازها stdlib هستند) |
| `Dockerfile` | ایمیج `python:3.12-slim` — نیازی به وب‌سرور واقعی نیست |
| `railway.toml` | تنظیمات restart policy و چک سلامت TCP |
| `.dockerignore` | جلوگیری از آپلود کش، `.env` و فایل‌های داده‌ی لوکال |

## مراحل استقرار روی Railway

1. در BotFather یک توکن جدید بسازید (اگر توکن قبلی جایی لو رفته، حتماً `revoke`
   کنید — دکمه‌ی Revoke Token در BotFather).
2. یک GitHub repo خصوصی بسازید و **فقط محتوای همین پوشه** را در آن push کنید
   (نه کل پوشه‌ی «ربات تموم شده»).
3. در Railway: `New Project → Deploy from GitHub repo` → ریپو را انتخاب کنید.
4. به بخش **Variables** بروید و این‌ها را اضافه کنید:
   - `BOT_TOKEN` = توکن جدید از BotFather
   - `ADMIN_ID` = شناسه عددی تلگرام ادمین (الان پیش‌فرض ندارد؛ باید حتماً تنظیم کنی)
   - `DATA_DIR` = `/data`
   - (اختیاری) `OPENAI_API_KEY`، `OPENAI_BASE_URL`، `OPENAI_MODEL` برای منشی هوش مصنوعی
5. به بخش **Settings → Volumes** بروید، یک Volume جدید بسازید و مسیر Mount آن را
   `/data` قرار دهید. بدون این مرحله، داده‌ها با هر استقرار از بین می‌روند.
6. Railway به‌طور خودکار build می‌کند. در قسمت **Deployments → Logs** باید این را
   ببینید:
   ```
   Bot is running. Stop with Ctrl+C or the stop button in Pydroid.
   ```
   اگر به‌جای آن خطای `BOT_TOKEN تنظیم نشده` بود، متغیر محیطی را چک کنید.

## چک سلامت

Railway فقط یک پورت عمومی می‌دهد. ربات از نوع long-polling است و پورتی باز نمی‌کند،
بنابراین یک endpoint سبک روی `PORT$` پاسخ `bot:polling` می‌دهد. این فقط برای چک
سلامت است و نباید آدرس عمومی بگیرد؛ اگر دامنه‌ای Generate کردید، مشکلی نیست اما
کاربردی ندارد — می‌توانید در تنظیمات Networking دامنه را حذف کنید.

## پشتیبانی از پروکسی

اگر خروجی شبکه به `api.telegram.org` مسدود باشد، در Logs خطای `NetworkError` یا
`Timed out` می‌بینید. در این حالت ربات خودش retry می‌کند؛ اگر ادامه‌دار بود، باید
از یک سرویس دیگر (نه این کانتینر) استفاده کنید. این نسخه پروکسی را پشتیبانی
نمی‌کند چون تلگرام polling در China/Iran به‌طور معمول از داخل کانتینر Railway
(خارج از ایران) مسدود نیست.

## اعمال تغییرات

برای تغییر توکن یا متغیرها: کافی است در داشبورد Railway متغیر را ویرایش کنید و
یک Redeploy بزنید. نیازی به تغییر کد نیست.

## توجه

- هیچ فایل `.json` داده نباید در ریپو commit شود (`.dockerignore` آن را نادیده
  می‌گیرد، ولی باز هم در git نگذارید).
- هیچ توکنی در کد نیست؛ همه چیز از متغیرهای محیطی می‌آید.

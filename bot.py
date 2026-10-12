# -*- coding: utf-8 -*-
"""
Telegram service-sales bot for Pydroid 3.
Library: python-telegram-bot 22.x
Set BOT_TOKEN below on your own device. Never share it publicly.
"""

import json
import logging
import os
import asyncio
import threading
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InputMediaPhoto,
)
from telegram.error import Forbidden, BadRequest, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    TypeHandler,
    filters,
)

# ====== SETTINGS ======
# Set these in your environment or replace BOT_TOKEN locally with a NEW token.
# The token previously included in the uploaded file should be revoked in @BotFather.
BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_NEW_BOT_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8928341236"))
DB_FILE = (_DATA_DIR := Path(os.getenv("DATA_DIR") or Path(__file__).parent)) / "orders.json"
MARKET_FILE = _DATA_DIR / "game_account_listings.json"
SECRETARY_FILE = _DATA_DIR / "secretary_settings.json"
ADMINS_FILE = _DATA_DIR / "admins.json"
DISCOUNT_FILE = _DATA_DIR / "discount_settings.json"
USERS_FILE = _DATA_DIR / "users.json"
ACCESS_FILE = _DATA_DIR / "access_settings.json"
TEST_FILE = _DATA_DIR / "test_accounts.json"
# The container filesystem is replaced on every redeploy, so DATA_DIR must point
# at a mounted volume for orders, users, and settings to survive.
_DATA_DIR.mkdir(parents=True, exist_ok=True)
# Serializes test-pool updates so two nearly simultaneous requests cannot take the same link.
TEST_ACCOUNTS_LOCK = asyncio.Lock()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1/chat/completions").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Public prices seen in the screenshots. Verify/update these before accepting payments.
PRODUCTS = {
    "🎮 سرور تانل گیمینگ VIP": [
        ("هر گیگابایت", 8000),
    ],
    "🇹🇷 سرور ترکیه VIP": [
        ("هر گیگابایت", 6000),
    ],
    "🌐 سرور وب‌گردی V2Ray": [
        ("هر گیگابایت", 5000),
    ],
}

MAIN_MENU = [
    ["🚀 خرید سرویس جدید", "🧪 اکانت تست"],
    ["💰 تعرفه سرویس‌ها"],
    ["📱 سرویس‌های من", "♻️ تمدید سرویس"],
    ["💡 آموزش استفاده", "💰 اعتبار من"],
    ["🎗️ کاربران برتر", "👥 معرفی به دوستان"],
    ["🧑🏻‍💻 تماس با پشتیبانی"],
    ["🤝 درخواست نمایندگی"],
    ["🎮 شاپ اکانت بازی‌ها"],
    ["💎 خدمات مجازی"],
    ["🔙 بازگشت به انتخاب سرور", "🔙 منوی اصلی"],
]
MAIN_MARKUP = ReplyKeyboardMarkup(
    MAIN_MENU, resize_keyboard=True, one_time_keyboard=True, is_persistent=False
)




def load_admin_ids():
    """Return primary admin plus extra admin IDs saved in admins.json."""
    ids = {ADMIN_ID}
    if ADMINS_FILE.exists():
        try:
            with ADMINS_FILE.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            ids.update(int(value) for value in payload.get("admin_ids", []) if str(value).isdigit())
        except Exception:
            logger.exception("Could not read admins.json; using primary admin only")
    return ids


def save_admin_ids(ids):
    ids = {int(value) for value in ids}
    ids.add(ADMIN_ID)  # The primary admin cannot be removed.
    tmp = ADMINS_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump({"admin_ids": sorted(ids)}, f, ensure_ascii=False, indent=2)
    tmp.replace(ADMINS_FILE)


def is_admin(user_id):
    return int(user_id) in load_admin_ids()


def load_db():
    if not DB_FILE.exists():
        return {"next_id": 1001, "orders": {}}
    try:
        with DB_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if "next_id" not in data or "orders" not in data:
            raise ValueError("Invalid database format")
        # شماره نمایشی سفارش برای همه مشتریان به‌صورت سراسری و پیوسته است: 1، 2، 3...
        # شناسه داخلی id جدا می‌ماند تا سفارش‌های قبلی و دستورات ادمین خراب نشوند.
        changed = False
        all_orders = sorted(data["orders"].values(), key=lambda item: int(item.get("id", 0)))
        for number, order in enumerate(all_orders, start=1):
            if order.get("order_number") != number:
                order["order_number"] = number
                changed = True
        if changed:
            save_db(data)
        return data
    except Exception:
        logger.exception("Could not read orders.json")
        raise RuntimeError(
            "فایل orders.json قابل خواندن نیست. برای جلوگیری از حذف سفارش‌ها، "
            "فایل را پاک نکنید؛ محتوای آن را بررسی کنید."
        )


def save_db(data):
    tmp = DB_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(DB_FILE)


def money(amount):
    return f"{amount:,} تومان"


def load_discount_percent():
    """Read the global config/service discount percentage (0..100)."""
    if not DISCOUNT_FILE.exists():
        return 0
    try:
        with DISCOUNT_FILE.open("r", encoding="utf-8") as f:
            value = int(json.load(f).get("percent", 0))
        return max(0, min(100, value))
    except Exception:
        logger.exception("Could not read discount_settings.json; using no discount")
        return 0


def save_discount_percent(percent):
    tmp = DISCOUNT_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump({"percent": int(percent)}, f, ensure_ascii=False, indent=2)
    tmp.replace(DISCOUNT_FILE)


def discounted_price(price, percent=None):
    if percent is None:
        percent = load_discount_percent()
    return round(int(price) * (100 - int(percent)) / 100)


def load_users():
    """Load users known to the bot, including users found in historical orders."""
    ids = set()
    if USERS_FILE.exists():
        try:
            with USERS_FILE.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            ids.update(int(x) for x in payload.get("user_ids", []) if str(x).isdigit())
        except Exception:
            logger.exception("Could not read users.json")
    try:
        db = load_db()
        ids.update(int(o["user_id"]) for o in db.get("orders", {}).values()
                   if str(o.get("user_id", "")).isdigit())
    except Exception:
        logger.exception("Could not recover users from orders")
    return ids


def remember_user(user_id):
    ids = load_users()
    ids.add(int(user_id))
    tmp = USERS_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump({"user_ids": sorted(ids)}, f, ensure_ascii=False, indent=2)
    tmp.replace(USERS_FILE)


def load_access_data():
    defaults = {"blocked_users": [], "banned_until": {}, "maintenance": False}
    if not ACCESS_FILE.exists():
        return defaults
    try:
        with ACCESS_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        defaults.update(data if isinstance(data, dict) else {})
        defaults["blocked_users"] = [int(x) for x in defaults.get("blocked_users", [])]
        defaults["banned_until"] = defaults.get("banned_until", {})
        defaults["maintenance"] = bool(defaults.get("maintenance", False))
        return defaults
    except Exception:
        logger.exception("Could not read access_settings.json")
        return defaults


def save_access_data(data):
    tmp = ACCESS_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(ACCESS_FILE)


async def access_gate(update, context):
    """Return True when an ordinary user's request should be stopped."""
    user = update.effective_user
    message = update.effective_message
    if not user or not message or is_admin(user.id):
        return False
    data = load_access_data()
    if user.id in data.get("blocked_users", []):
        await message.reply_text("⛔ دسترسی شما به این ربات مسدود شده است.")
        return True
    until = data.get("banned_until", {}).get(str(user.id))
    if until:
        try:
            expiry = datetime.fromisoformat(until)
            if datetime.now() < expiry:
                await message.reply_text(f"⛔ دسترسی شما تا {expiry.strftime('%Y-%m-%d %H:%M')} محدود شده است.")
                return True
            data["banned_until"].pop(str(user.id), None)
            save_access_data(data)
        except Exception:
            data["banned_until"].pop(str(user.id), None)
            save_access_data(data)
    if data.get("maintenance"):
        await message.reply_text("🛠 ربات موقتاً در حال بروزرسانی است. لطفاً بعداً دوباره تلاش کنید.")
        return True
    return False



async def senduser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a private message to one user by numeric Telegram chat ID."""
    message = update.effective_message
    actor = update.effective_user
    if not message or not actor:
        return
    if not is_admin(actor.id):
        await message.reply_text("⛔ این دستور فقط برای ادمین است.")
        return
    if len(context.args) < 2 or not context.args[0].isdigit():
        await message.reply_text(
            "فرمت صحیح:\n/senduser آیدی_عددی_کاربر متن پیام\n\n"
            "مثال:\n/senduser 123456789 سلام، سفارش شما بررسی شد."
        )
        return
    target_id = int(context.args[0])
    text = " ".join(context.args[1:]).strip()
    if target_id <= 0 or not text:
        await message.reply_text("آیدی کاربر یا متن پیام معتبر نیست.")
        return
    try:
        await context.bot.send_message(chat_id=target_id, text=text)
    except Forbidden:
        await message.reply_text(
            "❌ پیام ارسال نشد. احتمالاً کاربر ربات را شروع نکرده یا ربات را مسدود کرده است. "
            "از کاربر بخواه وارد ربات شود و /start بزند."
        )
    except BadRequest as exc:
        logger.warning("senduser BadRequest target=%s: %s", target_id, exc)
        await message.reply_text(
            "❌ تلگرام پیام را نپذیرفت. آیدی عددی را بررسی کن و مطمئن شو کاربر قبلاً ربات را شروع کرده است."
        )
    except TelegramError as exc:
        logger.exception("senduser TelegramError target=%s", target_id)
        await message.reply_text(f"❌ خطای تلگرام هنگام ارسال پیام: {exc}")
    except Exception:
        logger.exception("Unexpected senduser failure target=%s", target_id)
        await message.reply_text("❌ خطای غیرمنتظره رخ داد. گزارش خطا در کنسول ربات ثبت شد.")
    else:
        await message.reply_text(f"✅ پیام با موفقیت برای کاربر {target_id} ارسال شد.")


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین است.")
        return
    message_text = " ".join(context.args).strip()
    if not message_text:
        await update.effective_message.reply_text("فرمت: /broadcast متن پیام")
        return
    user_ids = sorted(load_users())
    if not user_ids:
        await update.effective_message.reply_text("هنوز کاربری برای ارسال پیام ثبت نشده است.")
        return
    success = failed = 0
    for user_id in user_ids:
        if user_id in load_access_data().get("blocked_users", []):
            continue
        try:
            await context.bot.send_message(chat_id=user_id, text=message_text)
            success += 1
        except Exception:
            failed += 1
            logger.warning("Broadcast failed for user_id=%s", user_id)
        await asyncio.sleep(0.04)
    await update.effective_message.reply_text(f"📣 ارسال همگانی تمام شد.\n✅ موفق: {success}\n❌ ناموفق: {failed}")


async def block_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین است.")
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.effective_message.reply_text("فرمت: /block آیدی_عددی_کاربر")
        return
    target = int(context.args[0])
    if target == ADMIN_ID or is_admin(target):
        await update.effective_message.reply_text("نمی‌توان ادمین را مسدود کرد.")
        return
    data = load_access_data()
    if target not in data["blocked_users"]:
        data["blocked_users"].append(target)
    save_access_data(data)
    await update.effective_message.reply_text(f"⛔ کاربر {target} مسدود شد.")


async def unblock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین است.")
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.effective_message.reply_text("فرمت: /unblock آیدی_عددی_کاربر")
        return
    target = int(context.args[0])
    data = load_access_data()
    data["blocked_users"] = [x for x in data["blocked_users"] if x != target]
    save_access_data(data)
    await update.effective_message.reply_text(f"✅ مسدودیت کاربر {target} برداشته شد.")


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین است.")
        return
    if len(context.args) != 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await update.effective_message.reply_text("فرمت: /ban آیدی_کاربر مدت_دقیقه\nمثال: /ban 123456789 60")
        return
    target, minutes = int(context.args[0]), int(context.args[1])
    if target == ADMIN_ID or is_admin(target) or minutes < 1 or minutes > 525600:
        await update.effective_message.reply_text("آیدی نامعتبر است یا مدت باید بین 1 تا 525600 دقیقه باشد.")
        return
    data = load_access_data()
    data["banned_until"][str(target)] = datetime.now().isoformat(timespec="minutes") 
    from datetime import timedelta
    data["banned_until"][str(target)] = (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="minutes")
    save_access_data(data)
    await update.effective_message.reply_text(f"⏳ کاربر {target} به مدت {minutes} دقیقه محدود شد.")


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین است.")
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.effective_message.reply_text("فرمت: /unban آیدی_عددی_کاربر")
        return
    data = load_access_data()
    data["banned_until"].pop(str(int(context.args[0])), None)
    save_access_data(data)
    await update.effective_message.reply_text("✅ محدودیت موقت کاربر برداشته شد.")


async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین است.")
        return
    data = load_access_data()
    data["maintenance"] = True
    save_access_data(data)
    await update.effective_message.reply_text("🛠 حالت بروزرسانی فعال شد؛ کاربران عادی پیام اطلاع‌رسانی می‌بینند.")


async def online_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای ادمین است.")
        return
    data = load_access_data()
    data["maintenance"] = False
    save_access_data(data)
    await update.effective_message.reply_text("✅ ربات به حالت عادی برگشت.")


def user_label(user):
    name = " ".join(x for x in [user.first_name, user.last_name] if x) or "بدون نام"
    username = f"@{user.username}" if user.username else "ندارد"
    return f"{name}\nیوزرنیم: {username}\nآیدی عددی: {user.id}"


def order_text(order):
    discount_line = ""
    if order.get("discount_percent", 0):
        discount_line = (
            f"\n🏷 تخفیف: {order['discount_percent']}٪"
            f"\n💵 مبلغ قبل از تخفیف: {money(order.get('original_amount', order['amount']))}"
            f"\n💸 مبلغ تخفیف: {money(order.get('discount_amount', 0))}"
        )
    return (
        f"🧾 سفارش شماره #{order['id']}\n"
        f"👤 مشتری: {order['customer_name']}\n"
        f"یوزرنیم: {order['username']}\n"
        f"آیدی عددی: {order['user_id']}\n"
        f"🌐 سرویس: {order['panel']}\n"
        f"📦 محصول: {order['product']}\n"
        f"🔢 تعداد: {order['quantity']}\n"
        f"💵 مبلغ: {money(order['amount'])}{discount_line}\n"
        f"📌 وضعیت: {order['status']}\n"
        f"🕒 زمان ثبت: {order['created_at']}"
    )


async def cancel_active_order(user_id, context):
    """Cancel the user's current unpaid order safely, if one exists."""
    order_id = context.user_data.get("active_receipt_order")
    if order_id is not None:
        db = load_db()
        order = db.get("orders", {}).get(str(order_id))
        if order and int(order.get("user_id", -1)) == int(user_id) and order.get("status") in ("در انتظار رسید", "رسید ارسال‌شده"):
            order["status"] = "لغو‌شده توسط مشتری"
            order["cancelled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_db(db)
    context.user_data.pop("active_receipt_order", None)


async def global_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # Returning to the main menu cancels an unpaid order so it cannot remain active accidentally.
    await cancel_active_order(query.from_user.id, context)
    context.user_data.pop("quantity_waiting", None)
    context.user_data.pop("pending_order", None)
    context.user_data.pop("market_flow", None)
    context.user_data.pop("panel", None)
    context.user_data.pop("product", None)
    context.user_data.pop("unit_price", None)
    await query.message.reply_text("به منوی اصلی برگشتید.", reply_markup=MAIN_MARKUP)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user:
        remember_user(update.effective_user.id)
    if await access_gate(update, context):
        return
    context.user_data.clear()
    await update.message.reply_text(
        "سلام، خوش آمدید 🌟\nاز منوی زیر گزینه موردنظرتان را انتخاب کنید.",
        reply_markup=MAIN_MARKUP,
    )


async def show_tariffs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    percent = load_discount_percent()
    lines = ["💰 تعرفه سرویس‌ها\n"]
    if percent:
        lines.append(f"🏷 تخفیف فعال: {percent}٪\n")
    for panel, options in PRODUCTS.items():
        lines.append(f"\n{panel}")
        for product, price in options:
            final_price = discounted_price(price, percent)
            if percent:
                lines.append(f"• {product}: قیمت عادی {money(price)} | با تخفیف {money(final_price)}")
            else:
                lines.append(f"• {product}: {money(price)}")
    await update.message.reply_text("\n".join(lines), reply_markup=MAIN_MARKUP)


def server_selection_markup():
    keyboard = [[InlineKeyboardButton(name, callback_data=f"panel:{i}")]
                for i, name in enumerate(PRODUCTS)]
    keyboard.append([InlineKeyboardButton("🔙 بازگشت به منو", callback_data="back")])
    return InlineKeyboardMarkup(keyboard)


async def begin_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🌍 لطفاً سرور موردنظر را انتخاب کنید:",
        reply_markup=server_selection_markup(),
    )


async def panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "back":
        await query.message.reply_text("به منوی اصلی برگشتید.", reply_markup=MAIN_MARKUP)
        return

    if data.startswith("panel:"):
        panels = list(PRODUCTS)
        idx = int(data.split(":")[1])
        if idx < 0 or idx >= len(panels):
            await query.message.reply_text("گزینه نامعتبر است.")
            return
        context.user_data["panel"] = panels[idx]
        percent = load_discount_percent()
        keyboard = [
            [InlineKeyboardButton(
                f"{name} — {money(discounted_price(price, percent))}" + (f" (تخفیف {percent}٪)" if percent else ""),
                callback_data=f"product:{i}"
            )]
            for i, (name, price) in enumerate(PRODUCTS[panels[idx]])
        ]
        keyboard.append([InlineKeyboardButton("🔙 انتخاب پنل دیگر", callback_data="back_to_panels")])
        await query.message.reply_text(
            f"📦 محصول مورد نظر را انتخاب کنید:\n\n{panels[idx]}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if data == "back_to_panels":
        await query.message.reply_text(
            "🌍 لطفاً سرور موردنظر را انتخاب کنید:",
            reply_markup=server_selection_markup(),
        )
        return

    if data.startswith("product:"):
        panel = context.user_data.get("panel")
        if panel not in PRODUCTS:
            await query.message.reply_text("از منوی اصلی دوباره خرید را شروع کنید.")
            return
        idx = int(data.split(":")[1])
        options = PRODUCTS[panel]
        if idx < 0 or idx >= len(options):
            await query.message.reply_text("گزینه نامعتبر است.")
            return
        product, unit_price = options[idx]
        context.user_data["product"] = product
        context.user_data["unit_price"] = unit_price
        context.user_data["quantity_waiting"] = True
        if product == "هر گیگابایت":
            prompt = "⚠️ توجه: حداقل خرید سرویس ۱۰ گیگابایت و حداکثر ۲۰۰ گیگابایت است.\n\nحجم موردنظر را با عدد انگلیسی بین 10 تا 200 بفرستید. مثال: 10"
        else:
            prompt = "لطفاً تعداد موردنظر را وارد کنید (فعلاً از 1 تا 10)."
        await query.message.reply_text(prompt)
        return


VIRTUAL_PRODUCTS = [
    ("تلگرام پرمیوم — ۱ ماه", 1200000),
    ("تلگرام پرمیوم — ۳ ماه", 3900000),
    ("تلگرام پرمیوم — ۶ ماه", 8000000),
]


async def virtual_service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        index = int(query.data.split(":", 1)[1])
        if index < 0 or index >= len(VIRTUAL_PRODUCTS):
            raise IndexError("virtual product index out of range")
        product, amount = VIRTUAL_PRODUCTS[index]
    except (ValueError, IndexError):
        await query.message.reply_text("گزینه معتبر نیست؛ دوباره از منو انتخاب کنید.")
        return
    user = query.from_user
    db = load_db()
    order_id = db["next_id"]
    db["next_id"] += 1
    order_number = 1 + max((int(o.get("order_number", 0)) for o in db["orders"].values()), default=0)
    order = {
        "id": order_id, "order_number": order_number, "user_id": user.id,
        "customer_name": " ".join(x for x in [user.first_name, user.last_name] if x) or "بدون نام",
        "username": f"@{user.username}" if user.username else "ندارد",
        "panel": "💎 خدمات مجازی", "product": product, "quantity": 1,
        "amount": amount, "original_amount": amount, "discount_percent": 0,
        "discount_amount": 0, "status": "در انتظار رسید", "receipt_file_id": None,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reviewed_by": None, "config_sent": False,
    }
    db["orders"][str(order_id)] = order
    save_db(db)
    context.user_data["active_receipt_order"] = order_id
    await query.message.reply_text(
        f"🧾 خلاصه سفارش خدمات مجازی\n\n💎 محصول: {product}\n💵 مبلغ قابل پرداخت: {money(amount)}\n\n"
        "💳 اطلاعات پرداخت:\n"
        "شماره کارت: 5022291620713105\n"
        "به نام: محمد امین یوسفی\n\n"
        "پس از پرداخت، عکس رسید را همین‌جا ارسال کنید تا برای ادمین ارسال شود.\n\n"
        "اگر منصرف شدید، دکمه «❌ لغو خرید» را بزنید.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ لغو خرید", callback_data=f"cancel_active_order:{order_id}")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="global_back")],
        ]),
    )


async def handle_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("quantity_waiting"):
        return False
    raw = (update.message.text or "").strip()
    try:
        quantity = int(raw)
    except ValueError:
        await update.message.reply_text("لطفاً فقط عدد صحیح انگلیسی بفرستید.")
        return True
    panel = context.user_data.get("panel")
    product = context.user_data.get("product")
    unit_price = context.user_data.get("unit_price")
    if panel not in PRODUCTS or not product or not isinstance(unit_price, int):
        context.user_data.pop("quantity_waiting", None)
        await update.message.reply_text("اطلاعات خرید پیدا نشد؛ دوباره از منو شروع کنید.")
        return True

    if product == "هر گیگابایت":
        if quantity < 10 or quantity > 200:
            await update.message.reply_text(
                "❌ حجم انتخابی نامعتبر است.\n\n"
                "⚠️ حداقل خرید ۱۰ گیگابایت و حداکثر خرید ۲۰۰ گیگابایت است.\n"
                "لطفاً عددی بین 10 تا 200 وارد کنید."
            )
            return True
    elif quantity < 1 or quantity > 10:
        await update.message.reply_text("تعداد باید بین 1 تا 10 باشد.")
        return True

    context.user_data["quantity_waiting"] = False
    original_amount = quantity * unit_price
    discount_percent = load_discount_percent()
    amount = discounted_price(original_amount, discount_percent)
    discount_amount = original_amount - amount
    context.user_data["pending_order"] = {
        "panel": panel,
        "product": product,
        "quantity": quantity,
        "amount": amount,
        "original_amount": original_amount,
        "discount_percent": discount_percent,
        "discount_amount": discount_amount,
    }
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ ثبت سفارش و ارسال رسید", callback_data="confirm_order")],
        [InlineKeyboardButton("❌ لغو خرید", callback_data="cancel_order")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="global_back")],
    ])
    await update.message.reply_text(
        f"🧾 خلاصه سفارش\n\n"
        f"🌐 سرویس: {panel}\n"
        f"📦 محصول: {product}\n"
        f"🔢 تعداد/حجم: {quantity}\n"
        + (f"💵 مبلغ قبل از تخفیف: {money(original_amount)}\n🏷 تخفیف: {discount_percent}٪ (کم‌شده: {money(discount_amount)})\n" if discount_percent else "")
        + f"💵 مبلغ قابل پرداخت: {money(amount)}\n\n"
        f"💳 اطلاعات پرداخت:\n"
        f"شماره کارت: 5022291620713105\n"
        f"به نام: محمد امین یوسفی\n\n"
        f"اگر اطلاعات درست است، ثبت سفارش را بزنید. سپس رسید پرداخت را به صورت عکس ارسال کنید.",
        reply_markup=keyboard,
    )
    return True


async def order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel_order":
        context.user_data.pop("pending_order", None)
        await query.message.reply_text("سفارش لغو شد.", reply_markup=MAIN_MARKUP)
        return
    if query.data != "confirm_order":
        return
    pending = context.user_data.get("pending_order")
    if not pending:
        await query.message.reply_text("سفارشی برای ثبت پیدا نشد؛ دوباره خرید کنید.")
        return

    user = query.from_user
    db = load_db()
    order_id = db["next_id"]
    db["next_id"] += 1
    order_number = 1 + max(
        (int(o.get("order_number", 0)) for o in db["orders"].values()), default=0
    )
    order = {
        "id": order_id,
        "order_number": order_number,
        "user_id": user.id,
        "customer_name": " ".join(x for x in [user.first_name, user.last_name] if x) or "بدون نام",
        "username": f"@{user.username}" if user.username else "ندارد",
        **pending,
        "status": "در انتظار رسید",
        "receipt_file_id": None,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reviewed_by": None,
        "config_sent": False,
    }
    db["orders"][str(order_id)] = order
    save_db(db)
    context.user_data["active_receipt_order"] = order_id
    context.user_data.pop("pending_order", None)
    await query.message.reply_text(
        f"✅ شماره سفارش: #{order_number}\n"
        f"🔐 شناسه داخلی سفارش: #{order_id}\n"
        "حالا لطفاً عکس رسید پرداخت را ارسال کنید.\n\n"
        "اگر منصرف شدید، می‌توانید خرید را لغو کنید.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ لغو خرید", callback_data=f"cancel_active_order:{order_id}")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="global_back")],
        ]),
    )


async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user:
        remember_user(update.effective_user.id)
    if await access_gate(update, context):
        return
    photo = update.message.photo
    if not photo:
        return
    order_id = context.user_data.get("active_receipt_order")
    if not order_id:
        await update.message.reply_text(
            "برای ثبت رسید، ابتدا از منوی «خرید سرویس جدید» سفارش ثبت کنید."
        )
        return
    db = load_db()
    order = db["orders"].get(str(order_id))
    if not order or order["user_id"] != update.effective_user.id:
        context.user_data.pop("active_receipt_order", None)
        await update.message.reply_text("سفارش فعال پیدا نشد؛ از منو سفارش جدید بسازید.")
        return
    if order["status"] not in ("در انتظار رسید", "رسید ارسال‌شده"):
        await update.message.reply_text("این سفارش دیگر منتظر رسید نیست.")
        return

    file_id = photo[-1].file_id
    order["receipt_file_id"] = file_id
    order["status"] = "رسید ارسال‌شده؛ در انتظار بررسی"
    save_db(db)

    admin_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تأیید رسید", callback_data=f"review:approve:{order_id}"),
            InlineKeyboardButton("❌ رد رسید", callback_data=f"review:reject:{order_id}"),
        ]
    ])
    caption = "📥 رسید پرداخت جدید\n\n" + order_text(order)
    try:
        for admin_id in sorted(load_admin_ids()):
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=file_id,
                caption=caption,
                reply_markup=admin_keyboard,
            )
        await update.message.reply_text(
            "✅ رسید پرداخت شما با موفقیت دریافت و جهت بررسی به واحد مدیریت ارسال شد.\n\n"
            "پس از بررسی، نتیجه از طریق همین ربات به اطلاع شما خواهد رسید.\n"
            "از شکیبایی شما سپاسگزاریم.",
            reply_markup=MAIN_MARKUP,
        )
        context.user_data.pop("active_receipt_order", None)
    except Exception:
        logger.exception("Failed to notify admin")
        await update.message.reply_text(
            "رسید دریافت شد، اما ارسال آن به ادمین انجام نشد. لطفاً کمی بعد دوباره تلاش کنید."
        )


async def cancel_active_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        order_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.message.reply_text("شناسه سفارش نامعتبر است.", reply_markup=MAIN_MARKUP)
        return
    db = load_db()
    order = db.get("orders", {}).get(str(order_id))
    if not order or int(order.get("user_id", -1)) != query.from_user.id:
        await query.message.reply_text("این سفارش پیدا نشد یا متعلق به شما نیست.", reply_markup=MAIN_MARKUP)
        return
    if order.get("status") not in ("در انتظار رسید", "رسید ارسال‌شده"):
        await query.message.reply_text("این سفارش دیگر قابل لغو نیست؛ وضعیت فعلی: " + str(order.get("status")), reply_markup=MAIN_MARKUP)
        return
    order["status"] = "لغو‌شده توسط مشتری"
    order["cancelled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_db(db)
    if context.user_data.get("active_receipt_order") == order_id:
        context.user_data.pop("active_receipt_order", None)
    await query.message.reply_text(f"✅ سفارش #{order.get('order_number', order_id)} لغو شد. دیگر رسیدی برای این سفارش ثبت نمی‌شود.", reply_markup=MAIN_MARKUP)


async def review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    if not is_admin(user.id):
        await query.answer("فقط ادمین اجازه بررسی رسید را دارد.", show_alert=True)
        return
    await query.answer()
    _, decision, order_id = query.data.split(":")
    db = load_db()
    order = db["orders"].get(order_id)
    if not order:
        await query.message.reply_text("این سفارش پیدا نشد.")
        return
    if order["status"] != "رسید ارسال‌شده؛ در انتظار بررسی":
        await query.message.reply_text(f"این سفارش قبلاً تعیین تکلیف شده است: {order['status']}")
        return

    if decision == "approve":
        order["status"] = "رسید تأییدشده؛ در انتظار کانفیگ"
        order["reviewed_by"] = user.id
        save_db(db)
        await context.bot.send_message(
            chat_id=order["user_id"],
            text=(f"✅ پرداخت سفارش شماره #{order.get('order_number', order_id)} تأیید شد.\n\n"
                  "از خرید شما سپاسگزاریم. اطلاعات سرویس پس از آماده‌سازی توسط مدیریت ارسال خواهد شد.\n\n"
                  "در صورت تمایل، می‌توانید از بخش زیر خرید دیگری را آغاز کنید."),
            reply_markup=server_selection_markup(),
        )
        await query.message.reply_text(
            f"رسید سفارش #{order_id} تأیید شد.\n"
            f"برای تحویل کانفیگ، دستور زیر را در همین چت با ربات ارسال کنید:\n"
            f"/sendconfig {order_id} آیدی_عددی_مشتری\n"
            "سپس در پیام بعدی متن کانفیگ را ارسال کنید."
        )
    elif decision == "reject":
        order["status"] = "رسید ردشده"
        order["reviewed_by"] = user.id
        save_db(db)
        try:
            await context.bot.send_message(
                chat_id=order["user_id"],
                text=f"❌ رسید پرداخت سفارش #{order_id} توسط ادمین تأیید نشد.\n"
                     "لطفاً رسید را بررسی کنید یا با پشتیبانی تماس بگیرید.",
            )
        except Exception:
            logger.exception("Could not notify customer")
        await query.message.reply_text(f"رسید سفارش #{order_id} رد شد.")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


async def sendconfig_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("این دستور فقط برای ادمین است.")
        return
    if len(context.args) != 2:
        await update.message.reply_text(
            "فرمت درست:\n/sendconfig شماره_سفارش آیدی_عددی_مشتری\n"
            "مثال: /sendconfig 1001 123456789"
        )
        return
    try:
        order_id = str(int(context.args[0]))
        customer_id = int(context.args[1])
    except ValueError:
        await update.message.reply_text("شماره سفارش و آیدی مشتری باید عدد باشند.")
        return
    db = load_db()
    order = db["orders"].get(order_id)
    if not order:
        await update.message.reply_text("شماره سفارش پیدا نشد.")
        return
    if order["status"] != "رسید تأییدشده؛ در انتظار کانفیگ":
        await update.message.reply_text("این سفارش تأیید نشده یا قبلاً تحویل داده شده است.")
        return
    if order["user_id"] != customer_id:
        await update.message.reply_text(
            f"آیدی مشتری با سفارش مطابقت ندارد.\nآیدی درست این سفارش: {order['user_id']}"
        )
        return
    context.user_data["config_order_id"] = order_id
    await update.message.reply_text(
        f"حالا متن کانفیگ سفارش #{order_id} را در پیام بعدی بفرست."
    )


async def handle_config_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return False
    order_id = context.user_data.get("config_order_id")
    if not order_id:
        return False
    text_value = (update.message.text or "").strip()
    if not text_value:
        await update.message.reply_text("متن کانفیگ خالی است؛ دوباره ارسال کنید.")
        return True
    db = load_db()
    order = db["orders"].get(str(order_id))
    if not order or order["status"] != "رسید تأییدشده؛ در انتظار کانفیگ":
        context.user_data.pop("config_order_id", None)
        await update.message.reply_text("این سفارش دیگر آماده تحویل نیست.")
        return True
    try:
        await context.bot.send_message(
            chat_id=order["user_id"],
            text=f"🎉 کانفیگ سفارش #{order_id} شما آماده است:\n\n{text_value}",
        )
    except Exception:
        logger.exception("Failed to send config to customer")
        await update.message.reply_text(
            "ارسال کانفیگ به مشتری ناموفق بود. بررسی کنید مشتری قبلاً ربات را Start کرده باشد."
        )
        return True
    order["status"] = "کانفیگ تحویل داده شد"
    order["config_sent"] = True
    order["config_sent_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_db(db)
    context.user_data.pop("config_order_id", None)
    await update.message.reply_text(f"کانفیگ سفارش #{order_id} با موفقیت ارسال شد.")
    return True


async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    mine = [
        o for o in db["orders"].values()
        if o.get("user_id") == update.effective_user.id
        and o.get("status") == "کانفیگ تحویل داده شد"
        and o.get("config_sent")
    ]
    mine.sort(key=lambda o: o.get("id", 0), reverse=True)
    if not mine:
        await update.message.reply_text(
            "📱 هنوز سرویس تحویل‌داده‌شده‌ای ندارید.\nبعد از تحویل کانفیگ، سرویس در همین بخش نمایش داده می‌شود.",
            reply_markup=MAIN_MARKUP,
        )
        return

    lines = ["📱 سرویس‌های تحویل‌داده‌شده شما:", "برای تمدید هر سرویس، دکمهٔ زیر همان سرویس را بزنید."]
    keyboard = []
    for order in mine:
        code = order.get("id", "—")
        customer_code = order.get("order_number", code)
        lines.append(
            f"\n━━━━━━━━━━━━\n"
            f"📦 نام سرویس: {order.get('panel', 'سرویس')}\n"
            f"🔹 محصول: {order.get('product', '—')}\n"
            f"🧾 شماره سفارش شما: #{customer_code}\n"
            f"📊 حجم/تعداد: {order.get('quantity', '—')}\n"
            f"💵 مبلغ خرید: {money(order.get('amount', 0))}"
        )
        keyboard.append([InlineKeyboardButton(
            f"♻️ تمدید {order.get('panel', 'سرویس')} | #{customer_code}",
            callback_data=f"renew_service:{code}"
        )])
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def renew_service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        source_id = str(int(query.data.split(":", 1)[1]))
    except (ValueError, IndexError):
        await query.message.reply_text("کد سرویس معتبر نیست.")
        return

    db = load_db()
    source = db["orders"].get(source_id)
    if (not source or source.get("user_id") != query.from_user.id
            or source.get("status") != "کانفیگ تحویل داده شد"
            or not source.get("config_sent")):
        await query.message.reply_text("این سرویس پیدا نشد یا هنوز تحویل داده نشده است.")
        return

    panel = source.get("panel")
    product = source.get("product")
    quantity = source.get("quantity")
    unit_price = next((price for name, price in PRODUCTS.get(panel, []) if name == product), None)
    if unit_price is None:
        await query.message.reply_text(
            "قیمت فعلی این سرویس در فهرست تعرفه‌ها پیدا نشد. لطفاً با پشتیبانی تماس بگیرید."
        )
        return

    try:
        quantity = int(quantity)
    except (ValueError, TypeError):
        await query.message.reply_text("حجم/تعداد سرویس قبلی معتبر نیست؛ با پشتیبانی تماس بگیرید.")
        return

    original_amount = quantity * unit_price
    discount_percent = load_discount_percent()
    amount = discounted_price(original_amount, discount_percent)
    discount_amount = original_amount - amount
    order_id = db["next_id"]
    db["next_id"] += 1
    user = query.from_user
    order_number = 1 + max(
        (int(o.get("order_number", 0)) for o in db["orders"].values()), default=0
    )
    renewal = {
        "id": order_id,
        "order_number": order_number,
        "user_id": user.id,
        "customer_name": " ".join(x for x in [user.first_name, user.last_name] if x) or "بدون نام",
        "username": f"@{user.username}" if user.username else "ندارد",
        "panel": panel,
        "product": product,
        "quantity": quantity,
        "amount": amount,
        "original_amount": original_amount,
        "discount_percent": discount_percent,
        "discount_amount": discount_amount,
        "status": "در انتظار رسید",
        "receipt_file_id": None,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reviewed_by": None,
        "config_sent": False,
        "renewal_of": int(source_id),
    }
    db["orders"][str(order_id)] = renewal
    save_db(db)
    context.user_data["active_receipt_order"] = order_id
    await query.message.reply_text(
        f"♻️ سفارش تمدید ثبت شد\n\n"
        f"📦 سرویس: {panel}\n"
        f"🔹 محصول: {product}\n"
        f"🧾 شماره سفارش: #{order_number}\n"
        f"🔐 شناسه داخلی سفارش: #{order_id}\n"
        f"🔁 تمدید سرویس با کد قبلی: #{source_id}\n"
        f"📊 حجم/تعداد: {quantity}\n"
        + (f"🏷 تخفیف: {discount_percent}٪\n" if discount_percent else "")
        + f"💵 مبلغ قابل پرداخت: {money(amount)}\n\n"
        "💳 پس از پرداخت، عکس رسید را همین‌جا ارسال کنید. بعد از تأیید پرداخت، ادمین تمدید را انجام می‌دهد.",
        reply_markup=MAIN_MARKUP,
    )

async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("این دستور فقط برای ادمین است.")
        return
    db = load_db()
    orders = sorted(db["orders"].values(), key=lambda o: o["id"], reverse=True)
    if not orders:
        await update.message.reply_text("هنوز سفارشی ثبت نشده است.")
        return
    lines = ["🗂 آخرین سفارش‌ها:"]
    for order in orders[:30]:
        lines.append(f"#{order['id']} | مشتری {order['user_id']} | {order['status']} | {money(order['amount'])}")
    await update.message.reply_text("\n".join(lines))


# ================= ONE-TIME TEST ACCOUNT POOL =================
def load_test_accounts():
    """Load queued test links and the persistent one-test-per-user ledger."""
    if not TEST_FILE.exists():
        return {"next_id": 1, "available": [], "claimed_users": {}}
    try:
        with TEST_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Invalid test account database")
        data.setdefault("next_id", 1)
        data.setdefault("available", [])
        data.setdefault("claimed_users", {})
        if not isinstance(data["available"], list) or not isinstance(data["claimed_users"], dict):
            raise ValueError("Invalid test account database fields")
        return data
    except Exception as exc:
        logger.exception("Could not read test account database")
        raise RuntimeError("فایل test_accounts.json خراب یا غیرقابل‌خواندن است؛ برای جلوگیری از ازدست‌رفتن اطلاعات آن را پاک نکنید.") from exc


def save_test_accounts(data):
    tmp = TEST_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(TEST_FILE)


async def test_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.effective_message.reply_text("⛔ این دستور فقط برای ادمین است.")
        return
    raw = " ".join(context.args or []).strip()
    if not raw or len(context.args or []) < 2:
        await update.effective_message.reply_text(
            "فرمت دستور:\n/test_add نام_سرور لینک_تست\n\n"
            "مثال:\n/test_add Turkey https://example.com/test-link\n\n"
            "هر بار فقط یک لینک اضافه کنید. نام سرور می‌تواند بدون فاصله یا با زیرخط باشد."
        )
        return
    server_name = context.args[0].replace("_", " ")[:80]
    link = " ".join(context.args[1:]).strip()
    if len(link) > 2000 or not (link.startswith("https://") or link.startswith("http://")):
        await update.effective_message.reply_text("لینک معتبر نیست؛ لینک باید با http:// یا https:// شروع شود.")
        return
    try:
        async with TEST_ACCOUNTS_LOCK:
            data = load_test_accounts()
            # Never re-queue a link that is waiting OR was already delivered.
            claimed_links = {
                str(record.get("link", "")).strip()
                for record in data["claimed_users"].values()
                if isinstance(record, dict)
            }
            if any(str(item.get("link", "")).strip() == link for item in data["available"]) or link in claimed_links:
                await update.effective_message.reply_text("⛔ این لینک قبلاً اضافه یا به مشتری تحویل شده است؛ برای جلوگیری از ارسال تکراری دوباره ثبت نشد.")
                return
            test_id = int(data["next_id"])
            data["next_id"] = test_id + 1
            data["available"].append({"id": test_id, "server": server_name, "link": link, "added_at": datetime.now().isoformat(timespec="seconds")})
            save_test_accounts(data)
            remaining = len(data["available"])
    except Exception:
        logger.exception("Could not add test link")
        await update.effective_message.reply_text("⚠️ ذخیره لینک تست ناموفق بود. فایل test_accounts.json را بررسی کن.")
        return
    await update.effective_message.reply_text(f"✅ لینک تست #{test_id} برای سرور «{server_name}» اضافه شد.\nتعداد لینک‌های آماده: {remaining}")


async def test_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.effective_message.reply_text("⛔ این دستور فقط برای ادمین است.")
        return
    try:
        async with TEST_ACCOUNTS_LOCK:
            data = load_test_accounts()
            available = list(data["available"])
            claimed = dict(data["claimed_users"])
    except Exception:
        logger.exception("Could not list test links")
        await update.effective_message.reply_text("⚠️ فایل اکانت تست قابل خواندن نیست.")
        return
    lines = [f"🧪 لینک‌های آماده: {len(available)}", f"👥 کاربران دریافت‌کننده: {len(claimed)}", ""]
    for item in available[:100]:
        lines.append(f"#{item.get('id')} — {item.get('server', 'سرور نامشخص')}")
    if not available:
        lines.append("صف لینک‌های تست خالی است. با /test_add لینک اضافه کنید.")
    await update.effective_message.reply_text("\n".join(lines))


async def test_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    if not user or not query.message:
        return

    user_key = str(user.id)
    # Claim + save happen under one lock: each user gets one link, and each link
    # can be allocated to at most one user even if requests arrive together.
    try:
        async with TEST_ACCOUNTS_LOCK:
            data = load_test_accounts()
            if user_key in data["claimed_users"]:
                claimed = dict(data["claimed_users"][user_key])
                item = None
                remaining = len(data["available"])
            elif not data["available"]:
                claimed = None
                item = None
                remaining = 0
            else:
                item = data["available"].pop(0)  # consume exactly ONE next link
                data["claimed_users"][user_key] = {
                    "test_id": item.get("id"),
                    "server": item.get("server", "سرور نامشخص"),
                    "link": item.get("link", ""),
                    "claimed_at": datetime.now().isoformat(timespec="seconds"),
                }
                save_test_accounts(data)  # persist before sending, preventing duplicate delivery
                claimed = None
                remaining = len(data["available"])
    except Exception:
        logger.exception("Could not safely allocate test account")
        await query.message.reply_text(
            "⚠️ دریافت تست فعلاً انجام نشد چون ذخیره اطلاعات با خطا روبه‌رو شد. لطفاً به پشتیبانی اطلاع بده.",
            reply_markup=MAIN_MARKUP,
        )
        return

    if item is None and claimed is not None:
        await query.message.reply_text(
            "⛔ شما قبلاً اکانت تست دریافت کرده‌اید؛ هر کاربر فقط یک تست می‌تواند بگیرد.\n"
            f"سرور قبلی: {claimed.get('server', 'نامشخص')}",
            reply_markup=MAIN_MARKUP,
        )
        return
    if item is None:
        await query.message.reply_text(
            "😕 فعلاً لینک تست آماده نداریم. لطفاً بعداً مراجعه کن.",
            reply_markup=MAIN_MARKUP,
        )
        return

    try:
        await query.message.reply_text(
            f"🧪 اکانت تست شما\nسرور: {item.get('server', 'نامشخص')}\n\n{item['link']}\n\n"
            "✅ این تست به حساب شما ثبت شد. هر کاربر فقط یک تست می‌تواند دریافت کند. "
            "این لینک از صف حذف شد و به مشتری بعدی داده نمی‌شود؛ مشتری بعدی لینک بعدی صف را می‌گیرد.",
            reply_markup=MAIN_MARKUP,
            disable_web_page_preview=True,
        )
    except TelegramError:
        # Allocation remains consumed so it cannot accidentally be sent to another user.
        logger.exception("Could not deliver allocated test link to user %s", user.id)
        return

    for admin_id in sorted(load_admin_ids()):
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(f"🧪 تست تحویل شد\nکاربر: {user.full_name}\nآیدی: {user.id}\n"
                      f"سرور: {item.get('server', 'نامشخص')}\nشناسه تست: #{item.get('id')}\n"
                      f"لینک‌های باقی‌مانده: {remaining}"),
            )
        except Exception:
            logger.exception("Could not notify admin about test claim")


async def menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.business_message:
        return
    if update.effective_user:
        remember_user(update.effective_user.id)
    if await access_gate(update, context):
        return
    txt = (update.message.text or "").strip()

    if txt in ("🔙 منوی اصلی", "🔙 بازگشت به منوی اصلی"):
        # If the user is in the payment/receipt stage, treat going back as cancelling that order.
        await cancel_active_order(update.effective_user.id, context)
        for key in ("quantity_waiting", "pending_order", "market_flow", "panel", "product", "unit_price", "config_flow"):
            context.user_data.pop(key, None)
        await update.message.reply_text("به منوی اصلی برگشتید. عملیات نیمه‌کاره لغو شد.", reply_markup=MAIN_MARKUP)
        return

    if txt == "🔙 بازگشت به انتخاب سرور":
        context.user_data.pop("quantity_waiting", None)
        context.user_data.pop("pending_order", None)
        context.user_data.pop("panel", None)
        context.user_data.pop("product", None)
        context.user_data.pop("unit_price", None)
        await begin_purchase(update, context)
        return

    if await handle_config_text(update, context):
        return
    if await handle_quantity(update, context):
        return

    if await market_text_input(update, context):
        return

    if txt == "🎮 شاپ اکانت بازی‌ها":
        await market_open(update, context)
    elif txt == "💎 خدمات مجازی":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💎 تلگرام پرمیوم — ۱ ماه | ۱٬۲۰۰٬۰۰۰ تومان", callback_data="virtual:0")],
            [InlineKeyboardButton("💎 تلگرام پرمیوم — ۳ ماه | ۳٬۹۰۰٬۰۰۰ تومان", callback_data="virtual:1")],
            [InlineKeyboardButton("💎 تلگرام پرمیوم — ۶ ماه | ۸٬۰۰۰٬۰۰۰ تومان", callback_data="virtual:2")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="global_back")],
        ])
        await update.message.reply_text("💎 خدمات مجازی\n\nپلن موردنظر تلگرام پرمیوم را انتخاب کنید:", reply_markup=keyboard)
    elif txt == "🚀 خرید سرویس جدید":
        await begin_purchase(update, context)
    elif txt == "💰 تعرفه سرویس‌ها":
        await show_tariffs(update, context)
    elif txt == "📱 سرویس‌های من":
        await my_orders(update, context)
    elif txt == "🧪 اکانت تست":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎁 دریافت اکانت تست رایگان", callback_data="test:request")],
            [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="global_back")],
        ])
        await update.message.reply_text(
            "🧪 دریافت اکانت تست\n\nهر کاربر فقط یک‌بار می‌تواند تست دریافت کند. لینک‌ها به ترتیب توسط ادمین اضافه می‌شوند و هر لینک فقط یک‌بار از صف تحویل داده می‌شود.",
            reply_markup=keyboard,
        )
    elif txt == "♻️ تمدید سرویس":
        await my_orders(update, context)
    elif txt == "💡 آموزش استفاده":
        await update.message.reply_text("آموزش استفاده فعلاً در حال آماده‌سازی است.", reply_markup=MAIN_MARKUP)
    elif txt == "💰 اعتبار من":
        await update.message.reply_text("اعتبار داخلی در این نسخه فعال نیست؛ پرداخت با رسید انجام می‌شود.", reply_markup=MAIN_MARKUP)
    elif txt == "🎗️ کاربران برتر":
        await update.message.reply_text("فهرست کاربران برتر فعلاً در دسترس نیست.", reply_markup=MAIN_MARKUP)
    elif txt == "👥 معرفی به دوستان":
        bot_username = context.bot_data.get("bot_username", "")
        await update.message.reply_text(
            f"لینک معرفی شما:\nhttps://t.me/{bot_username}?start={update.effective_user.id}"
            if bot_username else "لینک معرفی در دسترس نیست.",
            reply_markup=MAIN_MARKUP,
        )
    elif txt == "🧑🏻‍💻 تماس با پشتیبانی":
        await update.message.reply_text("برای پشتیبانی، پیام خود را همین‌جا بنویسید.", reply_markup=MAIN_MARKUP)
    elif txt == "🤝 درخواست نمایندگی":
        await update.message.reply_text("برای درخواست نمایندگی، با پشتیبانی تماس بگیرید.", reply_markup=MAIN_MARKUP)


# ================= ONLINE GAME ACCOUNT MARKETPLACE =================
def load_market_listings():
    if not MARKET_FILE.exists():
        return {"next_id": 1, "listings": {}}
    try:
        with MARKET_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("next_id", 1)
        data.setdefault("listings", {})
        return data
    except Exception as exc:
        logger.exception("Could not read game account marketplace database")
        # Never silently replace a damaged marketplace database with an empty one.
        raise RuntimeError(
            "فایل game_account_listings.json قابل خواندن نیست؛ برای جلوگیری از گم‌شدن آگهی‌ها، "
            "فایل را حذف نکنید و ابتدا از آن نسخه پشتیبان بگیرید."
        ) from exc


def save_market_listings(data):
    tmp = MARKET_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(MARKET_FILE)


def market_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍 مشاهده اکانت‌های ثبت‌شده", callback_data="market:browse:0")],
        [InlineKeyboardButton("➕ ثبت اکانت برای فروش", callback_data="market:sell")],
        [InlineKeyboardButton("📦 آگهی‌های من / حذف آگهی", callback_data="market:mine")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="market:home")],
    ])


async def market_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("market_flow", None)
    await update.effective_message.reply_text(
        "🎮 شاپ اکانت بازی‌های آنلاین\n\n"
        "اینجا می‌توانید برای بازی‌های آنلاین مختلف آگهی ثبت کنید یا آگهی‌های موجود را ببینید.\n"
        "برای ثبت آگهی حداقل ۵ عکس لازم است. خرید و پرداخت فقط پس از هماهنگی با پشتیبانی و بررسی جزئیات انجام شود؛ ربات پرداخت امانی یا تضمین معامله را به‌صورت خودکار انجام نمی‌دهد.",
        reply_markup=market_menu_markup(),
    )


async def market_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flow = context.user_data.get("market_flow")
    if not flow or not update.message or not update.message.text:
        return False
    value = update.message.text.strip()
    if value in ("❌ لغو ثبت آگهی", "🔙 بازگشت به انتخاب سرور"):
        context.user_data.pop("market_flow", None)
        await update.message.reply_text("ثبت آگهی لغو شد.", reply_markup=MAIN_MARKUP)
        return True
    if flow.get("step") == "game":
        if len(value) < 2 or len(value) > 80:
            await update.message.reply_text("نام بازی را بین ۲ تا ۸۰ نویسه وارد کنید.")
            return True
        flow["game"] = value
        flow["step"] = "level"
        await update.message.reply_text("🎚 لول اکانت را وارد کنید (مثلاً ۶۵ یا «لول نامشخص»):")
        return True
    if flow.get("step") == "level":
        if len(value) > 60:
            await update.message.reply_text("لطفاً لول را کوتاه‌تر وارد کنید.")
            return True
        flow["level"] = value
        flow["step"] = "price"
        await update.message.reply_text("💰 قیمت درخواستی را به تومان و فقط با عدد انگلیسی وارد کنید (مثلاً 1500000):")
        return True
    if flow.get("step") == "price":
        try:
            price = int(value.replace(",", "").replace("٬", ""))
        except ValueError:
            await update.message.reply_text("قیمت باید عدد صحیح انگلیسی باشد؛ مثال: 1500000")
            return True
        if price < 1000 or price > 10_000_000_000:
            await update.message.reply_text("قیمت باید بین ۱٬۰۰۰ تا ۱۰٬۰۰۰٬۰۰۰٬۰۰۰ تومان باشد.")
            return True
        flow["price"] = price
        flow["step"] = "description"
        await update.message.reply_text("📝 توضیحات آگهی را بفرستید (آیتم‌ها، اسکین‌ها، رتبه و نکات مهم). اطلاعات ورود یا رمز را ارسال نکنید:")
        return True
    if flow.get("step") == "description":
        if len(value) > 800:
            await update.message.reply_text("توضیحات حداکثر ۸۰۰ نویسه باشد.")
            return True
        flow["description"] = value
        flow["step"] = "photos"
        flow["photos"] = []
        await update.message.reply_text(
            "📸 حالا حداقل ۵ عکس واضح از اکانت بفرستید. پس از ارسال ۵ عکس یا بیشتر، دکمه «اتمام ارسال عکس‌ها» را بزنید.",
            reply_markup=ReplyKeyboardMarkup([["✅ اتمام ارسال عکس‌ها"], ["❌ لغو ثبت آگهی"], ["🔙 منوی اصلی"]], resize_keyboard=True, one_time_keyboard=False),
        )
        return True
    if flow.get("step") == "photos" and value == "✅ اتمام ارسال عکس‌ها":
        if len(flow.get("photos", [])) < 5:
            await update.message.reply_text(f"هنوز فقط {len(flow.get('photos', []))} عکس دریافت شده؛ حداقل ۵ عکس لازم است.")
            return True
        flow["step"] = "confirm"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ انتشار آگهی", callback_data="market:publish")],
            [InlineKeyboardButton("❌ لغو", callback_data="market:cancel")],
        ])
        await update.message.reply_text(
            f"📋 پیش‌نمایش آگهی\nبازی: {flow['game']}\nلول: {flow['level']}\nقیمت: {money(flow['price'])}\nعکس‌ها: {len(flow['photos'])}\nتوضیحات: {flow['description']}\n\nبا انتشار آگهی، تأیید می‌کنید مالک/مجاز به فروش اکانت هستید و اطلاعات آگهی درست است.",
            reply_markup=keyboard,
        )
        return True
    return False


async def market_photo_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    flow = context.user_data.get("market_flow")
    if flow and flow.get("step") == "photos" and update.message and update.message.photo:
        photos = flow.setdefault("photos", [])
        if len(photos) >= 10:
            await update.message.reply_text("حداکثر ۱۰ عکس برای هر آگهی پذیرفته می‌شود.")
            return
        photos.append(update.message.photo[-1].file_id)
        await update.message.reply_text(f"✅ عکس {len(photos)} ثبت شد. حداقل ۵ عکس لازم است؛ برای ادامه دکمه «اتمام ارسال عکس‌ها» را بزنید.")
        return
    await handle_receipt(update, context)


async def market_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user
    if data == "market:home":
        context.user_data.pop("market_flow", None)
        await query.message.reply_text("به منوی اصلی برگشتید.", reply_markup=MAIN_MARKUP)
        return
    if data == "market:sell":
        context.user_data["market_flow"] = {"step": "game", "seller_id": user.id}
        await query.message.reply_text("🎮 نام بازی آنلاین را وارد کنید (مثلاً Free Fire، PUBG Mobile، Roblox، Fortnite یا هر بازی دیگر):", reply_markup=ReplyKeyboardMarkup([["❌ لغو ثبت آگهی"], ["🔙 منوی اصلی"]], resize_keyboard=True, one_time_keyboard=False))
        return
    if data == "market:cancel":
        context.user_data.pop("market_flow", None)
        await query.message.reply_text("ثبت آگهی لغو شد.", reply_markup=MAIN_MARKUP)
        return
    if data == "market:publish":
        flow = context.user_data.get("market_flow")
        if not flow or flow.get("step") != "confirm" or len(flow.get("photos", [])) < 5:
            await query.message.reply_text("اطلاعات آگهی کامل نیست؛ دوباره از شاپ اکانت شروع کنید.")
            return
        db = load_market_listings()
        listing_id = int(db["next_id"])
        db["next_id"] = listing_id + 1
        db["listings"][str(listing_id)] = {
            "id": listing_id, "seller_id": user.id,
            "seller_name": user.full_name[:100], "game": flow["game"],
            "level": flow["level"], "price": flow["price"],
            "description": flow["description"], "photos": flow["photos"],
            "status": "active", "created_at": datetime.now().isoformat(timespec="seconds")
        }
        save_market_listings(db)
        context.user_data.pop("market_flow", None)
        await query.message.reply_text(f"✅ آگهی #{listing_id} منتشر شد و در فهرست آگهی‌ها نمایش داده می‌شود. هر زمان خواستید از «آگهی‌های من / حذف آگهی» آن را بردارید.", reply_markup=MAIN_MARKUP)
        return
    if data == "market:mine":
        db = load_market_listings()
        mine = [x for x in db["listings"].values() if x.get("seller_id") == user.id and x.get("status") == "active"]
        if not mine:
            await query.message.reply_text("آگهی فعال ندارید.", reply_markup=market_menu_markup())
            return
        keyboard = [[InlineKeyboardButton(f"❌ حذف آگهی #{x['id']} — {x['game']} — {money(x['price'])}", callback_data=f"market:remove:{x['id']}")] for x in mine[:50]]
        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="market:menu")])
        await query.message.reply_text("آگهی‌های فعال شما؛ برای برداشتن آگهی دکمه مربوط به آن را بزنید:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    if data == "market:menu":
        await query.message.reply_text("🎮 شاپ اکانت بازی‌های آنلاین:", reply_markup=market_menu_markup())
        return
    if data.startswith("market:remove:"):
        listing_id = data.rsplit(":", 1)[1]
        db = load_market_listings()
        listing = db["listings"].get(listing_id)
        if not listing or listing.get("seller_id") != user.id:
            await query.message.reply_text("این آگهی متعلق به شما نیست یا پیدا نشد.")
            return
        listing["status"] = "removed"
        listing["removed_at"] = datetime.now().isoformat(timespec="seconds")
        save_market_listings(db)
        await query.message.reply_text(f"✅ آگهی #{listing_id} از فهرست عمومی برداشته شد.", reply_markup=market_menu_markup())
        return
    if data.startswith("market:browse:"):
        db = load_market_listings()
        active = sorted([x for x in db["listings"].values() if x.get("status") == "active"], key=lambda x: x.get("id", 0), reverse=True)
        if not active:
            await query.message.reply_text("فعلاً آگهی فعالی ثبت نشده است. بعداً دوباره سر بزنید.", reply_markup=market_menu_markup())
            return
        try:
            page = max(0, int(data.rsplit(":", 1)[1]))
        except (ValueError, IndexError):
            await query.message.reply_text("صفحه درخواستی معتبر نیست؛ فهرست از ابتدا باز می‌شود.", reply_markup=market_menu_markup())
            return
        page_size = 8
        max_page = max(0, (len(active) - 1) // page_size)
        page = min(page, max_page)
        subset = active[page * page_size:(page + 1) * page_size]
        keyboard = [[InlineKeyboardButton(f"{x['game']} | لول {x['level']} | {money(x['price'])}", callback_data=f"market:view:{x['id']}")] for x in subset]
        nav = []
        if page > 0: nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"market:browse:{page-1}"))
        if (page + 1) * page_size < len(active): nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"market:browse:{page+1}"))
        if nav: keyboard.append(nav)
        keyboard.append([InlineKeyboardButton("🔙 منوی شاپ اکانت", callback_data="market:menu")])
        await query.message.reply_text("🛍 آگهی‌های فعال (برای جزئیات روی آگهی بزنید):\n\nقیمت‌ها را فروشندگان تعیین می‌کنند؛ پیش از پرداخت، مشخصات را با پشتیبانی بررسی کنید.", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    if data.startswith("market:view:"):
        listing_id = data.rsplit(":", 1)[1]
        db = load_market_listings()
        listing = db["listings"].get(listing_id)
        if not listing or listing.get("status") != "active":
            await query.message.reply_text("این آگهی دیگر فعال نیست.")
            return
        await query.message.reply_text(
            f"🎮 آگهی #{listing_id}\nبازی: {listing['game']}\nلول: {listing['level']}\nقیمت درخواستی: {money(listing['price'])}\nتعداد عکس: {len(listing.get('photos', []))}\nتوضیحات: {listing.get('description', '—')}\n\nبرای خرید، درخواست را بزنید تا با پشتیبانی هماهنگ شود. تا زمان بررسی، پول یا اطلاعات ورود را مستقیم برای فروشنده نفرستید.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🤝 درخواست خرید از طریق پشتیبانی", callback_data=f"market:buy:{listing_id}")],
                [InlineKeyboardButton("🔙 برگشت به آگهی‌ها", callback_data="market:browse:0")],
            ])
        )
        photos = listing.get("photos", [])[:10]
        if photos:
            try:
                await context.bot.send_media_group(chat_id=query.message.chat_id, media=[InputMediaPhoto(media=fid) for fid in photos])
            except Exception:
                for fid in photos:
                    try: await context.bot.send_photo(chat_id=query.message.chat_id, photo=fid)
                    except Exception: logger.exception("Could not display marketplace photo")
        return
    if data.startswith("market:buy:"):
        listing_id = data.rsplit(":", 1)[1]
        db = load_market_listings()
        listing = db["listings"].get(listing_id)
        if not listing or listing.get("status") != "active":
            await query.message.reply_text("این آگهی دیگر فعال نیست.")
            return
        if listing.get("seller_id") == user.id:
            await query.message.reply_text("این آگهی متعلق به خودتان است.")
            return
        text_msg = (f"🤝 درخواست واسطه‌گری خرید اکانت\nآگهی: #{listing_id}\nبازی: {listing['game']}\nلول: {listing['level']}\nقیمت: {money(listing['price'])}\nفروشنده ID: {listing['seller_id']}\nخریدار: {user.full_name} (ID: {user.id})\nیوزرنیم خریدار: @{user.username}" if user.username else f"🤝 درخواست واسطه‌گری خرید اکانت\nآگهی: #{listing_id}\nبازی: {listing['game']}\nلول: {listing['level']}\nقیمت: {money(listing['price'])}\nفروشنده ID: {listing['seller_id']}\nخریدار: {user.full_name} (ID: {user.id})\nخریدار یوزرنیم ندارد")
        sent = 0
        for admin_id in sorted(load_admin_ids()):
            try:
                await context.bot.send_message(chat_id=admin_id, text=text_msg)
                sent += 1
            except Exception:
                logger.exception("Could not notify marketplace support admin %s", admin_id)
        try:
            await context.bot.send_message(chat_id=listing["seller_id"], text=f"📩 برای آگهی #{listing_id} شما درخواست خرید ثبت شد. لطفاً تا هماهنگی پشتیبانی، اطلاعات ورود یا اکانت را تحویل ندهید.")
        except Exception:
            logger.exception("Could not notify marketplace seller")
        if sent:
            await query.message.reply_text("✅ درخواست شما ثبت شد و برای مدیریت ارسال شد؛ برای ادامه معامله منتظر پیام پشتیبانی بمانید. این ربات پرداخت امانی خودکار ندارد؛ تا تأیید شرایط، وجه یا اطلاعات ورود را ارسال نکنید.")
        else:
            await query.message.reply_text("⚠️ درخواست آماده شد، اما پیام به مدیریت ارسال نشد. لطفاً از بخش پشتیبانی به ما خبر دهید و فعلاً وجه یا اطلاعات ورود را ارسال نکنید.")
        return


async def setdiscount_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("دسترسی ندارید؛ فقط ادمین می‌تواند تخفیف را تغییر دهد.")
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text(
            "فرمت: /setdiscount درصد\nمثال: /setdiscount 20\nبرای حذف تخفیف: /setdiscount 0"
        )
        return
    try:
        percent = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("درصد باید عدد صحیح بین ۰ تا ۱۰۰ باشد.")
        return
    if not 0 <= percent <= 100:
        await update.effective_message.reply_text("درصد تخفیف باید بین ۰ تا ۱۰۰ باشد.")
        return
    save_discount_percent(percent)
    if percent:
        await update.effective_message.reply_text(
            f"✅ تخفیف عمومی سرویس‌ها روی {percent}٪ تنظیم شد. قیمت‌های جدید در سفارش‌های بعدی اعمال می‌شوند."
        )
    else:
        await update.effective_message.reply_text("✅ تخفیف غیرفعال شد؛ قیمت‌ها به حالت عادی برگشتند.")


async def discount_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("دسترسی ندارید.")
        return
    percent = load_discount_percent()
    await update.effective_message.reply_text(
        f"🏷 تخفیف فعلی: {percent}٪" if percent else "🏷 در حال حاضر تخفیفی فعال نیست."
    )


async def addadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only the primary admin configured in ADMIN_ID can grant/revoke admin access.
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("فقط مدیر اصلی می‌تواند ادمین اضافه یا حذف کند.")
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.effective_message.reply_text("فرمت: /addadmin آیدی_عددی\nمثال: /addadmin 123456789")
        return
    target_id = int(context.args[0])
    ids = load_admin_ids()
    if target_id in ids:
        await update.effective_message.reply_text("این کاربر از قبل ادمین است.")
        return
    ids.add(target_id)
    save_admin_ids(ids)
    await update.effective_message.reply_text(
        f"✅ ادمین اضافه شد: {target_id}\nبرای دریافت پیام‌های ربات، آن شخص باید ابتدا ربات را Start کند."
    )


async def removeadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("فقط مدیر اصلی می‌تواند ادمین اضافه یا حذف کند.")
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.effective_message.reply_text("فرمت: /removeadmin آیدی_عددی")
        return
    target_id = int(context.args[0])
    if target_id == ADMIN_ID:
        await update.effective_message.reply_text("مدیر اصلی قابل حذف نیست.")
        return
    ids = load_admin_ids()
    if target_id not in ids:
        await update.effective_message.reply_text("این آیدی در فهرست ادمین‌ها نیست.")
        return
    ids.remove(target_id)
    save_admin_ids(ids)
    await update.effective_message.reply_text(f"✅ دسترسی ادمین {target_id} حذف شد.")


async def admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("دسترسی ندارید.")
        return
    ids = sorted(load_admin_ids())
    lines = [f"{i}. {admin_id}" + (" (مدیر اصلی)" if admin_id == ADMIN_ID else "") for i, admin_id in enumerate(ids, 1)]
    await update.effective_message.reply_text("👮 فهرست ادمین‌ها:\n" + "\n".join(lines))


# ================= TELEGRAM BUSINESS SECRETARY =================
# Modes: auto (rules/FAQ), ai (requires a separate API key), hybrid (FAQ/rules then AI).
# Settings are stored separately so the existing orders.json is never overwritten.

def load_secretary_data():
    if not SECRETARY_FILE.exists():
        return {"connections": {}, "paused_chats": [], "faqs": [], "mode": "hybrid", "enabled": True, "history": {}}
    try:
        with SECRETARY_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("connections", {})
        data.setdefault("paused_chats", [])
        data.setdefault("faqs", [])
        data.setdefault("mode", "hybrid")
        data.setdefault("enabled", True)
        data.setdefault("history", {})
        return data
    except Exception:
        logger.exception("Could not read secretary_settings.json")
        raise RuntimeError("فایل secretary_settings.json خراب است؛ آن را حذف نکنید، ابتدا از آن نسخه پشتیبان بگیرید.")


def save_secretary_data(data):
    tmp = SECRETARY_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(SECRETARY_FILE)


def find_owner_connection(data, owner_id):
    matches = [c for c in data["connections"].values() if c.get("owner_id") == owner_id and c.get("is_enabled")]
    return matches[-1] if matches else None


def rule_answer(text, faqs):
    normalized = (text or "").strip().lower()
    for item in faqs:
        q = item.get("question", "").strip().lower()
        if q and (normalized == q or q in normalized):
            return item.get("answer", "")
    if any(x in normalized for x in ("سلام", "درود", "hello", "hi", "salam")):
        return "سلام، خوش اومدید 🌷 چطور می‌تونم کمکتون کنم؟"
    if any(x in normalized for x in ("قیمت", "تعرفه", "هزینه", "price", "cost")):
        return "برای اطلاع از تعرفه‌ها لطفاً نوع سرویس و حجم موردنظرتون رو بفرمایید تا راهنمایی‌تون کنیم."
    if any(x in normalized for x in ("خرید", "سفارش", "خریدن", "buy", "order")):
        return "برای ثبت سفارش لطفاً نوع سرویس و حجم موردنیازتون رو بفرمایید. اگر لازم باشه پشتیبانی ادامه کار رو پیگیری می‌کنه."
    if any(x in normalized for x in ("پشتیبانی", "ادمین", "انسان", "اپراتور", "support", "human")):
        return "پیامتون دریافت شد 🌷 در اولین فرصت توسط پشتیبانی بررسی می‌شه."
    if any(x in normalized for x in ("ممنون", "مرسی", "تشکر", "thanks", "thank you")):
        return "خواهش می‌کنم 🌹 هر سؤال دیگه‌ای داشتید در خدمتم."
    return None


def _call_ai_api(messages):
    if not OPENAI_API_KEY:
        return None
    payload = json.dumps({"model": OPENAI_MODEL, "messages": messages, "temperature": 0.6, "max_tokens": 500}).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_BASE_URL,
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            result = json.loads(response.read().decode("utf-8"))
        return result["choices"][0]["message"]["content"].strip()
    except Exception:
        logger.exception("AI provider request failed")
        return None


async def ai_answer(data, history_key, customer_text):
    history = data["history"].get(history_key, [])
    messages = [{"role": "system", "content": (
        "تو منشی فارسی‌زبان و مؤدب یک کسب‌وکار هستی. کوتاه، دقیق و دوستانه پاسخ بده. "
        "قیمت، موجودی، پرداخت یا تحویل را جعل نکن. اگر مطمئن نیستی بگو پشتیبانی بررسی می‌کند. "
        "رمز، کد ورود یا اطلاعات بانکی حساس درخواست نکن. خودت را هوش مصنوعی معرفی کن اگر پرسیده شد."
    )}]
    messages.extend(history[-8:])
    messages.append({"role": "user", "content": customer_text[:3000]})
    answer = await asyncio.to_thread(_call_ai_api, messages)
    if answer:
        history.extend([{"role": "user", "content": customer_text[:1000]}, {"role": "assistant", "content": answer[:1500]}])
        data["history"][history_key] = history[-10:]
        save_secretary_data(data)
    return answer


async def business_connection_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    connection = update.business_connection
    if not connection:
        return
    data = load_secretary_data()
    previous = data["connections"].get(connection.id, {})
    data["connections"][connection.id] = {
        "owner_id": connection.user.id,
        "owner_name": connection.user.full_name,
        "user_chat_id": connection.user_chat_id,
        "is_enabled": bool(connection.is_enabled),
        "secretary_enabled": previous.get("secretary_enabled", True),
        "mode": previous.get("mode", "hybrid"),
        "rights": connection.rights.to_dict() if connection.rights else {},
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_secretary_data(data)
    try:
        status = "فعال شد" if connection.is_enabled else "قطع شد"
        await context.bot.send_message(
            chat_id=connection.user_chat_id,
            text=f"🔔 اتصال منشی تلگرام بیزینس {status}.\nبرای مدیریت تنظیمات از دستور /secretary استفاده کنید."
        )
    except Exception:
        logger.exception("Could not notify business account owner")


async def business_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.business_message
    if not message or not message.business_connection_id:
        return
    if message.chat.type != "private" or not message.text:
        return
    data = load_secretary_data()
    connection_id = message.business_connection_id
    connection = data["connections"].get(connection_id)
    if not connection or not connection.get("is_enabled") or not data.get("enabled", True) or not connection.get("secretary_enabled", True):
        return
    # Never auto-reply to messages sent by the connected account owner.
    if message.from_user and message.from_user.id == connection.get("owner_id"):
        return
    rights = connection.get("rights") or {}
    if not rights.get("can_reply", False):
        return
    pause_key = f"{connection_id}:{message.chat_id}"
    if pause_key in data.get("paused_chats", []):
        return

    text_value = message.text.strip()
    mode = connection.get("mode", data.get("mode", "hybrid"))
    answer = None
    if mode in ("auto", "hybrid"):
        answer = rule_answer(text_value, data.get("faqs", []))
    if not answer and mode in ("ai", "hybrid"):
        history_key = pause_key
        answer = await ai_answer(data, history_key, text_value)
    if not answer:
        answer = ("پیامتون رو دریافت کردم 🌷 برای اینکه اطلاعات اشتباه ندم، "
                  "موضوع رو برای پشتیبانی می‌فرستم تا بررسی کنه.")
    try:
        await context.bot.send_message(
            chat_id=message.chat_id,
            text=answer[:4000],
            business_connection_id=connection_id,
        )
    except Exception:
        logger.exception("Failed to send business reply")


async def secretary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = load_secretary_data()
    if not is_admin(user_id):
        conn = find_owner_connection(data, user_id)
        if not conn:
            await update.effective_message.reply_text(
                "هنوز حساب بیزینس شما به این منشی وصل نشده. ابتدا Telegram Premium و بخش Telegram Business را بررسی کنید و ربات را از قسمت Chatbots متصل کنید."
            )
            return
    await update.effective_message.reply_text(
        "🤖 پنل منشی تلگرام\n\n"
        f"وضعیت منشی: {'روشن' if (conn.get('secretary_enabled', True) if user_id != ADMIN_ID else data.get('enabled', True)) else 'خاموش'}\n"
        f"حالت: {(conn.get('mode', data.get('mode', 'hybrid')) if user_id != ADMIN_ID else data.get('mode', 'hybrid'))}\n"
        f"حساب‌های متصل: {sum(1 for c in data['connections'].values() if c.get('is_enabled'))}\n"
        f"پرسش‌وپاسخ‌های ذخیره‌شده: {len(data.get('faqs', []))}\n\n"
        "دستورها:\n/mode auto — پاسخ‌های خودکار و FAQ\n/mode ai — هوش مصنوعی (نیازمند API key جداگانه)\n/mode hybrid — ترکیبی\n/secretary_on — روشن کردن منشی\n/secretary_off — خاموش کردن منشی\n/faq_add سؤال | جواب — افزودن پاسخ آماده\n/faq_list — نمایش FAQها\n/faq_clear — پاک کردن همه FAQها\n/pause chat_id — توقف پاسخ خودکار یک گفتگو\n/resume chat_id — ادامه پاسخ خودکار\n\n"
        "مدیریت ادمین‌ها (فقط مدیر اصلی): /addadmin آیدی، /removeadmin آیدی، /admins\n\nنکته: دستورهای مدیریتی را در چت خصوصی خود ربات بفرستید."
    )


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = load_secretary_data()
    conn = find_owner_connection(data, user_id)
    if not is_admin(user_id) and not conn:
        await update.effective_message.reply_text("ابتدا حساب Telegram Business خود را به ربات متصل کنید.")
        return
    if not context.args or context.args[0].lower() not in ("auto", "ai", "hybrid"):
        await update.effective_message.reply_text("استفاده: /mode auto یا /mode ai یا /mode hybrid")
        return
    mode = context.args[0].lower()
    if conn and not is_admin(user_id):
        # A connected account owner changes only their own secretary mode.
        conn["mode"] = mode
    else:
        data["mode"] = mode
    save_secretary_data(data)
    extra = "\nبرای حالت AI باید OPENAI_API_KEY جداگانه تنظیم شود؛ اشتراک تلگرام پریمیوم شامل API هوش مصنوعی نیست." if mode in ("ai", "hybrid") and not OPENAI_API_KEY else ""
    await update.effective_message.reply_text(f"✅ حالت منشی روی {mode} تنظیم شد.{extra}")


async def secretary_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = load_secretary_data()
    conn = find_owner_connection(data, user_id)
    is_on = update.effective_message.text.split()[0].split("@", 1)[0].endswith("_on")
    if not is_admin(user_id) and not conn:
        await update.effective_message.reply_text("ابتدا حساب Telegram Business خود را به ربات متصل کنید.")
        return
    if conn and not is_admin(user_id):
        conn["secretary_enabled"] = is_on
        state = conn["secretary_enabled"]
    else:
        data["enabled"] = is_on
        state = data["enabled"]
    save_secretary_data(data)
    await update.effective_message.reply_text("منشی روشن شد ✅" if state else "منشی خاموش شد ⏸️")


async def faq_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای مدیر ربات است.")
        return
    raw = " ".join(context.args)
    if "|" not in raw:
        await update.effective_message.reply_text("فرمت: /faq_add سؤال | جواب\nمثال: /faq_add ساعت پاسخگویی؟ | هر روز از ۹ تا ۱۸")
        return
    question, answer = [part.strip() for part in raw.split("|", 1)]
    if not question or not answer:
        await update.effective_message.reply_text("سؤال و جواب نباید خالی باشند.")
        return
    data = load_secretary_data()
    data["faqs"].append({"question": question, "answer": answer})
    save_secretary_data(data)
    await update.effective_message.reply_text("✅ پرسش و پاسخ ذخیره شد.")


async def faq_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_secretary_data()
    if not is_admin(update.effective_user.id) and not find_owner_connection(data, update.effective_user.id):
        await update.effective_message.reply_text("دسترسی ندارید.")
        return
    faqs = data.get("faqs", [])
    if not faqs:
        await update.effective_message.reply_text("هنوز FAQ ثبت نشده است.")
        return
    lines = [f"{i}. {item['question']} → {item['answer']}" for i, item in enumerate(faqs, 1)]
    await update.effective_message.reply_text("📚 پرسش‌وپاسخ‌ها:\n" + "\n".join(lines)[:4000])


async def faq_clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای مدیر ربات است.")
        return
    data = load_secretary_data()
    data["faqs"] = []
    save_secretary_data(data)
    await update.effective_message.reply_text("تمام پرسش‌وپاسخ‌های ذخیره‌شده پاک شدند.")


async def pause_resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("این دستور فقط برای مدیر ربات است.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("فرمت: /pause chat_id یا /resume chat_id\nبرای هر گفتگو باید chat_id عددی را داشته باشید.")
        return
    chat_id = int(context.args[0])
    data = load_secretary_data()
    conn = next((cid for cid, c in data["connections"].items() if c.get("is_enabled")), None)
    if not conn:
        await update.effective_message.reply_text("هیچ اتصال فعالی وجود ندارد.")
        return
    key = f"{conn}:{chat_id}"
    paused = set(data.get("paused_chats", []))
    if update.effective_message.text.startswith("/pause"):
        paused.add(key)
        message = "پاسخ خودکار برای این گفتگو متوقف شد."
    else:
        paused.discard(key)
        message = "پاسخ خودکار برای این گفتگو دوباره فعال شد."
    data["paused_chats"] = list(paused)
    save_secretary_data(data)
    await update.effective_message.reply_text(message)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception while processing update", exc_info=context.error)


async def post_init(application: Application):
    me = await application.bot.get_me()
    application.bot_data["bot_username"] = me.username or ""


def _start_health_server() -> None:
    """Tiny HTTP endpoint on the platform port so Railway's health check passes.

    python-telegram-bot's long polling opens no listening port, so without this
    the platform sees no listener and may kill or restart the container.
    Runs in a daemon thread because serve_forever() blocks.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    port = int(os.getenv("PORT", "8080"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (stdlib naming)
            body = b"bot:polling\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return  # silence per-request logging noise

    def _serve() -> None:
        try:
            ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
        except Exception:
            logger.exception("Health server failed to start (non-fatal)")

    threading.Thread(target=_serve, daemon=True).start()


def main():
    # Pydroid-friendly: if no token is configured, ask for it in the console.
    token = (BOT_TOKEN or "").strip()
    if not token or token == "PASTE_NEW_BOT_TOKEN_HERE":
        # On Railway the container has no interactive console: fail fast and clearly.
        raise SystemExit(
            "BOT_TOKEN تنظیم نشده. در متغیرهای محیطی Railway مقدار BOT_TOKEN را اضافه کن."
        )
    if not token:
        raise SystemExit("توکن خالی است. فایل را دوباره اجرا کن و توکن جدید را وارد کن.")
    if ":" not in token or len(token) < 30:
        raise SystemExit("فرمت توکن درست به نظر نمی‌رسد. توکن کامل را از API Token در @BotFather کپی کن؛ فاصله یا کوتیشن اضافه نداشته باشد.")
    try:
        app = Application.builder().token(token).post_init(post_init).build()
    except Exception as exc:
        raise SystemExit(f"ساخت ربات ناموفق بود: {exc}. نسخه کتابخانه را بررسی کن.")

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler(["senduser", "send_user"], senduser_command))
    app.add_handler(CommandHandler("block", block_command))
    app.add_handler(CommandHandler("unblock", unblock_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("update", update_command))
    app.add_handler(CommandHandler("online", online_command))
    app.add_handler(CommandHandler("addadmin", addadmin_command))
    app.add_handler(CommandHandler("removeadmin", removeadmin_command))
    app.add_handler(CommandHandler("admins", admins_command))
    app.add_handler(CommandHandler("setdiscount", setdiscount_command))
    app.add_handler(CommandHandler("discount", discount_command))
    app.add_handler(CommandHandler("secretary", secretary_command))
    app.add_handler(CommandHandler("mode", mode_command))
    app.add_handler(CommandHandler("secretary_on", secretary_toggle, has_args=False))
    app.add_handler(CommandHandler("secretary_off", secretary_toggle, has_args=False))
    app.add_handler(CommandHandler("faq_add", faq_add_command))
    app.add_handler(CommandHandler("faq_list", faq_list_command))
    app.add_handler(CommandHandler("faq_clear", faq_clear_command))
    app.add_handler(CommandHandler("pause", pause_resume_command))
    app.add_handler(CommandHandler("resume", pause_resume_command))
    app.add_handler(CommandHandler("orders", admin_orders))
    app.add_handler(CommandHandler("sendconfig", sendconfig_command))
    app.add_handler(CommandHandler("test_add", test_add_command))
    app.add_handler(CommandHandler("test_list", test_list_command))
    app.add_handler(CallbackQueryHandler(global_back_callback, pattern=r"^global_back$"))
    app.add_handler(CallbackQueryHandler(test_request_callback, pattern=r"^test:request$"))
    app.add_handler(CallbackQueryHandler(cancel_active_order_callback, pattern=r"^cancel_active_order:\d+$"))
    app.add_handler(CallbackQueryHandler(panel_callback, pattern=r"^(panel:\d+|back|back_to_panels|product:\d+)$"))
    app.add_handler(CallbackQueryHandler(order_callback, pattern=r"^(confirm_order|cancel_order)$"))
    app.add_handler(CallbackQueryHandler(renew_service_callback, pattern=r"^renew_service:\d+$"))
    app.add_handler(CallbackQueryHandler(review_callback, pattern=r"^review:(approve|reject):\d+$"))
    app.add_handler(CallbackQueryHandler(market_callback, pattern=r"^market:"))
    app.add_handler(CallbackQueryHandler(virtual_service_callback, pattern=r"^virtual:\d+$"))
    app.add_handler(MessageHandler(filters.PHOTO, market_photo_input))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_text))
    # Telegram Business updates are separate from normal bot-chat updates.
    app.add_handler(TypeHandler(Update, business_connection_update), group=1)
    app.add_handler(TypeHandler(Update, business_message_handler), group=2)
    app.add_error_handler(error_handler)

    # Railway exposes exactly one port; the health server must run beside polling.
    _start_health_server()

    print("Bot is running. Stop with Ctrl+C or the stop button in Pydroid.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

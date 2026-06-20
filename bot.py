"""
🌐 URL ➜ HTML — Pro Bot (Termux Compatible)
═══════════════════════════════════════════
URL dile bot full webpage er HTML source file
download kore pathiye dibe. Lightweight —
playwright/browser lagbe na, Termux-e 100% kaaj korbe.

Setup (Termux):
    pip install python-telegram-bot requests --upgrade

Run:
    python test.py
"""

import asyncio
import json
import logging
import os
import re
import time
import zipfile
import threading
from io import BytesIO
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from flask import Flask
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.error import TelegramError

# ═══════════════════════ CONFIG ═══════════════════════
BOT_TOKEN = "8928689890:AAHAQYcqITw6A6LzNUq1jvx4R4-eFRYc9ys"

# Telegram user IDs allowed to use the admin panel
ADMIN_IDS = [8600328303]  # <-- replace with your numeric Telegram user ID(s)

# Group where logs (new users, conversions, etc.) get posted
LOGS_GROUP_ID = -1003970225189

# Channels users must join before using the bot. Now stored persistently
# in BOT_DATA["force_join_channels"] so it survives restarts. Each entry:
# {"chat_id": "@channelusername or -100xxxx", "title": "Display Name", "url": "https://t.me/..."}

DATA_FILE = "bot_data.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG,
)

BOT_START_TIME = time.time()

# ═══════════════════════ HEALTH SERVER (Render) ═══════════════════════
flask_app = Flask(__name__)

@flask_app.route("/health")
def health():
    uptime_seconds = int(time.time() - BOT_START_TIME)
    hours, rem = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"
    return {
        "status": "ok",
        "uptime": uptime_str,
        "uptime_seconds": uptime_seconds,
    }

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)


# ═══════════════════════ PERSISTENT STORAGE ═══════════════════════
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("users", {})
                data.setdefault("stats", {"total_conversions": 0})
                data.setdefault("force_join_channels", [])
                return data
        except Exception:
            pass
    return {"users": {}, "stats": {"total_conversions": 0}, "force_join_channels": []}


def save_data(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"⚠️ Failed to save data: {e}")


BOT_DATA = load_data()


def get_force_join_channels() -> list:
    return BOT_DATA["force_join_channels"]


def register_user(user) -> bool:
    """Registers a user if new. Returns True if this user is new."""
    uid = str(user.id)
    is_new = uid not in BOT_DATA["users"]
    BOT_DATA["users"][uid] = {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "joined": BOT_DATA["users"].get(uid, {}).get(
            "joined", time.strftime("%Y-%m-%d %H:%M:%S")
        ),
        "last_seen": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_data(BOT_DATA)
    return is_new


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def send_log(context: ContextTypes.DEFAULT_TYPE, text: str):
    """Send a message to the logs group. Fails silently if not configured."""
    if not LOGS_GROUP_ID:
        return
    try:
        await context.bot.send_message(
            chat_id=LOGS_GROUP_ID, text=text, parse_mode="HTML"
        )
    except Exception as e:
        print(f"⚠️ Failed to send log: {e}")


async def log_activity(
    context: ContextTypes.DEFAULT_TYPE,
    user,
    action: str,
    detail: str = "",
    result: str = "",
):
    """Unified activity logger: logs every user action and the bot's
    response/outcome to the logs group in a consistent format."""
    username_part = f"@{user.username}" if user.username else "no username"
    lines = [
        f"📌 <b>{action}</b>",
        "",
        f"👤 <b>User:</b> {user.first_name} ({username_part})",
        f"🆔 <b>ID:</b> <code>{user.id}</code>",
    ]
    if detail:
        lines.append(f"💬 <b>Input:</b> <code>{detail[:500]}</code>")
    if result:
        lines.append(f"📤 <b>Bot Response:</b> {result}")
    lines.append(f"🕒 <b>Time:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}")
    await send_log(context, "\n".join(lines))


# ═══════════════════════ FORCE JOIN CHECK ═══════════════════════
def _normalize_chat_id(raw: str) -> str:
    """Accepts '@name', 'name', or numeric '-100...' and normalizes to
    a format get_chat_member accepts."""
    raw = raw.strip()
    if raw.lstrip("-").isdigit():
        return raw
    return raw if raw.startswith("@") else f"@{raw}"


async def check_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True if user has joined all required channels (or none configured)."""
    channels = get_force_join_channels()
    if not channels:
        return True

    user_id = update.effective_user.id
    not_joined = []

    for channel in channels:
        chat_id = _normalize_chat_id(str(channel["chat_id"]))
        try:
            member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status in ("left", "kicked"):
                not_joined.append(channel)
        except TelegramError as e:
            # IMPORTANT: this almost always means the bot itself is not an
            # admin in the target channel, so it can't see membership status.
            # Treat as "not verified yet" but log the real reason so the
            # admin can fix it (instead of silently always failing).
            print(f"⚠️ check_force_join: couldn't verify {chat_id} for user {user_id}: {e}")
            if LOGS_GROUP_ID:
                await send_log(
                    context,
                    "⚠️ <b>Force-Join Check Failed</b>\n\n"
                    f"Channel: <code>{chat_id}</code>\n"
                    f"Reason: <code>{str(e)[:200]}</code>\n"
                    "<i>Likely cause: the bot is not an admin in this channel.</i>",
                )
            not_joined.append(channel)

    if not_joined:
        buttons = [
            [InlineKeyboardButton(f"📢 {ch['title']}", url=ch["url"])]
            for ch in not_joined
        ]
        buttons.append(
            [SBtn("✅ I've Joined", style="success", callback_data="check_join")]
        )
        text = (
            "🔒 <b>Join Required</b>\n\n"
            "<blockquote>Please join the channel(s) below to use this bot, "
            "then tap <b>I've Joined</b>.</blockquote>"
        )
        if update.message:
            await update.message.reply_text(
                text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
            )
        elif update.callback_query:
            await update.callback_query.edit_message_text(
                text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
            )
        return False

    return True
class SBtn(InlineKeyboardButton):
    """InlineKeyboardButton with optional 'style' (primary/success/danger)
    and 'icon_custom_emoji_id' (premium custom emoji icon) fields,
    for Bot API 9.4+ clients. Falls back silently on older clients."""

    def __init__(self, text, style=None, icon_custom_emoji_id=None, **kwargs):
        super().__init__(text, **kwargs)
        self._style = style
        self._icon_custom_emoji_id = icon_custom_emoji_id

    def to_dict(self, recursive=True):
        d = super().to_dict(recursive=recursive)
        if self._style:
            d["style"] = self._style
        if self._icon_custom_emoji_id:
            d["icon_custom_emoji_id"] = self._icon_custom_emoji_id
        return d


# ═══════════════════════ KEYBOARDS ═══════════════════════
def main_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [SBtn("Convert URL", style="success", icon_custom_emoji_id="6228590336055186198", callback_data="convert")],
            [
                SBtn("SEO Report", style="primary", callback_data="seo"),
                SBtn("🧩 Tech Stack", style="primary", icon_custom_emoji_id="6228978596803774375", callback_data="tech"),
            ],
            [SBtn("Offline ZIP Package", style="primary", callback_data="zip")],
            [
                SBtn("Help", style="primary", icon_custom_emoji_id="5298805275967376925", callback_data="help"),
                SBtn("About", style="primary", icon_custom_emoji_id="5298805275967376925", callback_data="about"),
            ],
            [SBtn("Updates Channel", style="primary", icon_custom_emoji_id="6084902351196396155", url="https://t.me/SHUVOMODS6")],
        ]
    )


def back_keyboard():
    return InlineKeyboardMarkup(
        [[SBtn("🔙 Back to Menu", style="danger", callback_data="back")]]
    )


def result_keyboard():
    return InlineKeyboardMarkup(
        [
            [SBtn("🔁 Convert Another URL", style="success", callback_data="convert")],
            [SBtn("🔙 Main Menu", style="danger", callback_data="back")],
        ]
    )


def admin_panel_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                SBtn("📊 Stats", style="primary", callback_data="admin_stats"),
                SBtn("👥 Users", style="primary", callback_data="admin_users"),
            ],
            [SBtn("📢 Broadcast", style="success", callback_data="admin_broadcast")],
            [SBtn("🔗 Force Join Channels", style="primary", callback_data="admin_forcejoin")],
            [SBtn("➕ Add Force Join", style="success", callback_data="admin_addforcejoin")],
            [SBtn("🔙 Close Panel", style="danger", callback_data="admin_close")],
        ]
    )


def admin_back_keyboard():
    return InlineKeyboardMarkup(
        [[SBtn("🔙 Back to Admin Panel", style="danger", callback_data="admin_panel")]]
    )


# ═══════════════════════ START / MENU ═══════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # Save the user FIRST, regardless of force-join status, so broadcasts
    # and stats always include everyone who has ever pressed /start.
    is_new = register_user(user)
    if is_new:
        username_part = f"@{user.username}" if user.username else "no username"
        await send_log(
            context,
            "🆕 <b>New User Started The Bot</b>\n\n"
            f"👤 <b>Name:</b> {user.first_name}\n"
            f"🔗 <b>Username:</b> {username_part}\n"
            f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
            f"📅 <b>Joined:</b> {BOT_DATA['users'][str(user.id)]['joined']}",
        )

    if not await check_force_join(update, context):
        return

    text = (
        '<tg-emoji emoji-id="6136525109315245846">✨</tg-emoji> '
        "<b>Welcome to URL ➜ HTML Pro Bot</b> "
        '<tg-emoji emoji-id="6136525109315245846">✨</tg-emoji>\n\n'
        f'<tg-emoji emoji-id="6095851644468072524">👋</tg-emoji> '
        f"Hello, <b>{user.first_name}</b>!\n\n"
        "<blockquote>"
        '<tg-emoji emoji-id="6253414379442670769">✅</tg-emoji> '
        "I can fetch the full HTML source code of any website "
        "and turn it into a downloadable <b>.html</b> file!"
        "</blockquote>\n\n"
        '<tg-emoji emoji-id="6183532431153304141">👇</tg-emoji> '
        "<b>Choose an option below:</b>"
    )
    if update.message:
        await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=main_menu_keyboard()
        )
    else:
        await update.callback_query.edit_message_text(
            text, parse_mode="HTML", reply_markup=main_menu_keyboard()
        )


# ═══════════════════════ BUTTON HANDLER ═══════════════════════
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "convert":
        context.user_data["mode"] = "html"
        text = (
            '<tg-emoji emoji-id="6204235659578712061">😂</tg-emoji> '
            "<b>Send me the URL now</b>\n\n"
            "<blockquote>"
            '<tg-emoji emoji-id="6203763122981837713">👷</tg-emoji> Example:\n'
            "<code>https://example.com</code>"
            "</blockquote>\n\n"
            '<tg-emoji emoji-id="6339296880500936916">⚡️</tg-emoji> '
            "<b>Output:</b> Full page <b>.html</b> source file!"
        )
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=back_keyboard()
        )

    elif query.data == "seo":
        context.user_data["mode"] = "seo"
        text = (
            '<tg-emoji emoji-id="5188311512791393083">🔎</tg-emoji> '
            "<b>SEO / Page Report</b>\n\n"
            "<blockquote>"
            "Send me a URL — I'll check:\n"
            "• Title &amp; meta description\n"
            "• Heading structure (H1-H6)\n"
            "• Load time\n"
            "• Image &amp; link count"
            "</blockquote>"
        )
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=back_keyboard()
        )

    elif query.data == "tech":
        context.user_data["mode"] = "tech"
        text = (
            '<tg-emoji emoji-id="6228978596803774375">🧩</tg-emoji> '
            "<b>Tech Stack Detector</b>\n\n"
            "<blockquote>"
            "Send me a URL — I'll try to detect the platform/framework "
            "behind it (WordPress, Shopify, React, Next.js, etc.)"
            "</blockquote>"
        )
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=back_keyboard()
        )

    elif query.data == "zip":
        context.user_data["mode"] = "zip"
        text = (
            '<tg-emoji emoji-id="5773788281917412000">❤️</tg-emoji> '
            "<b>Offline ZIP Package</b>\n\n"
            "<blockquote>"
            "Send me a URL — I'll bundle the page's HTML along with its "
            "linked CSS, JS, and images into a single ZIP you can open "
            "offline in a browser."
            "</blockquote>\n\n"
            '<tg-emoji emoji-id="5280533308269155503">📝</tg-emoji> '
            "<i>Note: only same-page linked assets are fetched; "
            "very heavy sites may take longer.</i>"
        )
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=back_keyboard()
        )

    elif query.data == "help":
        text = (
            '<tg-emoji emoji-id="6334834495379736183">📖</tg-emoji> '
            "<b>How To Use</b>\n\n"
            "<blockquote>"
            '<tg-emoji emoji-id="5305763715692377402">1️⃣</tg-emoji> '
            "Tap the <b>Convert URL</b> button\n"
            '<tg-emoji emoji-id="5307907239380528763">2️⃣</tg-emoji> '
            "Send the target website link (with http/https)\n"
            '<tg-emoji emoji-id="6241992364890527679">3️⃣</tg-emoji> '
            "The bot will automatically download the full page HTML "
            "and send it as a file "
            '<tg-emoji emoji-id="6100395724162210221">✅</tg-emoji>'
            "</blockquote>\n\n"
            '<tg-emoji emoji-id="6028090581094240774">🥸</tg-emoji> '
            "<b>Note:</b> Source may be limited for JS-heavy or login-protected sites."
        )
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=back_keyboard()
        )

    elif query.data == "about":
        text = (
            '<tg-emoji emoji-id="6113971389935391397">👨‍💻</tg-emoji> '
            "<b>About This Bot</b>\n\n"
            "<blockquote>"
            '<tg-emoji emoji-id="6253414379442670769">✅</tg-emoji> '
            "<b>Built with:</b> python-telegram-bot + requests\n"
            '<tg-emoji emoji-id="6253414379442670769">✅</tg-emoji> '
            "<b>Function:</b> URL ➜ Full HTML source ➜ .html file\n"
            '<tg-emoji emoji-id="6253414379442670769">✅</tg-emoji> '
            "<b>Status:</b> "
            '<tg-emoji emoji-id="6332517244559430568">🟢</tg-emoji> '
            "Online &amp; Active"
            "</blockquote>\n\n"
            '<tg-emoji emoji-id="6253263677630188904">💡</tg-emoji>'
            '<tg-emoji emoji-id="6111809101535123919">🌩</tg-emoji>'
            '<tg-emoji emoji-id="6256026737465887830">💡</tg-emoji>'
            '<tg-emoji emoji-id="6255603215035797822">💡</tg-emoji>'
            '<tg-emoji emoji-id="6253795282912283121">💡</tg-emoji>'
            '<tg-emoji emoji-id="6255948350017768971">💡</tg-emoji>'
            '<tg-emoji emoji-id="6255801663999708585">💡</tg-emoji>\n\n'
            '<tg-emoji emoji-id="6082113830794567558">⚡️</tg-emoji> '
            "<i>Powered by your custom Telegram Bot</i>"
        )
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=back_keyboard()
        )

    elif query.data == "check_join":
        if await check_force_join(update, context):
            await start(update, context)

    elif query.data == "back":
        context.user_data["mode"] = None
        await start(update, context)


# ═══════════════════════ URL VALIDATION ═══════════════════════
URL_PATTERN = re.compile(
    r"^https?://"
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    r"[a-zA-Z]{2,}"
    r"(?::\d{1,5})?"
    r"(?:/[^\s]*)?$"
)


def is_valid_url(text: str) -> bool:
    """Strictly checks if text is a real, well-formed http(s) URL —
    not just plain text or a sentence that happens to contain 'http'."""
    if not text:
        return False
    text = text.strip()
    if " " in text or "\n" in text:
        return False
    if not URL_PATTERN.match(text):
        return False
    try:
        parsed = urlparse(text)
        if not parsed.scheme or not parsed.netloc:
            return False
        if "." not in parsed.netloc:
            return False
    except Exception:
        return False
    return True


def detect_blocked_page(html: str, status_code: int) -> str:
    """Returns a human-readable reason if the page looks like a bot-block/
    JS-challenge page instead of real content. Empty string if it looks fine."""
    lower = html.lower()
    if status_code in (403, 429, 503):
        return f"Site returned HTTP {status_code} (likely blocking automated requests)."
    if "checking your browser" in lower or "cf-browser-verification" in lower:
        return "Cloudflare bot-protection challenge page detected."
    if "captcha" in lower and len(html) < 5000:
        return "CAPTCHA / verification page detected."
    if "<body" not in lower and "<html" in lower and len(html) < 1000:
        return "Page returned almost no content (likely JS-only rendering)."
    return ""


# ═══════════════════════ HELPERS ═══════════════════════
def extract_title(html_content: str) -> str:
    title = "page"
    try:
        lower = html_content.lower()
        if "<title>" in lower:
            start_idx = lower.find("<title>") + 7
            end_idx = lower.find("</title>")
            raw_title = html_content[start_idx:end_idx].strip()
            cleaned = "".join(
                c for c in raw_title if c.isalnum() or c in (" ", "_", "-")
            ).strip()[:40]
            if cleaned:
                title = cleaned
    except Exception:
        pass
    return title


def get_real_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else "Not found"


def get_meta_description(html: str) -> str:
    m = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        html,
        re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
            html,
            re.IGNORECASE,
        )
    return m.group(1).strip()[:200] if m else "Not found"


def count_headings(html: str) -> dict:
    counts = {}
    for level in range(1, 7):
        counts[f"h{level}"] = len(
            re.findall(rf"<h{level}[\s>]", html, re.IGNORECASE)
        )
    return counts


def count_links_and_images(html: str) -> tuple:
    links = len(re.findall(r"<a\s[^>]*href=", html, re.IGNORECASE))
    images = len(re.findall(r"<img\s[^>]*src=", html, re.IGNORECASE))
    return links, images


def detect_tech_stack(html: str, headers: dict) -> list:
    detected = []
    lower = html.lower()
    server_header = headers.get("Server", "")
    powered_by = headers.get("X-Powered-By", "")

    signatures = [
        ("WordPress", ["wp-content", "wp-includes", "/wp-json/"]),
        ("Shopify", ["cdn.shopify.com", "shopify.com/s/"]),
        ("Wix", ["wix.com", "wixstatic.com"]),
        ("Squarespace", ["squarespace.com", "static1.squarespace"]),
        ("Next.js", ["__next", "_next/static"]),
        ("Nuxt.js", ["__nuxt", "_nuxt/"]),
        ("React", ["react-dom", "data-reactroot"]),
        ("Vue.js", ["__vue__", "v-cloak", "vue.js", "vue.min.js"]),
        ("Angular", ["ng-version", "ng-app"]),
        ("Bootstrap", ["bootstrap.min.css", "bootstrap.css"]),
        ("Tailwind CSS", ["tailwind"]),
        ("jQuery", ["jquery.min.js", "jquery.js"]),
        ("Cloudflare", ["cloudflare"]),
        ("Laravel", ["laravel_session", "/vendor/laravel"]),
        ("Django", ["csrfmiddlewaretoken"]),
        ("Magento", ["magento", "mage/cookies"]),
    ]

    for name, keywords in signatures:
        if any(k in lower for k in keywords):
            detected.append(name)

    if "php" in powered_by.lower():
        detected.append(f"PHP ({powered_by})")
    if server_header:
        detected.append(f"Server: {server_header}")

    if not detected:
        detected.append("Could not confidently detect a known stack")

    return list(dict.fromkeys(detected))  # de-duplicate, preserve order


CSS_URL_RE = re.compile(r'url\(\s*[\'"]?(.*?)[\'"]?\s*\)', re.IGNORECASE)

# Max total assets to fetch per site (safety limit so the bot doesn't hang forever)
MAX_ASSETS = 150


def _local_folder_for(category: str) -> str:
    return {"css": "css", "js": "js", "img": "images", "font": "fonts"}[category]


def _safe_filename(u: str, fallback_ext: str = "") -> str:
    path = urlparse(u).path
    name = path.split("/")[-1] or "file"
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80]
    if "." not in name and fallback_ext:
        name += fallback_ext
    return name or "file"


def build_offline_zip(html: str, base_url: str) -> tuple:
    """Crawls a single page's full asset graph (CSS, JS, images, fonts,
    favicon, and assets referenced inside CSS via url()), downloads
    everything, rewrites all references to local relative paths, and
    packs it into a ready-to-open offline ZIP.

    Returns (zip_bytes, asset_count).
    """
    soup = BeautifulSoup(html, "html.parser")

    # url -> (category, local_path)   shared so the same asset is only
    # downloaded once even if referenced multiple times
    seen: dict = {}
    downloaded: dict = {}

    def register(u: str, category: str) -> str:
        if not u or u.startswith("data:") or u.startswith("javascript:") or u.startswith("#"):
            return u
        full = urljoin(base_url, u.split("#")[0])
        if full in seen:
            return seen[full][1]
        if len(seen) >= MAX_ASSETS:
            return u
        ext_map = {"css": ".css", "js": ".js", "img": "", "font": ""}
        filename = _safe_filename(full, ext_map.get(category, ""))
        local_path = f"{_local_folder_for(category)}/{len(seen)}_{filename}"
        seen[full] = (category, local_path)
        return local_path

    for tag in soup.find_all("link", href=True):
        rel = " ".join(tag.get("rel", [])).lower()
        if "stylesheet" in rel:
            tag["href"] = register(tag["href"], "css")
        elif "icon" in rel:
            tag["href"] = register(tag["href"], "img")

    for tag in soup.find_all("script", src=True):
        tag["src"] = register(tag["src"], "js")

    for tag in soup.find_all("img"):
        if tag.get("src"):
            tag["src"] = register(tag["src"], "img")
        if tag.get("srcset"):
            parts = []
            for entry in tag["srcset"].split(","):
                bits = entry.strip().split(" ")
                if bits and bits[0]:
                    bits[0] = register(bits[0], "img")
                parts.append(" ".join(bits))
            tag["srcset"] = ", ".join(parts)

    for tag in soup.find_all(style=True):
        def _sub(m):
            return f"url('{register(m.group(1), 'img')}')"
        tag["style"] = CSS_URL_RE.sub(_sub, tag["style"])

    for tag in soup.find_all("style"):
        if tag.string:
            def _sub(m):
                return f"url('{register(m.group(1), 'img')}')"
            tag.string = CSS_URL_RE.sub(_sub, tag.string)

    def download_all_pending():
        for full_url, (category, local_path) in list(seen.items()):
            if full_url in downloaded:
                continue
            try:
                r = requests.get(full_url, headers=HEADERS, timeout=10)
                if r.status_code == 200:
                    content = r.content
                    if category == "css":
                        text = content.decode(r.encoding or "utf-8", errors="ignore")

                        def _css_sub(m):
                            ref = m.group(1)
                            if ref.startswith("data:"):
                                return m.group(0)
                            target = ref if ref.startswith("http") else urljoin(full_url, ref)
                            is_font = any(ref.lower().endswith(e) for e in (".woff", ".woff2", ".ttf", ".otf", ".eot"))
                            local = register(target, "font" if is_font else "img")
                            return f"url('../{local}')"

                        text = CSS_URL_RE.sub(_css_sub, text)
                        content = text.encode("utf-8")
                    downloaded[full_url] = content
            except Exception:
                continue

    download_all_pending()
    download_all_pending()  # second pass: pick up assets newly queued from inside CSS url()

    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", str(soup))
        for full_url, (category, local_path) in seen.items():
            content = downloaded.get(full_url)
            if content is not None:
                zf.writestr(local_path, content)

    zip_buffer.seek(0)
    return zip_buffer, len(downloaded)


# ═══════════════════════ URL HANDLER (HTML mode) ═══════════════════════
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not is_valid_url(url):
        await update.message.reply_text(
            "⚠️ <b>Invalid URL!</b>\n\n"
            "<blockquote>"
            "Please send a valid link starting with "
            "<code>http://</code> or <code>https://</code>"
            "</blockquote>",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    status_msg = await update.message.reply_text(
        "⏳ <b>Fetching page...</b>\n"
        "<blockquote>🔄 Please wait a moment...</blockquote>",
        parse_mode="HTML",
    )

    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        html_content = response.text
        status_code = response.status_code
    except requests.exceptions.Timeout:
        await status_msg.edit_text(
            "❌ <b>Error: Request Timed Out</b>\n\n"
            "<blockquote>Try again with a different URL.</blockquote>",
            parse_mode="HTML",
            reply_markup=result_keyboard(),
        )
        return
    except requests.exceptions.RequestException as e:
        await status_msg.edit_text(
            "❌ <b>Error Fetching URL</b>\n\n"
            f"<blockquote><code>{str(e)[:300]}</code></blockquote>",
            parse_mode="HTML",
            reply_markup=result_keyboard(),
        )
        return
    except Exception as e:
        await status_msg.edit_text(
            "❌ <b>Unexpected Error</b>\n\n"
            f"<blockquote><code>{str(e)[:300]}</code></blockquote>",
            parse_mode="HTML",
            reply_markup=result_keyboard(),
        )
        return

    title = extract_title(html_content)
    size_kb = round(len(html_content.encode("utf-8")) / 1024, 2)

    block_reason = detect_blocked_page(html_content, status_code)
    if block_reason:
        await status_msg.edit_text(
            "⚠️ <b>Could Not Fetch Real Page Content</b>\n\n"
            f"<blockquote>{block_reason}</blockquote>\n\n"
            "<i>This site likely blocks simple HTTP requests or requires "
            "JavaScript rendering, which this bot cannot bypass.</i>",
            parse_mode="HTML",
            reply_markup=result_keyboard(),
        )
        return

    html_buffer = BytesIO(html_content.encode("utf-8"))
    html_buffer.name = f"{title}.html"

    caption = (
        '<tg-emoji emoji-id="6098356404970593003">✅</tg-emoji> '
        "<b>Conversion Successful!</b>\n\n"
        "<blockquote>"
        f'<tg-emoji emoji-id="6183847471299433616">🔗</tg-emoji> <b>URL:</b> <code>{url}</code>\n'
        f'<tg-emoji emoji-id="6302921522570856335">📄</tg-emoji> <b>File:</b> {html_buffer.name}\n'
        f'<tg-emoji emoji-id="5341350320058410754">📦</tg-emoji> <b>Size:</b> {size_kb} KB\n'
        f'<tg-emoji emoji-id="5224450179368767019">🌎</tg-emoji> <b>Status Code:</b> {status_code}'
        "</blockquote>"
    )

    await update.message.reply_document(
        document=html_buffer,
        filename=html_buffer.name,
        caption=caption,
        parse_mode="HTML",
        reply_markup=result_keyboard(),
    )
    await status_msg.delete()

    user = update.effective_user
    BOT_DATA["stats"]["total_conversions"] = BOT_DATA["stats"].get("total_conversions", 0) + 1
    save_data(BOT_DATA)
    username_part = f"@{user.username}" if user.username else "no username"
    await send_log(
        context,
        "🔁 <b>HTML Conversion</b>\n\n"
        f"👤 <b>User:</b> {user.first_name} ({username_part})\n"
        f"🆔 <b>ID:</b> <code>{user.id}</code>\n"
        f"🔗 <b>URL:</b> <code>{url}</code>\n"
        f"📦 <b>Size:</b> {size_kb} KB",
    )


# ═══════════════════════ SEO REPORT HANDLER ═══════════════════════
async def handle_seo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not is_valid_url(url):
        await update.message.reply_text(
            "⚠️ <b>Invalid URL!</b> Please send a link starting with http:// or https://",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    status_msg = await update.message.reply_text(
        "⏳ <b>Analyzing page...</b>", parse_mode="HTML"
    )

    try:
        start_time = time.time()
        response = requests.get(url, headers=HEADERS, timeout=20)
        load_time = round(time.time() - start_time, 2)
        response.raise_for_status()
        html = response.text
    except Exception as e:
        await status_msg.edit_text(
            f"❌ <b>Error:</b> <code>{str(e)[:300]}</code>",
            parse_mode="HTML",
            reply_markup=result_keyboard(),
        )
        return

    page_title = get_real_title(html)
    meta_desc = get_meta_description(html)
    headings = count_headings(html)
    links, images = count_links_and_images(html)
    size_kb = round(len(html.encode("utf-8")) / 1024, 2)

    heading_line = "  ".join(
        f"H{lvl[1]}:{count}" for lvl, count in headings.items() if count > 0
    ) or "None found"

    text = (
        "🔎 <b>SEO / Page Report</b>\n\n"
        "<blockquote>"
        f"🔗 <b>URL:</b> <code>{url}</code>\n"
        f"⏱ <b>Load Time:</b> {load_time}s\n"
        f"📦 <b>Page Size:</b> {size_kb} KB\n"
        f"🌎 <b>Status:</b> {response.status_code}"
        "</blockquote>\n\n"
        f"📝 <b>Title:</b>\n<i>{page_title}</i>\n\n"
        f"📋 <b>Meta Description:</b>\n<i>{meta_desc}</i>\n\n"
        "<blockquote>"
        f"🔢 <b>Headings:</b> {heading_line}\n"
        f"🔗 <b>Links found:</b> {links}\n"
        f"🖼 <b>Images found:</b> {images}"
        "</blockquote>"
    )

    await status_msg.edit_text(
        text, parse_mode="HTML", reply_markup=result_keyboard()
    )

    user = update.effective_user
    await log_activity(
        context,
        user,
        action="🔎 SEO Report",
        detail=url,
        result=f"Title: {page_title[:80]} | Load: {load_time}s | Size: {size_kb} KB | Links: {links} | Images: {images}",
    )


# ═══════════════════════ TECH STACK HANDLER ═══════════════════════
async def handle_tech(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not is_valid_url(url):
        await update.message.reply_text(
            "⚠️ <b>Invalid URL!</b> Please send a link starting with http:// or https://",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    status_msg = await update.message.reply_text(
        "⏳ <b>Detecting tech stack...</b>", parse_mode="HTML"
    )

    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        html = response.text
    except Exception as e:
        await status_msg.edit_text(
            f"❌ <b>Error:</b> <code>{str(e)[:300]}</code>",
            parse_mode="HTML",
            reply_markup=result_keyboard(),
        )
        return

    detected = detect_tech_stack(html, response.headers)
    detected_lines = "\n".join(f"• {item}" for item in detected)

    text = (
        "🧩 <b>Tech Stack Detection</b>\n\n"
        f"🔗 <b>URL:</b> <code>{url}</code>\n\n"
        "<blockquote>"
        f"{detected_lines}"
        "</blockquote>\n\n"
        "<i>Detection is based on HTML/header signatures and may not be 100% accurate.</i>"
    )

    await status_msg.edit_text(
        text, parse_mode="HTML", reply_markup=result_keyboard()
    )

    user = update.effective_user
    await log_activity(
        context,
        user,
        action="🧩 Tech Stack Detection",
        detail=url,
        result=", ".join(detected),
    )


# ═══════════════════════ OFFLINE ZIP HANDLER ═══════════════════════
async def handle_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not is_valid_url(url):
        await update.message.reply_text(
            "⚠️ <b>Invalid URL!</b> Please send a link starting with http:// or https://",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    status_msg = await update.message.reply_text(
        "⏳ <b>Building offline package...</b>\n"
        "<blockquote>Fetching HTML and linked assets, this may take a bit longer...</blockquote>",
        parse_mode="HTML",
    )

    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        html = response.text
    except Exception as e:
        await status_msg.edit_text(
            f"❌ <b>Error fetching page:</b> <code>{str(e)[:300]}</code>",
            parse_mode="HTML",
            reply_markup=result_keyboard(),
        )
        return

    try:
        zip_buffer, asset_count = build_offline_zip(html, url)
    except Exception as e:
        await status_msg.edit_text(
            f"❌ <b>Error building ZIP:</b> <code>{str(e)[:300]}</code>",
            parse_mode="HTML",
            reply_markup=result_keyboard(),
        )
        return

    title = extract_title(html)
    zip_buffer.name = f"{title}_offline_package.zip"
    size_kb = round(zip_buffer.getbuffer().nbytes / 1024, 2)

    caption = (
        '<tg-emoji emoji-id="6053273602143293812">💥</tg-emoji> '
        "<b>Offline Package Ready!</b>\n\n"
        "<blockquote>"
        f'<tg-emoji emoji-id="6100307857721267700">🔗</tg-emoji> <b>URL:</b> <code>{url}</code>\n'
        f'<tg-emoji emoji-id="5341350320058410754">📦</tg-emoji> <b>ZIP Size:</b> {size_kb} KB\n'
        f'<tg-emoji emoji-id="6228978596803774375">🧩</tg-emoji> <b>Assets bundled:</b> {asset_count}\n'
        f'<tg-emoji emoji-id="6147464060305676048">😎</tg-emoji> <b>Entry point:</b> index.html'
        "</blockquote>\n\n"
        "<i>Extract and open index.html in any browser to view offline.</i>"
    )

    await update.message.reply_document(
        document=zip_buffer,
        filename=zip_buffer.name,
        caption=caption,
        parse_mode="HTML",
        reply_markup=result_keyboard(),
    )
    await status_msg.delete()

    user = update.effective_user
    await log_activity(
        context,
        user,
        action="📦 Offline ZIP Package",
        detail=url,
        result=f"ZIP Size: {size_kb} KB | Assets bundled: {asset_count}",
    )


# ═══════════════════════ ADMIN PANEL ═══════════════════════
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text(
            "🚫 <b>Access Denied</b>\n\n"
            "<blockquote>You are not authorized to use the admin panel.</blockquote>",
            parse_mode="HTML",
        )
        return

    await update.message.reply_text(
        "🛠 <b>Admin Panel</b>\n\n"
        "<blockquote>Manage bot users, broadcast messages, "
        "and configure force-join channels.</blockquote>",
        parse_mode="HTML",
        reply_markup=admin_panel_keyboard(),
    )


async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return

    await query.answer()

    if query.data == "admin_panel":
        context.user_data["admin_mode"] = None
        await query.edit_message_text(
            "🛠 <b>Admin Panel</b>\n\n"
            "<blockquote>Manage bot users, broadcast messages, "
            "and configure force-join channels.</blockquote>",
            parse_mode="HTML",
            reply_markup=admin_panel_keyboard(),
        )

    elif query.data == "admin_stats":
        total_users = len(BOT_DATA["users"])
        total_conversions = BOT_DATA["stats"].get("total_conversions", 0)
        text = (
            "📊 <b>Bot Statistics</b>\n\n"
            "<blockquote>"
            f"👥 <b>Total Users:</b> {total_users}\n"
            f"🔁 <b>Total Conversions:</b> {total_conversions}\n"
            f"🔗 <b>Force-Join Channels:</b> {len(get_force_join_channels())}"
            "</blockquote>"
        )
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=admin_back_keyboard()
        )

    elif query.data == "admin_users":
        users = list(BOT_DATA["users"].values())[-20:]
        if not users:
            lines = "No users yet."
        else:
            lines = "\n".join(
                f"• {u['first_name']} (@{u['username'] or 'N/A'}) — <code>{u['id']}</code>"
                for u in users
            )
        text = (
            "👥 <b>Recent Users (last 20)</b>\n\n"
            f"<blockquote>{lines}</blockquote>"
        )
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=admin_back_keyboard()
        )

    elif query.data == "admin_broadcast":
        context.user_data["admin_mode"] = "broadcast"
        await query.edit_message_text(
            "📢 <b>Broadcast Message</b>\n\n"
            "<blockquote>Send me anything now — text (bold/italic/quote "
            "formatting works), a photo, video, sticker, GIF, voice note, "
            "document, or message with premium emoji.\n\n"
            "It will be sent to all registered users exactly as you sent it.</blockquote>",
            parse_mode="HTML",
            reply_markup=admin_back_keyboard(),
        )

    elif query.data == "admin_forcejoin":
        channels = get_force_join_channels()
        if channels:
            lines = "\n".join(
                f"• {ch['title']} (<code>{ch['chat_id']}</code>)"
                for ch in channels
            )
        else:
            lines = "No force-join channels configured."
        text = (
            "🔗 <b>Force Join Channels</b>\n\n"
            f"<blockquote>{lines}</blockquote>\n\n"
            "<i>Use ➕ Add Force Join to add a channel, or tap a ❌ below to remove one.</i>"
        )
        buttons = [
            [SBtn(f"❌ Remove: {ch['title']}", style="danger", callback_data=f"admin_rmfj_{i}")]
            for i, ch in enumerate(channels)
        ]
        buttons.append([SBtn("🔙 Back to Admin Panel", style="danger", callback_data="admin_panel")])
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif query.data.startswith("admin_rmfj_"):
        idx = int(query.data.replace("admin_rmfj_", ""))
        channels = get_force_join_channels()
        if 0 <= idx < len(channels):
            removed = channels.pop(idx)
            save_data(BOT_DATA)
            await query.answer(f"Removed: {removed['title']}", show_alert=True)
        else:
            await query.answer("Already removed.", show_alert=True)
        # Re-render the updated list
        channels = get_force_join_channels()
        lines = "\n".join(
            f"• {ch['title']} (<code>{ch['chat_id']}</code>)" for ch in channels
        ) or "No force-join channels configured."
        text = (
            "🔗 <b>Force Join Channels</b>\n\n"
            f"<blockquote>{lines}</blockquote>\n\n"
            "<i>Use ➕ Add Force Join to add a channel, or tap a ❌ below to remove one.</i>"
        )
        buttons = [
            [SBtn(f"❌ Remove: {ch['title']}", style="danger", callback_data=f"admin_rmfj_{i}")]
            for i, ch in enumerate(channels)
        ]
        buttons.append([SBtn("🔙 Back to Admin Panel", style="danger", callback_data="admin_panel")])
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif query.data == "admin_addforcejoin":
        context.user_data["admin_mode"] = "addforcejoin_title"
        await query.edit_message_text(
            "➕ <b>Add Force Join Channel</b>\n\n"
            "<blockquote>Step 1/3: Send the <b>button title</b> that users will see.\n"
            "Example: <code>JOIN SHUVO</code></blockquote>",
            parse_mode="HTML",
            reply_markup=admin_back_keyboard(),
        )

    elif query.data == "admin_close":
        context.user_data["admin_mode"] = None
        await query.edit_message_text("🛠 Admin panel closed.")


async def handle_addforcejoin_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handles the multi-step Add Force Join flow."""
    user = update.effective_user
    if not is_admin(user.id):
        return False

    mode = context.user_data.get("admin_mode", "")

    if mode == "addforcejoin_title":
        # Step 1: got the button title
        context.user_data["fj_title"] = update.message.text.strip()
        context.user_data["admin_mode"] = "addforcejoin_link"
        await update.message.reply_text(
            "➕ <b>Add Force Join Channel</b>\n\n"
            "<blockquote>Step 2/3: Send the <b>channel invite link</b> (https://t.me/...).\n"
            "This will be the URL on the button.</blockquote>",
            parse_mode="HTML",
            reply_markup=admin_back_keyboard(),
        )
        return True

    elif mode == "addforcejoin_link":
        # Step 2: got the link
        context.user_data["fj_link"] = update.message.text.strip()
        context.user_data["admin_mode"] = "addforcejoin_chatid"
        await update.message.reply_text(
            "➕ <b>Add Force Join Channel</b>\n\n"
            "<blockquote>Step 3/3: Send the <b>channel username or chat ID</b>.\n"
            "Example: <code>@SHUVOMODS6</code> or <code>-1001234567890</code></blockquote>",
            parse_mode="HTML",
            reply_markup=admin_back_keyboard(),
        )
        return True

    elif mode == "addforcejoin_chatid":
        # Step 3: got the chat_id — add to list
        chat_id = _normalize_chat_id(update.message.text.strip())
        title = context.user_data.pop("fj_title", "Channel")
        link = context.user_data.pop("fj_link", "")
        context.user_data["admin_mode"] = None

        get_force_join_channels().append({
            "chat_id": chat_id,
            "title": title,
            "url": link,
        })
        save_data(BOT_DATA)

        await update.message.reply_text(
            "✅ <b>Force Join Channel Added!</b>\n\n"
            f"<blockquote>📢 <b>Button:</b> {title}\n"
            f"🔗 <b>Link:</b> {link}\n"
            f"🆔 <b>Chat ID:</b> <code>{chat_id}</code></blockquote>\n\n"
            "⚠️ <b>Important:</b> make sure this bot is added as an "
            "<b>admin</b> in that channel/group, otherwise it can't "
            "verify whether users joined.",
            parse_mode="HTML",
            reply_markup=admin_panel_keyboard(),
        )
        return True

    return False


async def handle_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Called when an admin is in broadcast mode and sends ANY message —
    text (with bold/italic/blockquote formatting), photo, video, sticker,
    GIF, voice note, document, or premium emoji. Uses copy_message so the
    exact content/formatting is replicated to every user, without a
    'Forwarded from' tag."""
    user = update.effective_user
    if not is_admin(user.id):
        return False

    if context.user_data.get("admin_mode") != "broadcast":
        return False

    context.user_data["admin_mode"] = None
    source_chat_id = update.effective_chat.id
    source_message_id = update.message.message_id

    status_msg = await update.message.reply_text(
        "📤 <b>Broadcasting...</b>", parse_mode="HTML"
    )

    sent, failed = 0, 0
    dead_uids = []
    for uid in list(BOT_DATA["users"].keys()):
        try:
            # copy_message preserves formatting (bold/italic/blockquote),
            # media (photo/video/sticker/gif/voice/document), and premium
            # emoji exactly as the admin sent them.
            await context.bot.copy_message(
                chat_id=int(uid),
                from_chat_id=source_chat_id,
                message_id=source_message_id,
            )
            sent += 1
        except Exception as e:
            failed += 1
            err = str(e).lower()
            if "blocked" in err or "not found" in err or "deactivated" in err:
                dead_uids.append(uid)
        # Telegram allows ~30 msgs/sec to different users; pace it to be safe.
        await asyncio.sleep(0.05)

    for uid in dead_uids:
        BOT_DATA["users"].pop(uid, None)
    if dead_uids:
        save_data(BOT_DATA)

    await status_msg.edit_text(
        "✅ <b>Broadcast Complete</b>\n\n"
        f"<blockquote>📨 Sent: {sent}\n❌ Failed: {failed}\n"
        f"🧹 Removed inactive: {len(dead_uids)}</blockquote>",
        parse_mode="HTML",
        reply_markup=admin_back_keyboard(),
    )

    await send_log(
        context,
        "📢 <b>Broadcast Sent</b>\n\n"
        f"👤 <b>By:</b> {user.first_name} (<code>{user.id}</code>)\n"
        f"📨 <b>Sent:</b> {sent} | ❌ <b>Failed:</b> {failed}",
    )
    return True


# ═══════════════════════ FALLBACK / DISPATCHER ═══════════════════════
async def fallback_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    # Admin add-force-join flow takes priority (text-only steps)
    if update.message.text and await handle_addforcejoin_text(update, context):
        return

    # Admin broadcast takes priority — works for ANY message type
    # (formatted text, photo, video, sticker, GIF, premium emoji, etc.)
    if await handle_broadcast_text(update, context):
        return

    # Everything below this point only makes sense for plain text messages
    if not update.message.text:
        return

    text = update.message.text.strip()
    is_url = is_valid_url(text)
    mode = context.user_data.get("mode")

    # Ignore plain conversation if not a URL and no active mode
    if not is_url and not mode:
        return

    register_user(update.effective_user)

    if not await check_force_join(update, context):
        return

    if mode == "seo":
        await handle_seo(update, context)
    elif mode == "tech":
        await handle_tech(update, context)
    elif mode == "html":
        await handle_url(update, context)
    else:
        # default mode: full website (HTML + CSS + JS + images + fonts) as ZIP
        await handle_zip(update, context)


# ═══════════════════════ MAIN ═══════════════════════
async def main():
    print("🔧 Initializing bot...")

    # Quick connectivity check before starting polling, so failures are visible
    try:
        test_resp = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=15
        )
        print(f"🔍 getMe check -> status {test_resp.status_code}: {test_resp.text}")
        if not test_resp.ok or not test_resp.json().get("ok"):
            print("❌ Bot token invalid or Telegram unreachable. Stopping here.")
            return
    except Exception as e:
        print(f"❌ Could not reach Telegram API: {e}")
        return

    # Start Flask health server in background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"🌐 Health server started on port {os.environ.get('PORT', 8080)}")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern=r"^admin_"))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, fallback_text))

    print("🤖 Bot is running... (press Ctrl+C to stop)")
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        # Keep running until interrupted
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())

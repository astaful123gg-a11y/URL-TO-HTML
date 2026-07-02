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
import glob
import json
import logging
import os
import re
import time
import zipfile
import threading
import posixpath
from io import BytesIO
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
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
BOT_TOKEN = "8928689890:AAGsbUN81nMwYAj8H5hjRU58UBGSC83qZFE"

# Telegram user IDs allowed to use the admin panel
ADMIN_IDS = [8600328303]  # <-- replace with your numeric Telegram user ID(s)

# Group where logs (new users, conversions, etc.) get posted
LOGS_GROUP_ID = -1003970225189

# Channels users must join before using the bot. Now stored persistently
# in BOT_DATA["force_join_channels"] so it survives restarts. Each entry:
# {"chat_id": "@channelusername or -100xxxx", "title": "Display Name", "url": "https://t.me/..."}

DATA_FILE = "bot_data.json"

# ── AI power (GitHub Models free inference API — GPT-4o-mini) ──
# Paste your GitHub PAT (needs "Models" read access) directly below.
GITHUB_TOKEN = "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"
AI_ENDPOINT = "https://models.github.ai/inference/chat/completions"
AI_MODEL = "openai/gpt-4o-mini"

# ── Real APK builder (Jipok/website-to-apk, needs the Dockerfile setup) ──
# Set by the Dockerfile's ENV. Empty locally -> URL TO APP falls back to a
# PWA zip instead of a real signed .apk (no Android SDK/Gradle available).
APK_BUILDER_DIR = os.environ.get("APK_BUILDER_DIR", "")
apk_build_lock = asyncio.Lock()  # only one Gradle build at a time (shared build dir)

POWERED_BY_TAG_JS = """// ==UserScript==
// @match *
// @run-at document-end
// ==/UserScript==
(function() {
  GM_addStyle(`
    #shuvo-powered-tag {
      position: fixed; bottom: 6px; right: 8px; z-index: 999999;
      background: rgba(0,0,0,0.55); color: #fff; font-size: 10px;
      padding: 3px 8px; border-radius: 10px; font-family: sans-serif;
      pointer-events: none; opacity: 0.85;
    }
  `);
  var tag = document.createElement('div');
  tag.id = 'shuvo-powered-tag';
  tag.textContent = 'POWERED BY SHUVO MODS';
  document.body.appendChild(tag);
})();
"""

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
            [SBtn("🌐 URL TO APP", style="primary", callback_data="urltoapp")],
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
            "<b>Output:</b> Full website, every internal page + assets, "
            "bundled as one offline ZIP (real browser rendering, "
            "auto-tries to clear Cloudflare JS checks)!"
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

    elif query.data == "urltoapp":
        context.user_data["mode"] = "urltoapp_url"
        text = (
            '<tg-emoji emoji-id="6339296880500936916">⚡️</tg-emoji> '
            "<b>URL TO APP</b>\n\n"
            "<blockquote>"
            "Send me the website URL you want to turn into an installable "
            "web-app (PWA)."
            "</blockquote>\n\n"
            '<tg-emoji emoji-id="5280533308269155503">📝</tg-emoji> '
            "<i>Example:</i> <code>https://example.com</code>"
        )
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=back_keyboard()
        )

    elif query.data == "urltoapp_skip":
        await handle_urltoapp_build(update, context, logo_bytes=None)

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


# ═══════════════════════ AI POWER (GitHub Models / GPT-4o-mini) ═══════════════════════
async def ai_analyze(prompt: str, system: str = "", max_tokens: int = 400) -> str:
    """Calls GitHub Models' free GPT-4o-mini inference endpoint.
    Runs the (blocking) HTTP call in a worker thread so it never freezes
    the bot for other users. Returns "" on any failure (missing token,
    rate-limited, network error) instead of raising — callers should treat
    an empty string as "AI unavailable right now" and just skip the AI
    section rather than breaking the whole report.
    """
    if not GITHUB_TOKEN:
        return ""

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    def _call():
        return requests.post(
            AI_ENDPOINT,
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "model": AI_MODEL,
                "messages": messages,
                "temperature": 0.4,
                "max_tokens": max_tokens,
            },
            timeout=30,
        )

    try:
        resp = await asyncio.to_thread(_call)
        if resp.status_code == 429:
            return "⚠️ AI quota busy right now (free tier rate limit) — try again in a bit."
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"⚠️ AI analyze failed: {e}")
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


def build_offline_zip(html: str, base_url: str, extra_asset_urls: set = None) -> tuple:
    """Crawls a single page's full asset graph (CSS, JS, images, fonts,
    favicon, and assets referenced inside CSS via url()), downloads
    everything, rewrites all references to local relative paths, and
    packs it into a ready-to-open offline ZIP.

    extra_asset_urls: optional set of resource URLs captured directly from
    real browser network traffic (covers JS-injected images, fetch()-loaded
    backgrounds, dynamically loaded fonts, etc. that static HTML/CSS
    parsing alone would never find).

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

    # ---- also queue assets captured from real browser network traffic ----
    # (JS-injected images, fetch()-loaded backgrounds, dynamic fonts, etc.)
    if extra_asset_urls:
        FONT_EXTS = (".woff", ".woff2", ".ttf", ".otf", ".eot")
        IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".avif")
        for u in extra_asset_urls:
            path = urlparse(u).path.lower()
            if path.endswith(".css"):
                register(u, "css")
            elif path.endswith(".js"):
                register(u, "js")
            elif path.endswith(FONT_EXTS):
                register(u, "font")
            elif path.endswith(IMG_EXTS) or not path.split("/")[-1].count("."):
                register(u, "img")
            else:
                register(u, "img")

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
        "<blockquote>🔄 Opening in a real browser so JS-protected / "
        "Cloudflare-checked pages load properly...</blockquote>",
        parse_mode="HTML",
    )

    html_content = None
    status_code = 200

    # Try a real headless browser first — it executes JS, so it clears
    # simple Cloudflare "checking your browser / just a moment" challenges
    # on its own, unlike a plain requests.get() which always gets stuck.
    try:
        html_content, _assets, status_code = await render_full_page(url)
    except Exception:
        html_content = None  # fall through to plain requests below

    if html_content is None:
        try:
            response = await asyncio.to_thread(requests.get, url, headers=HEADERS, timeout=20)
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
            "<i>This bot already tries a real headless browser (which passes "
            "simple Cloudflare JS checks). If you're still seeing this, the "
            "site is using an interactive CAPTCHA (Turnstile/hCaptcha/"
            "reCAPTCHA) that requires an actual human click — no bot, this "
            "one included, can solve that automatically.</i>",
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
        response = await asyncio.to_thread(requests.get, url, headers=HEADERS, timeout=20)
        load_time = round(time.time() - start_time, 2)
        response.raise_for_status()
        html = response.text
    except Exception as e:
        await status_msg.edit_text(
            f"❌ <b>Error:</b> <code>{str(e)[:300]}</code>",
            parse_mode="HTML",
            reply_markup=result_keyboard(),
        )
        await log_activity(
            context, update.effective_user, action="❌ SEO Report FAILED",
            detail=url, result=str(e)[:300],
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

    # AI power: ask GPT-4o-mini for a quick human-readable SEO verdict based
    # on the extracted data (not the raw HTML — cheaper/faster and stays
    # well under the free-tier token limit). Skipped silently if no
    # GITHUB_TOKEN is configured or the free quota is currently exhausted.
    ai_note = await ai_analyze(
        system="You are a concise SEO auditor. Reply in 3-4 short bullet "
        "points, no intro/outro text, plain language.",
        prompt=(
            f"URL: {url}\nTitle: {page_title}\nMeta description: {meta_desc}\n"
            f"Headings: {heading_line}\nLinks: {links}\nImages: {images}\n"
            f"Load time: {load_time}s\n\n"
            "Give quick SEO verdict + top fixes."
        ),
        max_tokens=250,
    )
    if ai_note:
        try:
            await status_msg.edit_text(
                text + f"\n\n🤖 <b>AI Verdict:</b>\n<blockquote>{ai_note}</blockquote>",
                parse_mode="HTML",
                reply_markup=result_keyboard(),
            )
        except Exception:
            pass

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
        response = await asyncio.to_thread(requests.get, url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        html = response.text
    except Exception as e:
        await status_msg.edit_text(
            f"❌ <b>Error:</b> <code>{str(e)[:300]}</code>",
            parse_mode="HTML",
            reply_markup=result_keyboard(),
        )
        await log_activity(
            context, update.effective_user, action="❌ Tech Detect FAILED",
            detail=url, result=str(e)[:300],
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

    ai_note = await ai_analyze(
        system="You are a concise web tech analyst. Reply in 2-3 short "
        "bullet points, no intro/outro text, plain language.",
        prompt=(
            f"URL: {url}\nDetected tech signatures: {', '.join(detected)}\n\n"
            "Briefly note anything notable about this stack "
            "(performance, common security concerns, or dev notes)."
        ),
        max_tokens=200,
    )
    if ai_note:
        try:
            await status_msg.edit_text(
                text + f"\n\n🤖 <b>AI Insight:</b>\n<blockquote>{ai_note}</blockquote>",
                parse_mode="HTML",
                reply_markup=result_keyboard(),
            )
        except Exception:
            pass

    user = update.effective_user
    await log_activity(
        context,
        user,
        action="🧩 Tech Stack Detection",
        detail=url,
        result=", ".join(detected),
    )


# ═══════════════════════ OFFLINE ZIP HANDLER ═══════════════════════
async def render_full_page(url: str) -> tuple:
    """Loads the page in a real (headless) browser so JavaScript-rendered
    content, lazy-loaded images, and dynamically-injected CSS/JS all show
    up — instead of the bare server HTML that requests.get() would return.
    A real browser also clears simple Cloudflare "checking your browser /
    just a moment" JS challenges on its own, since it actually executes
    the JS — a plain requests.get() can never do that.

    Returns (final_html, network_asset_urls, status_code).
    """
    network_urls = set()
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        try:
            page = await browser.new_page(user_agent=HEADERS.get("User-Agent"))

            def _track(request):
                if request.resource_type in ("stylesheet", "script", "image", "font", "media"):
                    network_urls.add(request.url)

            page.on("request", _track)

            response = await page.goto(url, wait_until="networkidle", timeout=30000)
            status_code = response.status if response else 200

            # If we landed on a Cloudflare JS challenge ("Just a moment...",
            # "Checking your browser..."), give it a few extra seconds — a
            # real browser usually clears it and auto-redirects once the JS
            # check finishes.
            try:
                early_html = (await page.content()).lower()
                if "just a moment" in early_html or "checking your browser" in early_html:
                    await page.wait_for_timeout(6000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass
            except Exception:
                pass

            # give lazy-loaded / scroll-triggered content a moment to fire
            await page.wait_for_timeout(1500)
            html = await page.content()
        finally:
            await browser.close()
    return html, network_urls, status_code


MAX_SITE_PAGES = 25
MAX_SITE_DEPTH = 2
MAX_SITE_ASSETS = 300


def _page_local_path(url: str) -> str:
    """Maps a page URL to a local .html file path inside the ZIP, preserving
    the site's folder structure (e.g. /blog/post-1 -> blog/post-1/index.html)."""
    path = urlparse(url).path.strip("/")
    if not path:
        return "index.html"
    if path.lower().endswith((".html", ".htm")):
        return re.sub(r"[^A-Za-z0-9/_.-]", "_", path)
    return re.sub(r"[^A-Za-z0-9/_-]", "_", path) + "/index.html"


async def crawl_full_site(start_url: str, progress_cb=None) -> tuple:
    """Crawls every same-domain internal link from start_url (BFS, capped by
    MAX_SITE_PAGES/MAX_SITE_DEPTH), rendering each page with a real browser
    so JS-built content is captured. All pages share ONE asset pool, so a
    CSS/JS/image/font file used across 20 pages is only downloaded once.
    Internal <a> links are rewritten to point at the local copy of the
    target page, so the whole offline site is fully click-through-able.

    Returns (zip_bytes, page_count, asset_count).
    """
    base_netloc = urlparse(start_url).netloc
    visited = set()
    queue = [(start_url, 0)]
    pages = {}  # normalized_url -> {"soup": ..., "local": ..., "html_url": ...}

    seen = {}        # full_asset_url -> (category, local_path)
    downloaded = {}  # full_asset_url -> bytes

    def register(u, category, current_page_url):
        if not u or u.startswith(("data:", "javascript:", "#", "mailto:", "tel:")):
            return u
        full = urljoin(current_page_url, u.split("#")[0])
        if full in seen:
            return seen[full][1]
        if len(seen) >= MAX_SITE_ASSETS:
            return u
        folder = {"css": "css", "js": "js", "img": "images", "font": "fonts"}[category]
        ext_map = {"css": ".css", "js": ".js", "img": "", "font": ""}
        raw_name = urlparse(full).path.split("/")[-1] or "file"
        name = re.sub(r"[^A-Za-z0-9._-]", "_", raw_name)[:80]
        if "." not in name and ext_map.get(category):
            name += ext_map[category]
        local_path = f"{folder}/{len(seen)}_{name}"  # canonical path, relative to ZIP root
        seen[full] = (category, local_path)
        return local_path

    def rel_for_page(asset_local_path: str, page_local_path: str) -> str:
        """Relative path from a given page's location to a top-level asset."""
        return posixpath.relpath(asset_local_path, posixpath.dirname(page_local_path) or ".")

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        try:
            page = await browser.new_page(user_agent=HEADERS.get("User-Agent"))
            network_urls = set()

            def _track(request):
                if request.resource_type in ("stylesheet", "script", "image", "font", "media"):
                    network_urls.add(request.url)

            page.on("request", _track)

            while queue and len(visited) < MAX_SITE_PAGES:
                url, depth = queue.pop(0)
                norm = url.split("#")[0].rstrip("/")
                if norm in visited:
                    continue
                visited.add(norm)

                try:
                    await page.goto(url, wait_until="networkidle", timeout=25000)
                    await page.wait_for_timeout(1000)
                    html = await page.content()
                except Exception:
                    continue

                soup = BeautifulSoup(html, "html.parser")
                pages[norm] = {"soup": soup, "local": _page_local_path(url), "url": url}

                if progress_cb:
                    await progress_cb(len(visited), url)

                if depth < MAX_SITE_DEPTH:
                    for a in soup.find_all("a", href=True):
                        href = a["href"]
                        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                            continue
                        full = urljoin(url, href.split("#")[0])
                        if urlparse(full).netloc != base_netloc:
                            continue
                        if full.split("#")[0].rstrip("/") not in visited:
                            queue.append((full, depth + 1))

            asset_network_urls = set(network_urls)
        finally:
            await browser.close()

    # ---- register assets + rewrite tags for every crawled page ----
    for norm, info in pages.items():
        soup, page_url, page_local = info["soup"], info["url"], info["local"]

        def reg_rel(u, category):
            canonical = register(u, category, page_url)
            if canonical.startswith(("http", "//")) or canonical == u:
                return canonical  # external / data / unregistered
            return rel_for_page(canonical, page_local)

        for tag in soup.find_all("link", href=True):
            rel = " ".join(tag.get("rel", [])).lower()
            if "stylesheet" in rel:
                tag["href"] = reg_rel(tag["href"], "css")
            elif "icon" in rel:
                tag["href"] = reg_rel(tag["href"], "img")

        for tag in soup.find_all("script", src=True):
            tag["src"] = reg_rel(tag["src"], "js")

        for tag in soup.find_all("img"):
            if tag.get("src"):
                tag["src"] = reg_rel(tag["src"], "img")
            if tag.get("srcset"):
                parts = []
                for entry in tag["srcset"].split(","):
                    bits = entry.strip().split(" ")
                    if bits and bits[0]:
                        bits[0] = reg_rel(bits[0], "img")
                    parts.append(" ".join(bits))
                tag["srcset"] = ", ".join(parts)

        for tag in soup.find_all(style=True):
            def _sub(m):
                return f"url('{reg_rel(m.group(1), 'img')}')"
            tag["style"] = CSS_URL_RE.sub(_sub, tag["style"])

        for tag in soup.find_all("style"):
            if tag.string:
                def _sub(m):
                    return f"url('{reg_rel(m.group(1), 'img')}')"
                tag.string = CSS_URL_RE.sub(_sub, tag.string)

        # rewrite internal <a href> links to point at the local copy
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            full = urljoin(page_url, href.split("#")[0])
            target_norm = full.rstrip("/")
            if urlparse(full).netloc == base_netloc and target_norm in pages:
                target_local = pages[target_norm]["local"]
                rel = posixpath.relpath(target_local, posixpath.dirname(info["local"]) or ".")
                a["href"] = rel

    for u in asset_network_urls:
        path = urlparse(u).path.lower()
        if path.endswith(".css"):
            register(u, "css", start_url)
        elif path.endswith(".js"):
            register(u, "js", start_url)
        elif path.endswith((".woff", ".woff2", ".ttf", ".otf", ".eot")):
            register(u, "font", start_url)
        else:
            register(u, "img", start_url)

    # ---- download every unique asset once ----
    async def download_one(session_url, category, local_path):
        try:
            r = await asyncio.to_thread(requests.get, session_url, headers=HEADERS, timeout=10)
            if r.status_code != 200:
                return
            content = r.content
            if category == "css":
                text = content.decode(r.encoding or "utf-8", errors="ignore")

                def _css_sub(m):
                    ref = m.group(1)
                    if ref.startswith("data:"):
                        return m.group(0)
                    target = ref if ref.startswith("http") else urljoin(session_url, ref)
                    is_font = ref.lower().endswith((".woff", ".woff2", ".ttf", ".otf", ".eot"))
                    local = register(target, "font" if is_font else "img", session_url)
                    # local is e.g. "fonts/3_a.woff2"; this CSS file lives in css/,
                    # so go up one level then into the target folder.
                    return f"url('../{local}')"

                text = CSS_URL_RE.sub(_css_sub, text)
                content = text.encode("utf-8")
            downloaded[session_url] = content
        except Exception:
            pass

    for _ in range(2):  # second pass picks up assets newly queued from CSS url()
        for full_url, (category, local_path) in list(seen.items()):
            if full_url not in downloaded:
                await download_one(full_url, category, local_path)

    # ---- write the ZIP ----
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for norm, info in pages.items():
            zf.writestr(info["local"], str(info["soup"]))
        for full_url, (category, local_path) in seen.items():
            content = downloaded.get(full_url)
            if content is not None:
                zf.writestr(local_path, content)

    zip_buffer.seek(0)
    return zip_buffer, len(pages), len(downloaded)


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
        "<blockquote>Rendering the page in a real browser so JS-loaded "
        "content/assets are captured too — this can take 10-20s...</blockquote>",
        parse_mode="HTML",
    )

    network_assets = set()
    try:
        html, network_assets, _status_code = await render_full_page(url)
    except Exception as e:
        # Fall back to plain requests fetch if the headless browser fails
        # (e.g. site blocks headless browsers) — better a partial result
        # than none.
        try:
            response = await asyncio.to_thread(requests.get, url, headers=HEADERS, timeout=20)
            response.raise_for_status()
            html = response.text
            await status_msg.edit_text(
                "⚠️ <i>Headless render failed, fell back to plain HTML fetch "
                f"(some JS-loaded content may be missing): </i><code>{str(e)[:150]}</code>",
                parse_mode="HTML",
            )
        except Exception as e2:
            await status_msg.edit_text(
                f"❌ <b>Error fetching page:</b> <code>{str(e2)[:300]}</code>",
                parse_mode="HTML",
                reply_markup=result_keyboard(),
            )
            return

    try:
        zip_buffer, asset_count = await asyncio.to_thread(
            build_offline_zip, html, url, network_assets
        )
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


async def handle_fullsite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """MAX POWER mode: crawls the entire site (up to MAX_SITE_PAGES internal
    pages, MAX_SITE_DEPTH deep), renders each with a real browser, and bundles
    everything — every page + every shared CSS/JS/image/font — into one ZIP
    with fully working offline navigation."""
    url = update.message.text.strip()
    if not is_valid_url(url):
        await update.message.reply_text(
            "⚠️ <b>Invalid URL!</b> Please send a link starting with http:// or https://",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    status_msg = await update.message.reply_text(
        "🚀 <b>MAX MODE: Crawling full website...</b>\n"
        "<blockquote>Rendering pages in a real browser, following internal "
        f"links (up to {MAX_SITE_PAGES} pages, {MAX_SITE_DEPTH} levels deep). "
        "This can take 1-3 minutes for larger sites.</blockquote>",
        parse_mode="HTML",
    )

    last_edit_time = [0.0]

    async def progress_cb(count, page_url):
        now = time.time()
        if now - last_edit_time[0] < 2:  # don't hit Telegram edit rate limits
            return
        last_edit_time[0] = now
        try:
            await status_msg.edit_text(
                f"🚀 <b>MAX MODE: Crawling full website...</b>\n"
                f"<blockquote>📄 Page {count}/{MAX_SITE_PAGES}: "
                f"<code>{page_url[:60]}</code></blockquote>",
                parse_mode="HTML",
            )
        except Exception:
            pass

    user = update.effective_user
    username_part = f"@{user.username}" if user.username else "no username"
    await send_log(
        context,
        "🚀 <b>Full Site Crawl STARTED</b>\n\n"
        f"👤 <b>User:</b> {user.first_name} ({username_part})\n"
        f"🆔 <b>ID:</b> <code>{user.id}</code>\n"
        f"🔗 <b>URL:</b> <code>{url}</code>\n"
        "<i>Runs in the background — other users are not blocked.</i>",
    )
    job_start = time.time()

    try:
        zip_buffer, page_count, asset_count = await crawl_full_site(url, progress_cb=progress_cb)
    except Exception as e:
        await status_msg.edit_text(
            f"❌ <b>Error crawling site:</b> <code>{str(e)[:300]}</code>",
            parse_mode="HTML",
            reply_markup=result_keyboard(),
        )
        await send_log(
            context,
            "❌ <b>Full Site Crawl FAILED</b>\n\n"
            f"👤 <b>User:</b> {user.first_name} ({username_part}) | <code>{user.id}</code>\n"
            f"🔗 <b>URL:</b> <code>{url}</code>\n"
            f"⚠️ <b>Error:</b> <code>{str(e)[:300]}</code>",
        )
        return

    job_duration = round(time.time() - job_start, 1)

    zip_buffer.name = f"{urlparse(url).netloc}_full_site.zip"
    size_kb = round(zip_buffer.getbuffer().nbytes / 1024, 2)
    size_mb = round(size_kb / 1024, 2)

    caption = (
        '<tg-emoji emoji-id="6053273602143293812">💥</tg-emoji> '
        "<b>Full Website Downloaded!</b>\n\n"
        "<blockquote>"
        f'🔗 <b>URL:</b> <code>{url}</code>\n'
        f'📄 <b>Pages captured:</b> {page_count}\n'
        f'🧩 <b>Assets bundled:</b> {asset_count}\n'
        f'📦 <b>ZIP Size:</b> {size_mb} MB\n'
        f'😎 <b>Entry point:</b> index.html'
        "</blockquote>\n\n"
        "<i>Extract and open index.html — internal links between pages work offline too.</i>"
    )

    # Telegram bot API caps regular document uploads around 50MB
    if zip_buffer.getbuffer().nbytes > 49 * 1024 * 1024:
        await status_msg.edit_text(
            f"⚠️ <b>Site too large ({size_mb} MB)</b> to send via Telegram (50MB limit).\n"
            "Try the single-page 📦 Offline ZIP option instead, or ask for a smaller page limit.",
            parse_mode="HTML",
            reply_markup=result_keyboard(),
        )
        return

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
        action="🚀 Full Website Crawl DONE",
        detail=url,
        result=f"Pages: {page_count} | Assets: {asset_count} | Size: {size_mb} MB | Took: {job_duration}s",
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


# ═══════════════════════ URL TO APP (real .apk via website-to-apk) ═══════════════════════
async def build_real_apk(url: str, app_name: str, app_id: str, icon_bytes: bytes = None) -> str:
    """Builds a REAL signed WebView .apk using the Jipok/website-to-apk
    toolchain (Java 17 + Android SDK + Gradle), which the Dockerfile
    installs to APK_BUILDER_DIR. Returns the path to the built .apk, or ""
    if the toolchain isn't present (e.g. running locally without Docker) or
    the build fails/times out — caller should fall back to the PWA package.

    Runs via asyncio subprocess (never blocks the event loop) and is
    serialized behind apk_build_lock since every build reuses the same
    on-disk Gradle project — two builds at once would corrupt each other.
    """
    if not APK_BUILDER_DIR or not os.path.isdir(APK_BUILDER_DIR):
        return ""

    icon_path = os.path.join(APK_BUILDER_DIR, "shuvo_bot_icon.png")
    if icon_bytes:
        with open(icon_path, "wb") as f:
            f.write(icon_bytes)
    else:
        default_icon = os.path.join(APK_BUILDER_DIR, "default_icon.png")
        icon_path = default_icon if os.path.exists(default_icon) else icon_path

    scripts_dir = os.path.join(APK_BUILDER_DIR, "scripts")
    os.makedirs(scripts_dir, exist_ok=True)
    with open(os.path.join(scripts_dir, "shuvo_tag.js"), "w") as f:
        f.write(POWERED_BY_TAG_JS)

    conf_text = f"""id = {app_id}
name = {app_name}
mainURL = {url}
icon = {icon_path}

allowSubdomains = true
requireDoubleBackToExit = true
enableExternalLinks = false
allowOpenMobileApp = false
confirmOpenExternalApp = true

JSEnabled = true
JSCanOpenWindowsAutomatically = false
DomStorageEnabled = true
DatabaseEnabled = true
SavePassword = false
AllowFileAccess = false
AllowFileAccessFromFileURLs = false
blockLocalhostRequests = true
trustUserCA = false
geolocationEnabled = false
cameraEnabled = false
microphoneEnabled = false
allowMixedContent = false
showDetailsOnErrorScreen = false

scripts = scripts/shuvo_tag.js
"""
    conf_path = os.path.join(APK_BUILDER_DIR, "webapk.conf")
    with open(conf_path, "w") as f:
        f.write(conf_text)

    async with apk_build_lock:
        try:
            proc = await asyncio.create_subprocess_exec(
                "./make.sh", "build", "webapk.conf",
                cwd=APK_BUILDER_DIR,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=280)
        except (asyncio.TimeoutError, Exception) as e:
            print(f"⚠️ APK build error/timeout: {e}")
            try:
                proc.kill()
            except Exception:
                pass
            return ""

        if proc.returncode != 0:
            print(f"⚠️ APK build failed:\n{stderr.decode(errors='ignore')[:1000]}")
            return ""

        apks = sorted(
            glob.glob(os.path.join(APK_BUILDER_DIR, "**", "*.apk"), recursive=True),
            key=os.path.getmtime, reverse=True,
        )
        return apks[0] if apks else ""



def build_pwa_package(url: str, logo_bytes: bytes = None) -> tuple:
    """Builds an installable PWA (Progressive Web App) package for the given
    URL — a zip containing manifest.json, a fullscreen wrapper index.html,
    a minimal service worker, and an app icon.

    NOTE: this is a real PWA (installable via browser "Add to Home Screen"
    once hosted on HTTPS static hosting like GitHub Pages/Netlify/Vercel),
    NOT a native .apk — building a real .apk needs Android SDK/build-tools
    + a keystore, which this Python-only bot environment doesn't have.

    Returns (zip_buffer, app_name).
    """
    parsed = urlparse(url)
    app_name = parsed.netloc.replace("www.", "") or "My App"

    has_logo = logo_bytes is not None
    icon_filename = "icon.jpg" if has_logo else "icon.svg"
    icon_mime = "image/jpeg" if has_logo else "image/svg+xml"

    if not has_logo:
        initial = (app_name[0] if app_name else "A").upper()
        logo_bytes = f"""<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512">
<rect width="512" height="512" fill="#5865F2"/>
<text x="50%" y="50%" font-size="260" fill="#ffffff" text-anchor="middle"
 dominant-baseline="central" font-family="Arial, sans-serif">{initial}</text>
</svg>""".encode("utf-8")

    manifest = {
        "name": app_name,
        "short_name": app_name[:12],
        "start_url": "index.html",
        "display": "standalone",
        "background_color": "#0f0f0f",
        "theme_color": "#5865F2",
        "icons": [
            {"src": icon_filename, "sizes": "512x512", "type": icon_mime},
        ],
    }

    index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{app_name}</title>
<link rel="manifest" href="manifest.json">
<link rel="apple-touch-icon" href="{icon_filename}">
<meta name="theme-color" content="#5865F2">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<style>
  html, body {{ margin:0; padding:0; height:100%; overflow:hidden; background:#0f0f0f; }}
  iframe {{ border:0; width:100%; height:100%; }}
</style>
</head>
<body>
<iframe src="{url}" allow="fullscreen; geolocation; camera; microphone"></iframe>
<script>
if ("serviceWorker" in navigator) {{
  window.addEventListener("load", () => {{
    navigator.serviceWorker.register("service-worker.js").catch(() => {{}});
  }});
}}
</script>
</body>
</html>"""

    service_worker = f"""const CACHE_NAME = "app-shell-v1";
const SHELL_FILES = ["index.html", "manifest.json", "{icon_filename}"];
self.addEventListener("install", (e) => {{
  e.waitUntil(caches.open(CACHE_NAME).then((c) => c.addAll(SHELL_FILES)));
}});
self.addEventListener("fetch", (e) => {{
  e.respondWith(
    caches.match(e.request).then((r) => r || fetch(e.request))
  );
}});"""

    readme = f"""URL TO APP — {app_name}
========================================

Eta ekTa PWA (Progressive Web App) package — REAL native .apk na, karon
.apk build korte Android SDK + build-tools + keystore lage, ja ei bot
environment e nai.

Kivabe "install" korbe (real app-er moto homescreen icon pabe):
1. Ei zip er shob file (index.html, manifest.json, service-worker.js,
   {icon_filename}) kono FREE static hosting e upload koro:
   - GitHub Pages (pages.github.com)
   - Netlify (netlify.com) — drag & drop e deploy hoy
   - Vercel (vercel.com)
2. Hosted URL ta mobile Chrome (Android) othoba Safari (iPhone) e open koro.
3. Browser menu theke "Add to Home Screen" / "Install App" tap koro.
4. Ekhon homescreen e ekTa app icon pabe — full screen e {url} open hobe,
   address bar chara, real app-er moto.

Target website: {url}
"""

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", index_html)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("service-worker.js", service_worker)
        zf.writestr(icon_filename, logo_bytes)
        zf.writestr("README.txt", readme)
    buf.seek(0)
    return buf, app_name


async def handle_urltoapp_build(update: Update, context: ContextTypes.DEFAULT_TYPE, logo_bytes: bytes = None):
    url = context.user_data.get("urltoapp_target_url")
    if not url:
        await update.effective_message.reply_text(
            "⚠️ URL pawa jayni, <b>URL TO APP</b> button diye abar shuru koro.",
            parse_mode="HTML",
        )
        return

    status_msg = await update.effective_message.reply_text(
        "⏳ <b>Building your app...</b>\n"
        "<blockquote>Real .apk hole 1-3 minute lagte pare, wait koro.</blockquote>",
        parse_mode="HTML",
    )

    parsed = urlparse(url)
    app_name = (parsed.netloc.replace("www.", "") or "App").split(".")[0]
    app_id = re.sub(r"[^a-z0-9]", "", app_name.lower())[:20] or "shuvoapp"

    apk_path = await build_real_apk(url, app_name, app_id, icon_bytes=logo_bytes)

    if apk_path:
        await status_msg.edit_text(
            "✅ <b>Real .apk ready!</b>\n\n"
            "<blockquote>Install kore open korle full-screen app "
            "hisebe open hobe — no address bar, no browser UI.</blockquote>",
            parse_mode="HTML",
        )
        with open(apk_path, "rb") as f:
            await update.effective_message.reply_document(
                document=f, filename=f"{app_id}.apk", reply_markup=result_keyboard()
            )
        user = update.effective_user
        context.user_data["mode"] = None
        context.user_data.pop("urltoapp_target_url", None)
        await log_activity(
            context, user, action="🌐 URL TO APP (real .apk)",
            detail=url, result=f"Logo: {'yes' if logo_bytes else 'no'}",
        )
        return

    # Fallback: real APK toolchain not available on this host (e.g. running
    # without the Docker/Android-SDK setup) or the build failed — ship the
    # dependency-free PWA package instead so the feature still works.
    try:
        zip_buffer, app_name = await asyncio.to_thread(build_pwa_package, url, logo_bytes)
    except Exception as e:
        await status_msg.edit_text(
            f"❌ <b>Build failed:</b> <code>{str(e)[:300]}</code>", parse_mode="HTML"
        )
        return

    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", app_name)[:40] or "app"
    zip_buffer.name = f"{safe_name}_PWA.zip"

    await status_msg.edit_text(
        "⚠️ <b>Real .apk builder available na ei host e</b> — PWA package "
        "pathacchi instead.\n\n"
        "<blockquote>README.txt e install steps deya ache.</blockquote>",
        parse_mode="HTML",
    )
    await update.effective_message.reply_document(
        document=zip_buffer, filename=zip_buffer.name, reply_markup=result_keyboard()
    )

    user = update.effective_user
    context.user_data["mode"] = None
    context.user_data.pop("urltoapp_target_url", None)
    await log_activity(
        context, user, action="🌐 URL TO APP (PWA fallback)",
        detail=url, result=f"Logo: {'yes' if logo_bytes else 'no (default icon)'}",
    )


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

    # URL-TO-APP: logo step accepts a photo instead of text
    if update.message.photo and context.user_data.get("mode") == "urltoapp_logo":
        photo_file = await update.message.photo[-1].get_file()
        logo_bytes = bytes(await photo_file.download_as_bytearray())
        await handle_urltoapp_build(update, context, logo_bytes=logo_bytes)
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

    if mode == "urltoapp_url":
        if not is_url:
            await update.message.reply_text(
                "⚠️ <b>Eta valid URL na.</b> http:// othoba https:// diye URL pathao.",
                parse_mode="HTML",
            )
            return
        context.user_data["urltoapp_target_url"] = text
        context.user_data["mode"] = "urltoapp_logo"
        await update.message.reply_text(
            '<tg-emoji emoji-id="6339296880500936916">⚡️</tg-emoji> '
            "<b>App logo pathao</b> (photo hisebe), othoba niche <b>Skip</b> "
            "e click koro logo chara build korte.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[SBtn("⏭ Skip", style="danger", callback_data="urltoapp_skip")]]
            ),
        )
        return

    if mode == "seo":
        await handle_seo(update, context)
    elif mode == "tech":
        await handle_tech(update, context)
    elif mode == "html":
        # "Convert URL" now does a full-site conversion (all internal pages
        # + assets, bundled offline) instead of a single raw HTML file.
        await handle_fullsite(update, context)
    elif mode == "zip":
        await handle_zip(update, context)
    else:
        # default / MAX mode: crawl the entire site (all internal pages)
        await handle_fullsite(update, context)


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

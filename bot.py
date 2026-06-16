"""
🌐 URL ➜ HTML — Pro Bot (Enhanced)
═══════════════════════════════════════════
Force Join System (Dynamic via Admin Panel)
/health endpoint for Uptime Robot
UI Photo button
Multiple channel support

Setup:
    pip install python-telegram-bot requests aiohttp

Run:
    python bot.py
"""

import json
import logging
import os
import re
import time
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from urllib.parse import urljoin, urlparse

import requests
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
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8928689890:AAHAQYcqITw6A6LzNUq1jvx4R4-eFRYc9ys")

# Telegram user IDs allowed to use the admin panel
ADMIN_IDS = [8600328303]

# Group where logs get posted
LOGS_GROUP_ID = -1003970225189

DATA_FILE = "bot_data.json"
HEALTH_PORT = int(os.environ.get("BOT_PORT", os.environ.get("PORT", 3000)))

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
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ═══════════════════════ HEALTH SERVER ═══════════════════════
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/health", "/"):
            body = b'{"status":"ok","bot":"URL to HTML Pro Bot"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress HTTP access logs


def run_health_server():
    server = HTTPServer(("0.0.0.0", HEALTH_PORT), HealthHandler)
    logger.info(f"✅ Health server running on port {HEALTH_PORT}")
    server.serve_forever()


# ═══════════════════════ PERSISTENT STORAGE ═══════════════════════
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "users": {},
        "stats": {"total_conversions": 0},
        "force_join_channels": [],
        "ui_photo_file_id": None,
    }


def save_data(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"⚠️ Failed to save data: {e}")


BOT_DATA = load_data()


def get_force_join_channels():
    return BOT_DATA.get("force_join_channels", [])


def register_user(user) -> bool:
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
    if not LOGS_GROUP_ID:
        return
    try:
        await context.bot.send_message(
            chat_id=LOGS_GROUP_ID, text=text, parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"⚠️ Failed to send log: {e}")


# ═══════════════════════ SBTN CLASS ═══════════════════════
class SBtn(InlineKeyboardButton):
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


# ═══════════════════════ FORCE JOIN CHECK ═══════════════════════
async def check_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    channels = get_force_join_channels()
    if not channels:
        return True

    user_id = update.effective_user.id
    not_joined = []

    for channel in channels:
        try:
            member = await context.bot.get_chat_member(
                chat_id=channel["chat_id"], user_id=user_id
            )
            if member.status in ("left", "kicked"):
                not_joined.append(channel)
        except TelegramError:
            not_joined.append(channel)

    if not_joined:
        buttons = [
            [SBtn(f"📢 {ch['title']}", style="primary", url=ch["url"])]
            for ch in not_joined
        ]
        buttons.append(
            [SBtn("✅ আমি জয়েন করেছি", style="success", callback_data="check_join")]
        )
        text = (
            "🔒 <b>Join Required</b>\n\n"
            "<blockquote>নিচের চ্যানেল/গ্রুপে জয়েন করুন, তারপর "
            "<b>আমি জয়েন করেছি</b> বাটনে ক্লিক করুন।</blockquote>"
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


# ═══════════════════════ KEYBOARDS ═══════════════════════
def main_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [SBtn("🌐 Convert URL", style="success", callback_data="convert")],
            [
                SBtn("🔎 SEO Report", style="primary", callback_data="seo"),
                SBtn("🧩 Tech Stack", style="primary", callback_data="tech"),
            ],
            [SBtn("📦 Offline ZIP Package", style="primary", callback_data="zip")],
            [SBtn("📸 UI Photo", style="primary", callback_data="ui_photo")],
            [
                SBtn("❓ Help", style="primary", callback_data="help"),
                SBtn("ℹ️ About", style="primary", callback_data="about"),
            ],
            [SBtn("📢 Updates Channel", style="primary", url="https://t.me/SHUVOMODS6")],
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
            [SBtn("📸 Set UI Photo", style="primary", callback_data="admin_set_photo")],
            [SBtn("❌ Close Panel", style="danger", callback_data="admin_close")],
        ]
    )


def admin_back_keyboard():
    return InlineKeyboardMarkup(
        [[SBtn("🔙 Back to Admin Panel", style="danger", callback_data="admin_panel")]]
    )


def force_join_manage_keyboard():
    channels = get_force_join_channels()
    buttons = []

    for i, ch in enumerate(channels):
        buttons.append(
            [SBtn(f"🗑 Remove: {ch['title']}", style="danger", callback_data=f"admin_fj_remove_{i}")]
        )

    buttons.append([SBtn("➕ Add Force Join Channel", style="success", callback_data="admin_fj_add")])
    buttons.append([SBtn("🔙 Back to Admin Panel", style="danger", callback_data="admin_panel")])
    return InlineKeyboardMarkup(buttons)


# ═══════════════════════ START / MENU ═══════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not await check_force_join(update, context):
        return

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

    text = (
        "✨ <b>Welcome to URL ➜ HTML Pro Bot</b> ✨\n\n"
        f"👋 Hello, <b>{user.first_name}</b>!\n\n"
        "<blockquote>"
        "✅ আমি যেকোনো website এর full HTML source code download করে "
        "<b>.html</b> file হিসেবে পাঠিয়ে দিতে পারি!"
        "</blockquote>\n\n"
        "👇 <b>নিচে একটি অপশন বেছে নিন:</b>"
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
        await query.edit_message_text(
            "🌐 <b>URL পাঠান</b>\n\n"
            "<blockquote>👷 Example:\n<code>https://example.com</code></blockquote>\n\n"
            "⚡️ <b>Output:</b> Full page <b>.html</b> source file!",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

    elif query.data == "seo":
        context.user_data["mode"] = "seo"
        await query.edit_message_text(
            "🔎 <b>SEO / Page Report</b>\n\n"
            "<blockquote>URL পাঠান — আমি চেক করব:\n"
            "• Title & meta description\n"
            "• Heading structure (H1-H6)\n"
            "• Load time\n"
            "• Image & link count</blockquote>",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

    elif query.data == "tech":
        context.user_data["mode"] = "tech"
        await query.edit_message_text(
            "🧩 <b>Tech Stack Detector</b>\n\n"
            "<blockquote>URL পাঠান — আমি detect করব platform/framework "
            "(WordPress, Shopify, React, Next.js, etc.)</blockquote>",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

    elif query.data == "zip":
        context.user_data["mode"] = "zip"
        await query.edit_message_text(
            "📦 <b>Offline ZIP Package</b>\n\n"
            "<blockquote>URL পাঠান — আমি HTML, CSS, JS, এবং images "
            "একটি ZIP এ bundle করব।</blockquote>\n\n"
            "<i>Note: same-page linked assets fetch করা হয়।</i>",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

    elif query.data == "ui_photo":
        photo_file_id = BOT_DATA.get("ui_photo_file_id")
        if photo_file_id:
            try:
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=photo_file_id,
                    caption="📸 <b>Bot UI Preview</b>",
                    parse_mode="HTML",
                    reply_markup=back_keyboard(),
                )
            except Exception:
                await query.edit_message_text(
                    "⚠️ <b>Photo পাঠাতে সমস্যা হয়েছে।</b>\n\nAdmin কে জানান।",
                    parse_mode="HTML",
                    reply_markup=back_keyboard(),
                )
        else:
            await query.edit_message_text(
                "📸 <b>UI Photo</b>\n\n"
                "<blockquote>এখনো কোনো UI photo set করা হয়নি।\n"
                "Admin /admin দিয়ে photo set করতে পারবেন।</blockquote>",
                parse_mode="HTML",
                reply_markup=back_keyboard(),
            )

    elif query.data == "help":
        await query.edit_message_text(
            "📖 <b>How To Use</b>\n\n"
            "<blockquote>"
            "1️⃣ <b>Convert URL</b> বাটনে ট্যাপ করুন\n"
            "2️⃣ Target website এর link পাঠান (http/https সহ)\n"
            "3️⃣ Bot automatically full page HTML download করে file পাঠাবে ✅"
            "</blockquote>\n\n"
            "<b>Note:</b> JS-heavy বা login-protected sites এ source limited হতে পারে।",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

    elif query.data == "about":
        channels = get_force_join_channels()
        await query.edit_message_text(
            "ℹ️ <b>About This Bot</b>\n\n"
            "<blockquote>"
            "✅ <b>Built with:</b> python-telegram-bot + requests\n"
            "✅ <b>Function:</b> URL ➜ Full HTML source ➜ .html file\n"
            f"✅ <b>Force Join Channels:</b> {len(channels)}\n"
            "🟢 <b>Status:</b> Online & Active"
            "</blockquote>\n\n"
            "⚡️ <i>Powered by your custom Telegram Bot</i>",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
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
        html, re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
            html, re.IGNORECASE,
        )
    return m.group(1).strip()[:200] if m else "Not found"


def count_headings(html: str) -> dict:
    counts = {}
    for level in range(1, 7):
        counts[f"h{level}"] = len(re.findall(rf"<h{level}[\s>]", html, re.IGNORECASE))
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
    return list(dict.fromkeys(detected))


def extract_asset_urls(html: str, base_url: str) -> dict:
    css_urls = re.findall(
        r'<link[^>]+rel=["\']stylesheet["\'][^>]+href=["\'](.*?)["\']', html, re.IGNORECASE
    )
    js_urls = re.findall(r'<script[^>]+src=["\'](.*?)["\']', html, re.IGNORECASE)
    img_urls = re.findall(r'<img[^>]+src=["\'](.*?)["\']', html, re.IGNORECASE)

    def resolve(urls):
        resolved = []
        for u in urls:
            if u.startswith("data:"):
                continue
            resolved.append(urljoin(base_url, u))
        return resolved

    return {"css": resolve(css_urls)[:15], "js": resolve(js_urls)[:15], "img": resolve(img_urls)[:15]}


def build_offline_zip(html: str, base_url: str) -> tuple:
    assets = extract_asset_urls(html, base_url)
    zip_buffer = BytesIO()
    asset_count = 0
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", html)
        for category, urls in assets.items():
            folder = {"css": "css", "js": "js", "img": "images"}[category]
            for u in urls:
                try:
                    r = requests.get(u, headers=HEADERS, timeout=8)
                    if r.status_code == 200:
                        filename = urlparse(u).path.split("/")[-1] or "file"
                        zf.writestr(f"{folder}/{filename}", r.content)
                        asset_count += 1
                except Exception:
                    continue
    zip_buffer.seek(0)
    return zip_buffer, asset_count


# ═══════════════════════ URL HANDLER ═══════════════════════
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not is_valid_url(url):
        await update.message.reply_text(
            "⚠️ <b>Invalid URL!</b>\n\n"
            "<blockquote>http:// বা https:// দিয়ে শুরু valid link পাঠান</blockquote>",
            parse_mode="HTML", reply_markup=back_keyboard(),
        )
        return

    status_msg = await update.message.reply_text(
        "⏳ <b>Fetching page...</b>\n<blockquote>🔄 একটু অপেক্ষা করুন...</blockquote>",
        parse_mode="HTML",
    )

    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        html_content = response.text
        status_code = response.status_code
    except requests.exceptions.Timeout:
        await status_msg.edit_text(
            "❌ <b>Error: Request Timed Out</b>\n\n<blockquote>অন্য URL দিয়ে আবার চেষ্টা করুন।</blockquote>",
            parse_mode="HTML", reply_markup=result_keyboard(),
        )
        return
    except requests.exceptions.RequestException as e:
        await status_msg.edit_text(
            f"❌ <b>Error Fetching URL</b>\n\n<blockquote><code>{str(e)[:300]}</code></blockquote>",
            parse_mode="HTML", reply_markup=result_keyboard(),
        )
        return

    title = extract_title(html_content)
    size_kb = round(len(html_content.encode("utf-8")) / 1024, 2)

    block_reason = detect_blocked_page(html_content, status_code)
    if block_reason:
        await status_msg.edit_text(
            "⚠️ <b>Could Not Fetch Real Page Content</b>\n\n"
            f"<blockquote>{block_reason}</blockquote>",
            parse_mode="HTML", reply_markup=result_keyboard(),
        )
        return

    html_buffer = BytesIO(html_content.encode("utf-8"))
    html_buffer.name = f"{title}.html"

    caption = (
        "✅ <b>Conversion Successful!</b>\n\n"
        "<blockquote>"
        f"🔗 <b>URL:</b> <code>{url}</code>\n"
        f"📄 <b>File:</b> {html_buffer.name}\n"
        f"📦 <b>Size:</b> {size_kb} KB\n"
        f"🌎 <b>Status Code:</b> {status_code}"
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

    BOT_DATA["stats"]["total_conversions"] = BOT_DATA["stats"].get("total_conversions", 0) + 1
    save_data(BOT_DATA)
    user = update.effective_user
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
            "⚠️ <b>Invalid URL!</b> http:// বা https:// দিয়ে শুরু link পাঠান",
            parse_mode="HTML", reply_markup=back_keyboard(),
        )
        return

    status_msg = await update.message.reply_text("⏳ <b>Analyzing page...</b>", parse_mode="HTML")

    try:
        start_time = time.time()
        response = requests.get(url, headers=HEADERS, timeout=20)
        load_time = round(time.time() - start_time, 2)
        response.raise_for_status()
        html = response.text
    except Exception as e:
        await status_msg.edit_text(
            f"❌ <b>Error:</b> <code>{str(e)[:300]}</code>",
            parse_mode="HTML", reply_markup=result_keyboard(),
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

    await status_msg.edit_text(
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
        "</blockquote>",
        parse_mode="HTML", reply_markup=result_keyboard(),
    )


# ═══════════════════════ TECH STACK HANDLER ═══════════════════════
async def handle_tech(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not is_valid_url(url):
        await update.message.reply_text(
            "⚠️ <b>Invalid URL!</b> http:// বা https:// দিয়ে শুরু link পাঠান",
            parse_mode="HTML", reply_markup=back_keyboard(),
        )
        return

    status_msg = await update.message.reply_text("⏳ <b>Detecting tech stack...</b>", parse_mode="HTML")

    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        html = response.text
    except Exception as e:
        await status_msg.edit_text(
            f"❌ <b>Error:</b> <code>{str(e)[:300]}</code>",
            parse_mode="HTML", reply_markup=result_keyboard(),
        )
        return

    detected = detect_tech_stack(html, response.headers)
    detected_lines = "\n".join(f"• {item}" for item in detected)

    await status_msg.edit_text(
        "🧩 <b>Tech Stack Detection</b>\n\n"
        f"🔗 <b>URL:</b> <code>{url}</code>\n\n"
        f"<blockquote>{detected_lines}</blockquote>\n\n"
        "<i>Detection is based on HTML/header signatures.</i>",
        parse_mode="HTML", reply_markup=result_keyboard(),
    )


# ═══════════════════════ OFFLINE ZIP HANDLER ═══════════════════════
async def handle_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not is_valid_url(url):
        await update.message.reply_text(
            "⚠️ <b>Invalid URL!</b> http:// বা https:// দিয়ে শুরু link পাঠান",
            parse_mode="HTML", reply_markup=back_keyboard(),
        )
        return

    status_msg = await update.message.reply_text(
        "⏳ <b>Building offline package...</b>\n"
        "<blockquote>HTML এবং assets fetch করা হচ্ছে, একটু সময় লাগতে পারে...</blockquote>",
        parse_mode="HTML",
    )

    try:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        html = response.text
    except Exception as e:
        await status_msg.edit_text(
            f"❌ <b>Error fetching page:</b> <code>{str(e)[:300]}</code>",
            parse_mode="HTML", reply_markup=result_keyboard(),
        )
        return

    try:
        zip_buffer, asset_count = build_offline_zip(html, url)
    except Exception as e:
        await status_msg.edit_text(
            f"❌ <b>Error building ZIP:</b> <code>{str(e)[:300]}</code>",
            parse_mode="HTML", reply_markup=result_keyboard(),
        )
        return

    title = extract_title(html)
    zip_buffer.name = f"{title}_offline_package.zip"
    size_kb = round(zip_buffer.getbuffer().nbytes / 1024, 2)

    await update.message.reply_document(
        document=zip_buffer,
        filename=zip_buffer.name,
        caption=(
            "💥 <b>Offline Package Ready!</b>\n\n"
            "<blockquote>"
            f"🔗 <b>URL:</b> <code>{url}</code>\n"
            f"📦 <b>ZIP Size:</b> {size_kb} KB\n"
            f"🧩 <b>Assets bundled:</b> {asset_count}\n"
            "😎 <b>Entry point:</b> index.html"
            "</blockquote>\n\n"
            "<i>Extract করে index.html যেকোনো browser এ open করুন।</i>"
        ),
        parse_mode="HTML",
        reply_markup=result_keyboard(),
    )
    await status_msg.delete()


# ═══════════════════════ ADMIN COMMAND ═══════════════════════
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text(
            "🚫 <b>Access Denied</b>\n\n"
            "<blockquote>আপনি admin panel ব্যবহার করতে অনুমোদিত নন।</blockquote>",
            parse_mode="HTML",
        )
        return

    await update.message.reply_text(
        "🛠 <b>Admin Panel</b>\n\n"
        "<blockquote>Bot users manage করুন, broadcast পাঠান, "
        "এবং force-join channels configure করুন।</blockquote>",
        parse_mode="HTML",
        reply_markup=admin_panel_keyboard(),
    )


# ═══════════════════════ ADMIN CALLBACK HANDLER ═══════════════════════
async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user

    if not is_admin(user.id):
        await query.answer("🚫 Not authorized.", show_alert=True)
        return

    await query.answer()

    # ── Admin Panel Home ──
    if query.data == "admin_panel":
        context.user_data["admin_mode"] = None
        await query.edit_message_text(
            "🛠 <b>Admin Panel</b>\n\n"
            "<blockquote>Bot users manage করুন, broadcast পাঠান, "
            "এবং force-join channels configure করুন।</blockquote>",
            parse_mode="HTML",
            reply_markup=admin_panel_keyboard(),
        )

    # ── Stats ──
    elif query.data == "admin_stats":
        total_users = len(BOT_DATA["users"])
        total_conversions = BOT_DATA["stats"].get("total_conversions", 0)
        channels = get_force_join_channels()
        await query.edit_message_text(
            "📊 <b>Bot Statistics</b>\n\n"
            "<blockquote>"
            f"👥 <b>Total Users:</b> {total_users}\n"
            f"🔁 <b>Total Conversions:</b> {total_conversions}\n"
            f"🔗 <b>Force-Join Channels:</b> {len(channels)}\n"
            f"📸 <b>UI Photo Set:</b> {'✅ Yes' if BOT_DATA.get('ui_photo_file_id') else '❌ No'}"
            "</blockquote>",
            parse_mode="HTML",
            reply_markup=admin_back_keyboard(),
        )

    # ── Users ──
    elif query.data == "admin_users":
        users = list(BOT_DATA["users"].values())[-20:]
        lines = "\n".join(
            f"• {u['first_name']} (@{u.get('username') or 'N/A'}) — <code>{u['id']}</code>"
            for u in users
        ) if users else "No users yet."
        await query.edit_message_text(
            f"👥 <b>Recent Users (last 20)</b>\n\n<blockquote>{lines}</blockquote>",
            parse_mode="HTML",
            reply_markup=admin_back_keyboard(),
        )

    # ── Broadcast ──
    elif query.data == "admin_broadcast":
        context.user_data["admin_mode"] = "broadcast"
        await query.edit_message_text(
            "📢 <b>Broadcast Message</b>\n\n"
            "<blockquote>এখন message text পাঠান। "
            "এটি সব registered users এ পাঠানো হবে।</blockquote>",
            parse_mode="HTML",
            reply_markup=admin_back_keyboard(),
        )

    # ── Force Join Management ──
    elif query.data == "admin_forcejoin":
        channels = get_force_join_channels()
        if channels:
            lines = "\n".join(
                f"<b>{i+1}.</b> {ch['title']} — <code>{ch['chat_id']}</code>"
                for i, ch in enumerate(channels)
            )
        else:
            lines = "কোনো force-join channel নেই।"
        await query.edit_message_text(
            "🔗 <b>Force Join Channels</b>\n\n"
            f"<blockquote>{lines}</blockquote>\n\n"
            "<i>নিচে Remove বা Add বাটন দিয়ে manage করুন।</i>",
            parse_mode="HTML",
            reply_markup=force_join_manage_keyboard(),
        )

    # ── Add Force Join — Step 1: Ask for button title ──
    elif query.data == "admin_fj_add":
        context.user_data["admin_mode"] = "fj_title"
        await query.edit_message_text(
            "➕ <b>Add Force Join Channel</b>\n\n"
            "<blockquote><b>Step 1/2:</b> Button এর title লিখুন।\n\n"
            "Example: <code>JOIN SHUVO</code></blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[SBtn("❌ Cancel", style="danger", callback_data="admin_forcejoin")]]
            ),
        )

    # ── Remove Force Join ──
    elif query.data.startswith("admin_fj_remove_"):
        try:
            idx = int(query.data.split("_")[-1])
            channels = get_force_join_channels()
            if 0 <= idx < len(channels):
                removed = channels.pop(idx)
                BOT_DATA["force_join_channels"] = channels
                save_data(BOT_DATA)
                await query.answer(f"✅ Removed: {removed['title']}", show_alert=True)
            # Refresh the force join panel
            await query.edit_message_text(
                "🔗 <b>Force Join Channels</b>\n\n"
                f"<blockquote>{'<br>'.join(f'{i+1}. {ch[chr(116)]+chr(105)+chr(116)+chr(108)+chr(101)}' for i, ch in enumerate(get_force_join_channels())) or 'কোনো channel নেই।'}</blockquote>",
                parse_mode="HTML",
                reply_markup=force_join_manage_keyboard(),
            )
        except Exception as e:
            await query.answer(f"Error: {e}", show_alert=True)

        # Re-render properly
        channels = get_force_join_channels()
        lines = "\n".join(
            f"<b>{i+1}.</b> {ch['title']} — <code>{ch['chat_id']}</code>"
            for i, ch in enumerate(channels)
        ) if channels else "কোনো force-join channel নেই।"
        try:
            await query.edit_message_text(
                "🔗 <b>Force Join Channels</b>\n\n"
                f"<blockquote>{lines}</blockquote>\n\n"
                "<i>নিচে Remove বা Add বাটন দিয়ে manage করুন।</i>",
                parse_mode="HTML",
                reply_markup=force_join_manage_keyboard(),
            )
        except Exception:
            pass

    # ── Set UI Photo ──
    elif query.data == "admin_set_photo":
        context.user_data["admin_mode"] = "set_photo"
        await query.edit_message_text(
            "📸 <b>Set UI Photo</b>\n\n"
            "<blockquote>এখন একটি photo পাঠান। "
            "এটি UI Photo button এ দেখা যাবে।</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[SBtn("❌ Cancel", style="danger", callback_data="admin_panel")]]
            ),
        )

    # ── Close ──
    elif query.data == "admin_close":
        context.user_data["admin_mode"] = None
        await query.edit_message_text("🛠 Admin panel closed.")


# ═══════════════════════ ADMIN TEXT/PHOTO HANDLER ═══════════════════════
async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not is_admin(user.id):
        return False

    admin_mode = context.user_data.get("admin_mode")

    # ── Broadcast ──
    if admin_mode == "broadcast" and update.message and update.message.text:
        message_text = update.message.text
        context.user_data["admin_mode"] = None
        status_msg = await update.message.reply_text("📤 <b>Broadcasting...</b>", parse_mode="HTML")
        sent, failed = 0, 0
        for uid in list(BOT_DATA["users"].keys()):
            try:
                await context.bot.send_message(chat_id=int(uid), text=message_text)
                sent += 1
            except Exception:
                failed += 1

        await status_msg.edit_text(
            "✅ <b>Broadcast Complete</b>\n\n"
            f"<blockquote>📨 Sent: {sent}\n❌ Failed: {failed}</blockquote>",
            parse_mode="HTML",
            reply_markup=admin_back_keyboard(),
        )
        await send_log(
            context,
            f"📢 <b>Broadcast Sent</b>\n👤 By: {user.first_name} (<code>{user.id}</code>)\n"
            f"📨 Sent: {sent} | ❌ Failed: {failed}",
        )
        return True

    # ── Force Join: Step 1 — Button Title ──
    if admin_mode == "fj_title" and update.message and update.message.text:
        title = update.message.text.strip()
        context.user_data["fj_pending_title"] = title
        context.user_data["admin_mode"] = "fj_link"
        await update.message.reply_text(
            "➕ <b>Add Force Join Channel</b>\n\n"
            f"<blockquote><b>Button Title:</b> <code>{title}</code>\n\n"
            "<b>Step 2/2:</b> এখন channel/group এর invite link পাঠান।\n\n"
            "Example: <code>https://t.me/yourchannel</code>\n\n"
            "Chat ID ও দিতে পারেন (যেমন: <code>@username</code> বা <code>-100xxxxxxxx</code>)</blockquote>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[SBtn("❌ Cancel", style="danger", callback_data="admin_forcejoin")]]
            ),
        )
        return True

    # ── Force Join: Step 2 — Link ──
    if admin_mode == "fj_link" and update.message and update.message.text:
        link = update.message.text.strip()
        title = context.user_data.get("fj_pending_title", "Channel")
        context.user_data["admin_mode"] = None
        context.user_data["fj_pending_title"] = None

        # Determine chat_id from the link
        if link.startswith("https://t.me/") or link.startswith("http://t.me/"):
            username = link.rstrip("/").split("/")[-1]
            if username.startswith("+"):
                chat_id = link  # private group invite link
            else:
                chat_id = f"@{username}"
        elif link.startswith("@"):
            chat_id = link
        else:
            chat_id = link  # assume raw chat_id like -100xxxxxxxx

        new_channel = {
            "chat_id": chat_id,
            "title": title,
            "url": link if link.startswith("http") else f"https://t.me/{chat_id.lstrip('@')}",
        }

        if "force_join_channels" not in BOT_DATA:
            BOT_DATA["force_join_channels"] = []
        BOT_DATA["force_join_channels"].append(new_channel)
        save_data(BOT_DATA)

        await update.message.reply_text(
            "✅ <b>Force Join Channel Added!</b>\n\n"
            "<blockquote>"
            f"📢 <b>Title:</b> {title}\n"
            f"🔗 <b>Chat ID:</b> <code>{chat_id}</code>\n"
            f"🌐 <b>URL:</b> {new_channel['url']}"
            "</blockquote>\n\n"
            f"মোট channels: <b>{len(BOT_DATA['force_join_channels'])}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [SBtn("🔗 Manage Force Join", style="primary", callback_data="admin_forcejoin")],
                [SBtn("🔙 Admin Panel", style="danger", callback_data="admin_panel")],
            ]),
        )
        return True

    # ── Set UI Photo ──
    if admin_mode == "set_photo" and update.message and update.message.photo:
        photo = update.message.photo[-1]  # highest resolution
        BOT_DATA["ui_photo_file_id"] = photo.file_id
        save_data(BOT_DATA)
        context.user_data["admin_mode"] = None
        await update.message.reply_text(
            "✅ <b>UI Photo Set!</b>\n\n"
            "<blockquote>📸 Photo সফলভাবে save হয়েছে।\n"
            "User রা এখন UI Photo বাটনে ক্লিক করলে এই photo দেখতে পাবেন।</blockquote>",
            parse_mode="HTML",
            reply_markup=admin_back_keyboard(),
        )
        return True

    return False


# ═══════════════════════ FALLBACK / DISPATCHER ═══════════════════════
async def fallback_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Admin input takes priority
    if await handle_admin_input(update, context):
        return

    if not update.message:
        return

    text = update.message.text.strip() if update.message.text else ""
    is_url = is_valid_url(text)
    mode = context.user_data.get("mode")

    if not is_url and not mode:
        return

    if not await check_force_join(update, context):
        return

    if mode == "seo":
        await handle_seo(update, context)
    elif mode == "tech":
        await handle_tech(update, context)
    elif mode == "zip":
        await handle_zip(update, context)
    else:
        await handle_url(update, context)


async def fallback_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photos sent by admin (for setting UI photo)."""
    if await handle_admin_input(update, context):
        return


# ═══════════════════════ HEALTH COMMAND ═══════════════════════
async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🟢 <b>Bot Status: Online</b>\n\n"
        "<blockquote>"
        f"⏰ <b>Time:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"👥 <b>Users:</b> {len(BOT_DATA['users'])}\n"
        f"🔁 <b>Conversions:</b> {BOT_DATA['stats'].get('total_conversions', 0)}\n"
        f"🔗 <b>Force Join Channels:</b> {len(get_force_join_channels())}\n"
        f"🌐 <b>Health Port:</b> {HEALTH_PORT}"
        "</blockquote>",
        parse_mode="HTML",
    )


# ═══════════════════════ MAIN ═══════════════════════
import asyncio

async def async_main():
    print("🔧 Initializing bot...")

    # Start health server in background thread
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    print(f"🌐 Health endpoint: http://0.0.0.0:{HEALTH_PORT}/health")

    # Quick connectivity check
    try:
        test_resp = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=15
        )
        print(f"🔍 getMe check -> status {test_resp.status_code}: {test_resp.text[:200]}")
        if not test_resp.ok or not test_resp.json().get("ok"):
            print("❌ Bot token invalid or Telegram unreachable. Stopping.")
            return
    except Exception as e:
        print(f"❌ Could not reach Telegram API: {e}")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern=r"^admin_"))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, fallback_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_text))

    print("🤖 Bot is running... (press Ctrl+C to stop)")
    try:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        # Keep running until interrupted
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as e:
        print(f"❌ Bot crashed during polling: {e}")
        raise
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()

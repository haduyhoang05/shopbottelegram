import os
import re
import json
import time
import queue
import sqlite3
import threading
import urllib.parse
import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any

import requests
import customtkinter as ctk
from tkinter import filedialog, messagebox

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================================================
# CONFIG / CONSTANTS
# =========================================================
APP_TITLE = "TELE SHOP BOT - VietQR (A) + AutoBank V3 (PC 24/7)"
DB_FILE = "tele_shop.sqlite3"
CONFIG_FILE = "tele_shop_config.json"

DEFAULT_VIETQR_TEMPLATE = "compact2"

VIETQR_BANKS = [
    ("Vietcombank", "vietcombank"),
    ("Techcombank", "techcombank"),
    ("MB Bank", "mbbank"),
    ("ACB", "acb"),
    ("VietinBank", "vietinbank"),
    ("BIDV", "bidv"),
    ("Sacombank", "sacombank"),
    ("TPBank", "tpbank"),
]

CODERENT_V2_CODES = {
    "Vietcombank": "VCB",
    "MB Bank": "MB",
    "BIDV": "BIDV",
    "Techcombank": "TCB",
    "ACB": "ACB",
    "TPBank": "TPB",
    "VPBank": "VPB",
    "OCB": "OCB",
    "MSB": "MSB",
    "ViettelMoney": "VTM",
    "SeABank": "SEA",
    "TheSieuRe": "TSR",
}

NAPID_RE = re.compile(r"\bNapid\s*(\d+)\b", re.IGNORECASE)

# =========================================================
# HELPERS
# =========================================================
def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_int(x, default=0) -> int:
    try:
        return int(str(x).replace(",", "").strip())
    except Exception:
        return default


def safe_pos_int(x, default=0) -> int:
    try:
        val = int(str(x).replace(",", "").strip())
        return val if val > 0 else default
    except Exception:
        return default


def vietqr_image_url(
    bank_id: str,
    account_no: str,
    template: str,
    amount: int,
    add_info: str,
    account_name: str,
) -> str:
    base = f"https://img.vietqr.io/image/{bank_id}-{account_no}-{template}.png"
    qs = {
        "amount": str(max(0, int(amount))),
        "addInfo": add_info,
        "accountName": account_name or "",
    }
    return base + "?" + urllib.parse.urlencode(qs, safe="")


def build_coderent_v2_url(bank_name: str, token: str) -> str:
    code = CODERENT_V2_CODES.get(bank_name)
    if not code:
        raise ValueError(f"Bank V2 không hỗ trợ: {bank_name}")
    return f"https://api.coderent.one/api/{code}/historyv2/{token}"


# =========================================================
# DB
# =========================================================
def db_connect():
    con = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=30.0)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db_connect()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        balance INTEGER DEFAULT 0,
        ref_by INTEGER,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS categories(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        is_active INTEGER DEFAULT 1
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id INTEGER,
        name TEXT NOT NULL,
        price INTEGER NOT NULL,
        stock INTEGER DEFAULT 0,
        deliver_text TEXT DEFAULT '',
        is_active INTEGER DEFAULT 1
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        product_id INTEGER,
        price INTEGER,
        discount INTEGER DEFAULT 0,
        final_price INTEGER,
        coupon_code TEXT,
        status TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS deposits(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        description TEXT,
        trans_id TEXT,
        status TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS coupons(
        code TEXT PRIMARY KEY,
        discount_amount INTEGER DEFAULT 0,
        discount_percent INTEGER DEFAULT 0,
        apply_product_id INTEGER DEFAULT NULL,
        min_order INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        max_uses INTEGER DEFAULT 0,
        used_count INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS affiliate(
        user_id INTEGER PRIMARY KEY,
        percent INTEGER DEFAULT 5
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS affiliate_earnings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_user_id INTEGER,
        order_id INTEGER,
        amount INTEGER,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS bank_seen(
        transaction_id TEXT PRIMARY KEY,
        seen_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS product_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER,
        data_text TEXT NOT NULL,
        is_sold INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS bot_settings(
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_product_items_prod_sold ON product_items(product_id, is_sold)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_deposits_user ON deposits(user_id)")

    con.commit()
    con.close()


# ---------- user helpers ----------
def upsert_user(tg_user):
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (tg_user.id,))
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO users(user_id, username, first_name, balance, ref_by, created_at) VALUES(?,?,?,?,?,?)",
            (tg_user.id, tg_user.username or "", tg_user.first_name or "", 0, None, now_str()),
        )
    else:
        cur.execute(
            "UPDATE users SET username=?, first_name=? WHERE user_id=?",
            (tg_user.username or "", tg_user.first_name or "", tg_user.id),
        )
    con.commit()
    con.close()


def set_ref_if_empty(user_id: int, ref_by: int):
    if user_id == ref_by:
        return
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT ref_by FROM users WHERE user_id=?", (user_id,))
    r = cur.fetchone()
    if r and r["ref_by"] is None:
        cur.execute("UPDATE users SET ref_by=? WHERE user_id=?", (ref_by, user_id))
    con.commit()
    con.close()


def get_user(user_id: int):
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    r = cur.fetchone()
    con.close()
    return r


def get_balance(user_id: int) -> int:
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    r = cur.fetchone()
    con.close()
    return int(r["balance"] or 0) if r else 0


def add_balance(user_id: int, amount: int):
    con = db_connect()
    cur = con.cursor()

    cur.execute("""
        INSERT OR IGNORE INTO users(user_id, username, first_name, balance, ref_by, created_at)
        VALUES(?, '', '', 0, NULL, ?)
    """, (int(user_id), now_str()))

    cur.execute("""
        UPDATE users
        SET balance = COALESCE(balance,0) + ?
        WHERE user_id=?
    """, (int(amount), int(user_id)))

    con.commit()
    con.close()


def subtract_balance(user_id: int, amount: int) -> bool:
    if amount <= 0:
        return True
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    r = cur.fetchone()
    if not r:
        con.close()
        return False
    bal = int(r["balance"] or 0)
    if bal < amount:
        con.close()
        return False
    cur.execute("UPDATE users SET balance = balance - ? WHERE user_id=? AND balance >= ?", (int(amount), user_id, int(amount)))
    affected = cur.rowcount
    con.commit()
    con.close()
    return affected > 0


# ---------- categories/products ----------
def add_category(name: str) -> int:
    con = db_connect()
    cur = con.cursor()
    cur.execute("INSERT INTO categories(name, is_active) VALUES(?,1)", (name,))
    con.commit()
    cid = cur.lastrowid
    con.close()
    return cid


def edit_category(cid: int, name: str):
    con = db_connect()
    cur = con.cursor()
    cur.execute("UPDATE categories SET name=? WHERE id=?", (name, cid))
    con.commit()
    con.close()


def delete_category(cid: int):
    con = db_connect()
    cur = con.cursor()
    cur.execute("UPDATE categories SET is_active=0 WHERE id=?", (cid,))
    con.commit()
    con.close()


def list_categories():
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT id, name FROM categories WHERE is_active=1 ORDER BY id DESC")
    rows = cur.fetchall()
    con.close()
    return rows


def edit_product(pid: int, name: str, price: int, new_items: Optional[List[str]] = None) -> int:
    con = db_connect()
    cur = con.cursor()
    if new_items:
        for item in new_items:
            cur.execute(
                "INSERT INTO product_items(product_id, data_text, is_sold) VALUES(?,?,0)",
                (pid, item),
            )
    cur.execute("SELECT COUNT(*) AS unsold FROM product_items WHERE product_id=? AND is_sold=0", (pid,))
    actual_stock = int(cur.fetchone()["unsold"])
    cur.execute(
        "UPDATE products SET name=?, price=?, stock=? WHERE id=?",
        (name, int(price), actual_stock, pid),
    )
    con.commit()
    con.close()
    return actual_stock


def delete_product(pid: int):
    con = db_connect()
    cur = con.cursor()
    cur.execute("UPDATE products SET is_active=0 WHERE id=?", (pid,))
    con.commit()
    con.close()


def list_products_by_category(cid: int):
    con = db_connect()
    cur = con.cursor()
    cur.execute("""
        SELECT id, name, price, stock FROM products
        WHERE is_active=1 AND category_id=?
        ORDER BY id DESC
    """, (cid,))
    rows = cur.fetchall()
    con.close()
    return rows


def get_product(pid: int):
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT * FROM products WHERE id=? AND is_active=1", (pid,))
    r = cur.fetchone()
    con.close()
    return r


# ---------- coupons ----------
def upsert_coupon(
    code: str,
    discount_amount: int,
    discount_percent: int,
    apply_product_id: Optional[int],
    min_order: int,
    max_uses: int,
):
    con = db_connect()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO coupons(code, discount_amount, discount_percent, apply_product_id, min_order, is_active, max_uses, used_count)
        VALUES(?,?,?,?,?,1,?,0)
        ON CONFLICT(code) DO UPDATE SET
            discount_amount=excluded.discount_amount,
            discount_percent=excluded.discount_percent,
            apply_product_id=excluded.apply_product_id,
            min_order=excluded.min_order,
            max_uses=excluded.max_uses,
            is_active=1
    """, (code.upper(), int(discount_amount), int(discount_percent), apply_product_id, int(min_order), int(max_uses)))
    con.commit()
    con.close()


def disable_coupon(code: str):
    con = db_connect()
    cur = con.cursor()
    cur.execute("UPDATE coupons SET is_active=0 WHERE code=?", (code.upper(),))
    con.commit()
    con.close()


def get_coupon(code: str):
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT * FROM coupons WHERE code=? AND is_active=1", (code.upper(),))
    r = cur.fetchone()
    con.close()
    return r


def mark_coupon_used(code: str):
    con = db_connect()
    cur = con.cursor()
    cur.execute("UPDATE coupons SET used_count = used_count + 1 WHERE code=?", (code.upper(),))
    con.commit()
    con.close()


# ---------- affiliate ----------
def get_aff_percent(user_id: int) -> int:
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT percent FROM affiliate WHERE user_id=?", (user_id,))
    r = cur.fetchone()
    if not r:
        cur.execute("INSERT OR IGNORE INTO affiliate(user_id, percent) VALUES(?,5)", (user_id,))
        con.commit()
        con.close()
        return 5
    con.close()
    return int(r["percent"] or 5)


def set_aff_percent(user_id: int, percent: int):
    con = db_connect()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO affiliate(user_id, percent) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET percent=excluded.percent",
        (user_id, int(percent)),
    )
    con.commit()
    con.close()


def add_aff_earning(referrer_id: int, referred_user_id: int, order_id: int, amount: int):
    con = db_connect()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO affiliate_earnings(referrer_id, referred_user_id, order_id, amount, created_at) VALUES(?,?,?,?,?)",
        (referrer_id, referred_user_id, order_id, int(amount), now_str()),
    )
    con.commit()
    con.close()


# ---------- orders/deposits ----------
def create_order(
    user_id: int,
    product_id: int,
    price: int,
    discount: int,
    final_price: int,
    coupon_code: Optional[str],
    status: str,
) -> int:
    con = db_connect()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO orders(user_id, product_id, price, discount, final_price, coupon_code, status, created_at)
        VALUES(?,?,?,?,?,?,?,?)
    """, (user_id, product_id, int(price), int(discount), int(final_price), coupon_code, status, now_str()))
    con.commit()
    oid = cur.lastrowid
    con.close()
    return oid


def list_orders(user_id: int, limit: int = 20):
    con = db_connect()
    cur = con.cursor()
    cur.execute("""
        SELECT o.id, o.final_price, o.status, o.created_at, p.name AS product_name
        FROM orders o
        LEFT JOIN products p ON p.id=o.product_id
        WHERE o.user_id=?
        ORDER BY o.id DESC
        LIMIT ?
    """, (user_id, int(limit)))
    rows = cur.fetchall()
    con.close()
    return rows


def create_deposit(user_id: int, amount: int, description: str, trans_id: str, status: str):
    con = db_connect()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO deposits(user_id, amount, description, trans_id, status, created_at)
        VALUES(?,?,?,?,?,?)
    """, (user_id, int(amount), description or "", trans_id or "", status, now_str()))
    con.commit()
    con.close()


# ---------- bank seen ----------
def bank_tx_seen(transaction_id: str) -> bool:
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT transaction_id FROM bank_seen WHERE transaction_id=?", (transaction_id,))
    r = cur.fetchone()
    con.close()
    return r is not None


def mark_bank_tx_seen(transaction_id: str):
    con = db_connect()
    cur = con.cursor()
    cur.execute("INSERT OR IGNORE INTO bank_seen(transaction_id, seen_at) VALUES(?,?)", (transaction_id, now_str()))
    con.commit()
    con.close()


# =========================================================
# CONFIG DATACLASS
# =========================================================
@dataclass
class BotConfig:
    admin_id: int = 0
    bot_token: str = ""

    # nhóm log Telegram
    log_group_id: int = 0

    # /start video
    start_video_path: str = ""

    # VietQR quick link
    vietqr_bank_name: str = "Vietcombank"
    vietqr_bank_id: str = "vietcombank"
    vietqr_stk: str = ""
    vietqr_ctk: str = ""
    vietqr_template: str = DEFAULT_VIETQR_TEMPLATE

    # AutoBank API Coderent V2
    bank_v3_name: str = "Vietcombank"
    bank_v3_token: str = ""
    poll_interval: int = 15

    auto_bank_api_text: str = ""


def load_config() -> BotConfig:
    if not os.path.exists(CONFIG_FILE):
        return BotConfig()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            j = json.load(f)
        return BotConfig(**j)
    except Exception:
        return BotConfig()


def save_config(cfg: BotConfig):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg.__dict__, f, ensure_ascii=False, indent=2)


# =========================================================
# BOT RUNNER
# =========================================================
class BotRunner:
    def __init__(self, log_queue: queue.Queue):
        self.cfg: BotConfig = load_config()
        self.log_queue = log_queue

        self._app: Optional[Application] = None
        self._bot_thread: Optional[threading.Thread] = None
        self._bank_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._bank_wake_event = threading.Event()
        self._bot_loop: Optional[asyncio.AbstractEventLoop] = None
        self._bot_username: Optional[str] = None

        self._admin_lock = threading.Lock()
        self._admin_mode: set = set()

        self._buy_cache: Dict[int, Dict[str, Any]] = {}

    def log(self, msg: str):
        try:
            self.log_queue.put(f"[{now_str()}] {msg}")
        except Exception:
            pass

    def is_admin_mode(self, user_id: int) -> bool:
        with self._admin_lock:
            return user_id in self._admin_mode

    def enable_admin_mode(self, user_id: int):
        with self._admin_lock:
            self._admin_mode.add(user_id)

    def disable_admin_mode(self, user_id: int):
        with self._admin_lock:
            self._admin_mode.discard(user_id)

    def _clear_user_state(self, context: ContextTypes.DEFAULT_TYPE):
        if context.user_data is not None:
            context.user_data.pop("waiting_deposit_amount", None)
            context.user_data.pop("waiting_input_qty_pid", None)

    def update_config(self, cfg: BotConfig):
        self.cfg = cfg
        self._bank_wake_event.set()

    # =========================
    # TELEGRAM UI
    # =========================
    def user_main_keyboard(self, user_id: int) -> InlineKeyboardMarkup:
        rows = [
            [InlineKeyboardButton("📦               Chuyên mục             ", callback_data="u:cats")],
            [InlineKeyboardButton("💳               Nạp tiền               ", callback_data="u:deposit"),
             InlineKeyboardButton("🧾               Lịch sử mua            ", callback_data="u:history")],
        ]
        if self.is_admin_mode(user_id):
            rows.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="a:panel")])
        return InlineKeyboardMarkup(rows)

    def admin_keyboard(self) -> InlineKeyboardMarkup:
        rows = [
            [InlineKeyboardButton("➕ Thêm chuyên mục", callback_data="a:addcat"),
             InlineKeyboardButton("✏️ Sửa chuyên mục", callback_data="a:editcat")],
            [InlineKeyboardButton("🗑 Xóa chuyên mục", callback_data="a:delcat")],
            [InlineKeyboardButton("➕ Thêm sản phẩm", callback_data="a:addprod"),
             InlineKeyboardButton("✏️ Sửa sản phẩm", callback_data="a:editprod")],
            [InlineKeyboardButton("🗑 Xóa sản phẩm", callback_data="a:delprod")],
            [InlineKeyboardButton("🏷 Coupon: Thêm/Update", callback_data="a:addcoupon"),
             InlineKeyboardButton("🚫 Coupon: Tắt", callback_data="a:delcoupon")],
            [InlineKeyboardButton("💸 % Hoa hồng (Affiliate)", callback_data="a:setcomm")],
            [InlineKeyboardButton("📣 Broadcast", callback_data="a:broadcast")],
            [InlineKeyboardButton("⬅️ Quay lại", callback_data="u:home")],
        ]
        return InlineKeyboardMarkup(rows)

    # =========================
    # LOG GROUP
    # =========================
    async def _send_log_group(self, bot, text: str):
        gid = int(self.cfg.log_group_id or 0)
        if gid == 0:
            return
        try:
            await bot.send_message(
                chat_id=gid,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception as e:
            self.log(f"Send log group error: {e}")

    async def _notify_purchase_log(
        self,
        bot,
        user_id: int,
        order_id: int,
        prod_name: str,
        price: int,
        discount: int,
        final_price: int,
        coupon_code: Optional[str],
    ):
        u = get_user(user_id)
        uname = ""
        fname = ""
        if u:
            uname = u["username"] or ""
            fname = u["first_name"] or ""

        user_line = f"<code>{user_id}</code>"
        if uname:
            user_line += f" • @{uname}"
        elif fname:
            user_line += f" • {fname}"

        text = (
            "🛒 <b>ĐƠN HÀNG MỚI</b>\n"
            f"🧾 Mã đơn: <b>#{order_id}</b>\n"
            f"👤 User: {user_line}\n"
            f"📦 Sản phẩm: <b>{prod_name}</b>\n"
            f"💵 Giá gốc: <b>{price:,}đ</b>\n"
            f"🏷 Giảm giá: <b>{discount:,}đ</b>\n"
            f"✅ Thanh toán: <b>{final_price:,}đ</b>\n"
            f"🎟 Coupon: <code>{coupon_code or 'none'}</code>\n"
            f"⏰ Thời gian: <code>{now_str()}</code>"
        )
        await self._send_log_group(bot, text)

    async def _notify_deposit_log(self, bot, user_id: int, amount: int, tid: str, desc: str):
        u = get_user(user_id)
        uname = ""
        fname = ""
        if u:
            uname = u["username"] or ""
            fname = u["first_name"] or ""

        user_line = f"<code>{user_id}</code>"
        if uname:
            user_line += f" • @{uname}"
        elif fname:
            user_line += f" • {fname}"

        text = (
            "💳 <b>NẠP TIỀN THÀNH CÔNG</b>\n"
            f"👤 User: {user_line}\n"
            f"💰 Số tiền: <b>{amount:,}đ</b>\n"
            f"🧾 Mã GD: <code>{tid}</code>\n"
            f"📝 Nội dung: <code>{(desc or '')[:300]}</code>\n"
            f"⏰ Thời gian: <code>{now_str()}</code>"
        )
        await self._send_log_group(bot, text)

    # =========================
    # TELEGRAM HANDLERS
    # =========================
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self._clear_user_state(context)
        user = update.effective_user
        upsert_user(user)

        if context.args:
            arg0 = context.args[0].strip()
            if arg0.lower().startswith("ref_"):
                ref_id = safe_int(arg0.split("_", 1)[1], 0)
                if ref_id > 0:
                    set_ref_if_empty(user.id, ref_id)

        try:
            if self.cfg.start_video_path and os.path.exists(self.cfg.start_video_path):
                with open(self.cfg.start_video_path, "rb") as video_file:
                    await update.message.reply_video(video=video_file, caption="🎉 Chào mừng bạn!")
        except Exception as e:
            self.log(f"Send start video error: {e}")

        bal = get_balance(user.id)
        txt = (
            f"👋 Xin chào <b>{user.first_name}</b>\n"
            f"💰 Số dư: <b>{bal:,}đ</b>\n\n"
            f"Chọn chức năng bên dưới:"
        )
        await update.message.reply_text(txt, reply_markup=self.user_main_keyboard(user.id), parse_mode=ParseMode.HTML)

    async def cmd_checkadmin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        upsert_user(user)
        if user.id == int(self.cfg.admin_id or 0):
            self.enable_admin_mode(user.id)
            await update.message.reply_text("✅ Đã bật chế độ Admin ẩn.", reply_markup=self.user_main_keyboard(user.id))
        else:
            await update.message.reply_text("❌ Bạn không phải admin.")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self._clear_user_state(context)
        txt = (
            "📌 Lệnh:\n"
            "/start\n"
            "/checkadmin (admin ẩn)\n"
            "/help\n"
            "/getchatid\n\n"
            "Admin (sau /checkadmin):\n"
            "Dùng Admin Panel trong nút."
        )
        await update.message.reply_text(txt)

    async def cmd_getchatid(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        await update.message.reply_text(
            f"🆔 Chat ID: <code>{chat.id}</code>\n📝 Type: <b>{chat.type}</b>",
            parse_mode=ParseMode.HTML,
        )

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        user = q.from_user
        upsert_user(user)

        data = q.data or ""
        if data == "u:home":
            self._clear_user_state(context)
            bal = get_balance(user.id)
            await q.edit_message_text(
                f"🏠 Menu chính\n💰 Số dư: <b>{bal:,}đ</b>",
                reply_markup=self.user_main_keyboard(user.id),
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "u:cats":
            self._clear_user_state(context)
            cats = list_categories()
            if not cats:
                await q.edit_message_text("❌ Chưa có chuyên mục. Admin thêm chuyên mục trước.", reply_markup=self.user_main_keyboard(user.id))
                return
            rows = []
            for c in cats:
                rows.append([InlineKeyboardButton(f"📁                 {c['name']}             ", callback_data=f"u:cat:{c['id']}")])
            rows.append([InlineKeyboardButton("⬅️              Quay lại                ", callback_data="u:home")])
            await q.edit_message_text("📦 Chọn chuyên mục:", reply_markup=InlineKeyboardMarkup(rows))
            return

        if data.startswith("u:cat:"):
            self._clear_user_state(context)
            cid = safe_int(data.split(":")[2], 0)
            prods = list_products_by_category(cid)
            if not prods:
                await q.edit_message_text(
                    "❌ Chưa có sản phẩm trong chuyên mục này.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại", callback_data="u:cats")]]),
                )
                return
            rows = []
            for p in prods:
                rows.append([InlineKeyboardButton(f"🛒 {p['name']} — {p['price']:,}đ (Kho: {p['stock']})", callback_data=f"u:prod:{p['id']}")])
            rows.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="u:cats")])
            await q.edit_message_text("🛍 Chọn sản phẩm:", reply_markup=InlineKeyboardMarkup(rows))
            return

        if data.startswith("u:prod:"):
            self._clear_user_state(context)
            pid = safe_int(data.split(":")[2], 0)
            prod = get_product(pid)
            if not prod:
                await q.edit_message_text(
                    "❌ Sản phẩm không tồn tại.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại", callback_data="u:cats")]]),
                )
                return
            
            stock = int(prod['stock'] or 0)
            txt = (
                f"🛒 <b>{prod['name']}</b>\n"
                f"💵 Giá: <b>{int(prod['price']):,}đ / 1 SP</b>\n"
                f"📦 Kho hiện còn: <b>{stock:,}</b>\n\n"
                f"👇 <b>BẤM CHỌN SỐ LƯỢNG HOẶC GÕ SỐ VÀO Ô CHAT ĐỂ MUA:</b>"
            )
            
            # Kích hoạt trạng thái chờ nhập số lượng ngay lập tức từ ô chat
            context.user_data["waiting_input_qty_pid"] = pid
            
            rows = []
            qty_options = [1, 2, 5, 10]
            qty_row = []
            for qty in qty_options:
                if stock >= qty:
                    qty_row.append(InlineKeyboardButton(f"🔢 {qty}", callback_data=f"u:qty:{pid}:{qty}"))
            if qty_row:
                rows.append(qty_row)
                
            rows.append([InlineKeyboardButton("⬅️ Chọn sản phẩm khác", callback_data="u:cats")])
            await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML)
            return

        if data.startswith("u:qty:"):
            self._clear_user_state(context)
            parts = data.split(":")
            pid = safe_int(parts[2], 0)
            qty = safe_int(parts[3], 1)
            
            prod = get_product(pid)
            if not prod or int(prod["stock"] or 0) < qty:
                await q.edit_message_text("❌ Kho không đủ số lượng bạn yêu cầu.", reply_markup=self.user_main_keyboard(user.id))
                return
                
            self._buy_cache[user.id] = {"product_id": pid, "quantity": qty}
            total_price = int(prod["price"]) * qty
            
            txt = (
                f"🛍 <b>XÁC NHẬN ĐƠN HÀNG</b>\n"
                f"📦 Sản phẩm: <b>{prod['name']}</b>\n"
                f"🔢 Số lượng: <b>{qty} SP</b>\n"
                f"💵 Tổng tiền: <b>{total_price:,}đ</b>\n\n"
                f"👇 <b>VUI LÒNG CHỌN PHƯƠNG THỨC THANH TOÁN:</b>"
            )
            
            rows = [
                [
                    InlineKeyboardButton("💰 Bằng Số Dư Ví", callback_data="u:pay:balance"),
                    InlineKeyboardButton("💳 Chuyển Khoản Trực Tiếp", callback_data="u:pay:bank")
                ],
                [InlineKeyboardButton("❌ Hủy Đơn Hàng", callback_data="u:home")]
            ]
            await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML)
            return

        if data == "u:pay:balance":
            self._clear_user_state(context)
            await self._finalize_purchase_with_qty(user.id, context=context, query=q)
            return

        if data == "u:pay:bank":
            self._clear_user_state(context)
            cache = self._buy_cache.get(user.id)
            if not cache:
                await q.edit_message_text("❌ Không tìm thấy thông tin đơn hàng.", reply_markup=self.user_main_keyboard(user.id))
                return
                
            pid = cache.get("product_id")
            qty = safe_int(cache.get("quantity"), 1)
            prod = get_product(pid)
            
            if not prod:
                await q.edit_message_text("❌ Sản phẩm không tồn tại.", reply_markup=self.user_main_keyboard(user.id))
                return
                
            total_price = int(prod["price"]) * qty
            
            if not self.cfg.vietqr_stk or not self.cfg.vietqr_ctk or not self.cfg.vietqr_bank_id:
                await q.edit_message_text("❌ Admin chưa cấu hình VietQR ngân hàng trên GUI máy tính.", reply_markup=self.user_main_keyboard(user.id))
                return

            add_info = f"Napid {user.id}"
            qr_url = vietqr_image_url(
                bank_id=self.cfg.vietqr_bank_id,
                account_no=self.cfg.vietqr_stk,
                template=self.cfg.vietqr_template or DEFAULT_VIETQR_TEMPLATE,
                amount=total_price,
                add_info=add_info,
                account_name=self.cfg.vietqr_ctk,
            )
            
            msg = (
                f"💳 <b>THANH TOÁN CHUYỂN KHOẢN TRỰC TIẾP</b>\n"
                f"📦 Đơn hàng: <b>{prod['name']}</b> (Số lượng: {qty})\n"
                f"💰 Tổng tiền cần chuyển: <b>{total_price:,}đ</b>\n\n"
                f"✅ Nội dung bắt buộc: <code>{add_info}</code>\n\n"
                f"📌 <b>LƯU Ý:</b> Hệ thống AutoBank sẽ tự quét ngân hàng mỗi 15 giây. "
                f"Ngay sau khi bạn chuyển tiền thành công, hệ thống sẽ tự nạp tiền vào ví của bạn, "
                f"sau đó hãy bấm lại vào <b>Chuyên mục -> Chọn sản phẩm đó -> Chọn thanh toán bằng số dư ví</b> để nhận hàng nhé!\n\n"
                f"🔗 <b>MÃ QR THANH TOÁN:</b> {qr_url}"
            )
            await q.edit_message_text(msg, parse_mode=ParseMode.HTML, reply_markup=self.user_main_keyboard(user.id))
            return

        if data == "u:deposit":
            self._clear_user_state(context)
            await q.edit_message_text("💳 Nhập số tiền bạn muốn nạp (VD: 50000):")
            context.user_data["waiting_deposit_amount"] = True
            return

        if data == "u:history":
            self._clear_user_state(context)
            orders = list_orders(user.id, limit=15)
            if not orders:
                await q.edit_message_text("🧾 Chưa có đơn hàng.", reply_markup=self.user_main_keyboard(user.id))
                return
            lines = ["🧾 <b>Lịch sử mua</b> (15 đơn gần nhất):\n"]
            for o in orders:
                lines.append(f"#{o['id']} • {o['product_name'] or 'SP'} • {int(o['final_price']):,}đ • {o['status']} • {o['created_at']}")
            await q.edit_message_text("\n".join(lines), reply_markup=self.user_main_keyboard(user.id), parse_mode=ParseMode.HTML)
            return

        if data == "a:panel":
            self._clear_user_state(context)
            if not self.is_admin_mode(user.id):
                await q.edit_message_text("❌ Chưa bật admin. Dùng /checkadmin", reply_markup=self.user_main_keyboard(user.id))
                return
            await q.edit_message_text("🛠 <b>Admin Panel</b>", reply_markup=self.admin_keyboard(), parse_mode=ParseMode.HTML)
            return

        if data.startswith("a:"):
            self._clear_user_state(context)
            if not self.is_admin_mode(user.id):
                await q.edit_message_text("❌ Bạn không có quyền.", reply_markup=self.user_main_keyboard(user.id))
                return

            action = data.split(":")[1]
            if action == "addcat":
                await q.edit_message_text("➕ Gửi: <code>/addcat Tên chuyên mục</code>", parse_mode=ParseMode.HTML, reply_markup=self.admin_keyboard())
            elif action == "editcat":
                await q.edit_message_text("✏️ Gửi: <code>/editcat ID Tên mới</code>", parse_mode=ParseMode.HTML, reply_markup=self.admin_keyboard())
            elif action == "delcat":
                await q.edit_message_text("🗑 Gửi: <code>/delcat ID</code>", parse_mode=ParseMode.HTML, reply_markup=self.admin_keyboard())
            elif action == "addprod":
                await q.edit_message_text(
                    "➕ <b>CÚ PHÁP THÊM SẢN PHẨM MỚI:</b>\n"
                    "<code>/addprod CAT_ID | Tên SP | Giá | Kho | Danh sách nội dung (mỗi món 1 dòng)</code>\n\n"
                    "<b>Ví dụ:</b>\n"
                    "<code>/addprod 1 | Netflix 1 Tháng | 50000 | 2 |\n"
                    "tai_khoan_1@gmail.com/pass123\n"
                    "tai_khoan_2@gmail.com/pass456</code>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.admin_keyboard(),
                )
            elif action == "editprod":
                await q.edit_message_text(
                    "✏️ <b>CÚ PHÁP SỬA SẢN PHẨM:</b>\n"
                    "<code>/editprod PROD_ID | Tên SP | Giá [| Kho | Nội dung thêm]</code>\n\n"
                    "<b>Ví dụ sửa tên/giá:</b>\n"
                    "<code>/editprod 1 | Netflix VIP | 60000</code>\n\n"
                    "<b>Ví dụ nạp thêm kho:</b>\n"
                    "<code>/editprod 1 | Netflix VIP | 60000 | 2 |\n"
                    "acc1@gmail.com|pass1\n"
                    "acc2@gmail.com|pass2</code>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.admin_keyboard(),
                )
            elif action == "delprod":
                await q.edit_message_text("🗑 Gửi: <code>/delprod PROD_ID</code>", parse_mode=ParseMode.HTML, reply_markup=self.admin_keyboard())
            elif action == "addcoupon":
                await q.edit_message_text(
                    "🏷 Gửi:\n"
                    "<code>/coupon CODE | giam_tien | giam_% | apply_prod_id_or_all | min_order | max_uses</code>\n",
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.admin_keyboard(),
                )
            elif action == "delcoupon":
                await q.edit_message_text("🚫 Gửi: <code>/couponoff CODE</code>", parse_mode=ParseMode.HTML, reply_markup=self.admin_keyboard())
            elif action == "setcomm":
                await q.edit_message_text("💸 Gửi: <code>/setcomm USER_ID PERCENT</code>\nVí dụ: <code>/setcomm 123456789 10</code>", parse_mode=ParseMode.HTML, reply_markup=self.admin_keyboard())
            elif action == "broadcast":
                await q.edit_message_text("📣 Gửi: <code>/broadcast Nội dung</code>", parse_mode=ParseMode.HTML, reply_markup=self.admin_keyboard())
            else:
                await q.edit_message_text("⚠️ Không hỗ trợ.", reply_markup=self.admin_keyboard())

    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        upsert_user(user)
        text = (update.message.text or "").strip()

        if context.user_data.get("waiting_deposit_amount"):
            self._clear_user_state(context)
            amt = safe_pos_int(text, 0)
            if amt <= 0:
                await update.message.reply_text(
                    "❌ Số tiền không hợp lệ. Vui lòng bấm Nạp tiền lại và nhập số tiền lớn hơn 0.",
                    reply_markup=self.user_main_keyboard(user.id),
                )
                return
            if not self.cfg.vietqr_stk or not self.cfg.vietqr_ctk or not self.cfg.vietqr_bank_id:
                await update.message.reply_text("❌ Admin chưa cấu hình VietQR (STK/CTK/BANK).", reply_markup=self.user_main_keyboard(user.id))
                return

            add_info = f"Napid {user.id}"
            qr_url = vietqr_image_url(
                bank_id=self.cfg.vietqr_bank_id,
                account_no=self.cfg.vietqr_stk,
                template=self.cfg.vietqr_template or DEFAULT_VIETQR_TEMPLATE,
                amount=amt,
                add_info=add_info,
                account_name=self.cfg.vietqr_ctk,
            )
            msg = (
                "💳 <b>Nạp tiền</b>\n"
                f"✅ Nội dung chuyển khoản: <code>{add_info}</code>\n"
                f"💰 Số tiền: <b>{amt:,}đ</b>\n\n"
                "📌 Quét QR để chuyển khoản đúng nội dung.\n"
                f"🔗 QR: {qr_url}"
            )
            await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=self.user_main_keyboard(user.id))
            return

        # XỬ LÝ KHI KHÁCH TỰ GÕ SỐ LƯỢNG TỪ BÀN PHÍM
        if context.user_data.get("waiting_input_qty_pid"):
            pid = context.user_data.get("waiting_input_qty_pid")
            qty = safe_int(text, 0)
            
            prod = get_product(pid)
            if not prod:
                self._clear_user_state(context)
                await update.message.reply_text("❌ Sản phẩm không còn tồn tại.", reply_markup=self.user_main_keyboard(user.id))
                return
                
            stock = int(prod["stock"] or 0)
            if qty <= 0:
                await update.message.reply_text("❌ Số lượng phải là một số lớn hơn 0. Vui lòng nhập lại số lượng bằng số (hoặc bấm Menu để hủy):")
                return
                
            if qty > stock:
                await update.message.reply_text(f"❌ Kho chỉ còn {stock} sản phẩm, không đủ. Vui lòng nhập lại số lượng (hoặc bấm Menu để hủy):")
                return
                
            self._clear_user_state(context)
            self._buy_cache[user.id] = {"product_id": pid, "quantity": qty}
            total_price = int(prod["price"]) * qty
            
            txt = (
                f"🛍 <b>XÁC NHẬN ĐƠN HÀNG</b>\n"
                f"📦 Sản phẩm: <b>{prod['name']}</b>\n"
                f"🔢 Số lượng bạn chọn: <b>{qty} SP</b>\n"
                f"💵 Tổng tiền: <b>{total_price:,}đ</b>\n\n"
                f"👇 <b>VUI LÒNG CHỌN PHƯƠNG THỨC THANH TOÁN:</b>"
            )
            
            rows = [
                [
                    InlineKeyboardButton("💰 Bằng Số Dư Ví", callback_data="u:pay:balance"),
                    InlineKeyboardButton("💳 Chuyển Khoản Trực Tiếp", callback_data="u:pay:bank")
                ],
                [InlineKeyboardButton("❌ Hủy Đơn Hàng", callback_data="u:home")]
            ]
            await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML)
            return

        await update.message.reply_text("Chọn chức năng trong menu nhé.", reply_markup=self.user_main_keyboard(user.id))

    async def _finalize_purchase_with_qty(
        self,
        user_id: int,
        context: ContextTypes.DEFAULT_TYPE,
        query=None,
        message=None,
    ):
        async def reply_error(err_text: str):
            if query:
                try:
                    await query.edit_message_text(err_text, reply_markup=self.user_main_keyboard(user_id), parse_mode=ParseMode.HTML)
                    return
                except Exception:
                    pass
            if message:
                try:
                    await message.reply_text(err_text, reply_markup=self.user_main_keyboard(user_id), parse_mode=ParseMode.HTML)
                    return
                except Exception:
                    pass
            await context.bot.send_message(chat_id=user_id, text=err_text, reply_markup=self.user_main_keyboard(user_id), parse_mode=ParseMode.HTML)

        cache = self._buy_cache.get(user_id)
        if not cache:
            await reply_error("❌ Không có giao dịch mua đang chờ.")
            return
            
        pid = cache.get("product_id")
        qty = safe_pos_int(cache.get("quantity"), 1)

        con = db_connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            cur = con.cursor()

            # 1. Kiểm tra sản phẩm và kho
            cur.execute("SELECT id, name, price, stock, is_active FROM products WHERE id=? AND is_active=1", (pid,))
            prod = cur.fetchone()
            if not prod:
                con.rollback()
                await reply_error("❌ Sản phẩm không tồn tại hoặc đã ngừng kinh doanh.")
                return

            stock = int(prod["stock"] or 0)
            if stock < qty:
                con.rollback()
                await reply_error(f"❌ Số lượng trong kho không đủ (còn {stock} SP, yêu cầu {qty} SP).")
                return

            price_per_item = int(prod["price"])
            total_price = price_per_item * qty

            # 2. Kiểm tra số dư người dùng
            cur.execute("SELECT balance, ref_by FROM users WHERE user_id=?", (user_id,))
            user_row = cur.fetchone()
            bal = int(user_row["balance"] or 0) if user_row else 0
            if bal < total_price:
                con.rollback()
                await reply_error(
                    f"❌ Số dư không đủ.\n💰 Số dư hiện tại: <b>{bal:,}đ</b>\n"
                    f"🧾 Tổng tiền cần thanh toán ({qty} SP): <b>{total_price:,}đ</b>\n👉 Hãy nạp thêm tiền."
                )
                return

            # 3. Lấy đúng số lượng item chưa bán trong transaction
            cur.execute(
                "SELECT id, data_text FROM product_items WHERE product_id=? AND is_sold=0 LIMIT ?",
                (pid, qty)
            )
            items = cur.fetchall()
            if len(items) < qty:
                con.rollback()
                await reply_error("❌ Kho hàng phát sinh thiếu hụt đột xuất. Giao dịch đã hủy, số dư chưa bị trừ.")
                return

            # 4. Đánh dấu is_sold = 1
            for item in items:
                cur.execute("UPDATE product_items SET is_sold=1 WHERE id=? AND is_sold=0", (item["id"],))
                if cur.rowcount == 0:
                    con.rollback()
                    await reply_error("❌ Một trong các món hàng vừa được bán cho người khác. Vui lòng thử lại.")
                    return

            # 5. Trừ kho trong bảng products
            cur.execute("UPDATE products SET stock = stock - ? WHERE id=? AND stock >= ?", (qty, pid, qty))
            if cur.rowcount == 0:
                con.rollback()
                await reply_error("❌ Kho hàng thay đổi đột xuất, không đủ số lượng để trừ.")
                return

            # 6. Trừ tiền ví của user
            cur.execute("UPDATE users SET balance = balance - ? WHERE user_id=? AND balance >= ?", (total_price, user_id, total_price))
            if cur.rowcount == 0:
                con.rollback()
                await reply_error("❌ Trừ tiền thất bại (số dư không đủ).")
                return

            # 7. Tạo đơn hàng
            cur.execute("""
                INSERT INTO orders(user_id, product_id, price, discount, final_price, coupon_code, status, created_at)
                VALUES(?,?,?,?,?,?,?,?)
            """, (user_id, pid, total_price, 0, total_price, None, "PAID", now_str()))
            order_id = cur.lastrowid

            # 8. Hoa hồng Affiliate nếu có người giới thiệu
            ref_by = user_row["ref_by"] if user_row else None
            if ref_by and ref_by != user_id:
                cur.execute("SELECT percent FROM affiliate WHERE user_id=?", (ref_by,))
                aff_row = cur.fetchone()
                pct = int(aff_row["percent"]) if aff_row else 5
                comm = int((total_price * pct) / 100)
                if comm > 0:
                    cur.execute("UPDATE users SET balance = COALESCE(balance, 0) + ? WHERE user_id=?", (comm, ref_by))
                    cur.execute("""
                        INSERT INTO affiliate_earnings(referrer_id, referred_user_id, order_id, amount, created_at)
                        VALUES(?,?,?,?,?)
                    """, (ref_by, user_id, order_id, comm, now_str()))

            con.commit()
        except Exception as e:
            con.rollback()
            self.log(f"Lỗi hoàn tất đơn hàng: {e}")
            await reply_error("❌ Đã có lỗi xảy ra trong quá trình xử lý đơn hàng. Vui lòng thử lại.")
            return
        finally:
            con.close()

        self._buy_cache.pop(user_id, None)

        deliver_items_text = [f"▪️ <code>{item['data_text']}</code>" for item in items]
        items_delivery_string = "\n".join(deliver_items_text)

        # Cập nhật tin nhắn inline callback nếu có
        if query:
            try:
                await query.edit_message_text(
                    f"✅ <b>Thanh toán đơn #{order_id} thành công!</b>\nThông tin chi tiết đã được gửi bên dưới 👇",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

        # Gửi thông tin sản phẩm về chat riêng của user
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "✅ <b>MUA HÀNG THÀNH CÔNG</b>\n"
                f"🧾 Mã đơn hàng: <b>#{order_id}</b>\n"
                f"🛒 Sản phẩm: <b>{prod['name']}</b>\n"
                f"🔢 Số lượng mua: <b>{qty} SP</b>\n"
                f"✅ Tổng thanh toán: <b>{total_price:,}đ</b>\n\n"
                f"📦 <b>THÔNG TIN SẢN PHẨM CỦA BẠN:</b>\n"
                f"{items_delivery_string}\n\n"
                f"<i>Cảm ơn bạn đã tin tưởng ủng hộ cửa hàng!</i>"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=self.user_main_keyboard(user_id),
        )

        # Thông báo cho admin
        if int(self.cfg.admin_id or 0) > 0:
            try:
                await context.bot.send_message(
                    chat_id=int(self.cfg.admin_id),
                    text=f"🛎 ĐƠN HÀNG MỚI #{order_id}\nUser: {user_id}\nSP: {prod['name']}\nSố lượng: {qty}\nTổng tiền: {total_price:,}đ",
                )
            except Exception:
                pass

        # Gửi log vào nhóm
        await self._notify_purchase_log(
            context.bot,
            user_id=user_id,
            order_id=order_id,
            prod_name=prod["name"],
            price=total_price,
            discount=0,
            final_price=total_price,
            coupon_code=None,
        )

    # =========================
    # ADMIN COMMANDS
    # =========================
    def _is_admin(self, user_id: int) -> bool:
        return user_id == int(self.cfg.admin_id or 0)

    async def admin_guard(self, update: Update) -> bool:
        uid = update.effective_user.id
        if not self._is_admin(uid):
            await update.message.reply_text("❌ Không có quyền admin.")
            return False
        if not self.is_admin_mode(uid):
            await update.message.reply_text("⚠️ Bật admin trước bằng /checkadmin")
            return False
        return True

    async def cmd_addcat(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        name = " ".join(context.args).strip()
        if not name:
            await update.message.reply_text("Dùng: /addcat Tên chuyên mục")
            return
        cid = add_category(name)
        await update.message.reply_text(f"✅ Đã thêm chuyên mục #{cid}: {name}")

    async def cmd_editcat(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        if len(context.args) < 2:
            await update.message.reply_text("Dùng: /editcat ID Tên mới")
            return
        cid = safe_int(context.args[0], 0)
        name = " ".join(context.args[1:]).strip()
        if cid <= 0 or not name:
            await update.message.reply_text("❌ Sai dữ liệu.")
            return
        edit_category(cid, name)
        await update.message.reply_text(f"✅ Đã sửa chuyên mục #{cid} -> {name}")

    async def cmd_delcat(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        if len(context.args) < 1:
            await update.message.reply_text("Dùng: /delcat ID")
            return
        cid = safe_int(context.args[0], 0)
        if cid <= 0:
            await update.message.reply_text("❌ ID không hợp lệ.")
            return
        delete_category(cid)
        await update.message.reply_text(f"✅ Đã xóa (ẩn) chuyên mục #{cid}")

    def _parse_pipe(self, raw: str) -> List[str]:
        return [x.strip() for x in raw.split("|")]

    async def cmd_addprod(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        
        raw = update.message.text.replace("/addprod", "", 1).strip()
        parts = [x.strip() for x in raw.split("|", 4)]
        
        if len(parts) < 4:
            await update.message.reply_text(
                "➕ <b>CÚ PHÁP THÊM SẢN PHẨM MỚI:</b>\n"
                "<code>/addprod CAT_ID | Tên SP | Giá | Kho | Danh sách nội dung (mỗi món 1 dòng)</code>\n\n"
                "<b>Ví dụ:</b>\n"
                "<code>/addprod 1 | Netflix 1 Tháng | 50000 | 2 |\n"
                "tai_khoan_1@gmail.com/pass123\n"
                "tai_khoan_2@gmail.com/pass456</code>\n\n"
                "<i>Ghi chú: [Kho] nên là số lượng món dự kiến, hệ thống sẽ tự đếm số dòng nội dung thực tế.</i>",
                parse_mode=ParseMode.HTML
            )
            return
            
        cid = safe_pos_int(parts[0], 0)
        name = parts[1]
        price = safe_pos_int(parts[2], 0)
        
        if len(parts) >= 5:
            p3 = parts[3]
            if p3.isdigit() and "\n" not in p3:
                stock_declared = safe_int(p3, 0)
                raw_items = parts[4]
            else:
                stock_declared = -1
                raw_items = p3 + " | " + parts[4]
        else:
            stock_declared = -1
            raw_items = parts[3]
            
        items_list = [line.strip() for line in raw_items.splitlines() if line.strip()]
        actual_stock = len(items_list)
        
        if cid <= 0:
            await update.message.reply_text("❌ Mã chuyên mục (CAT_ID) phải là số > 0.")
            return
        if not name:
            await update.message.reply_text("❌ Tên sản phẩm không được để trống.")
            return
        if price <= 0:
            await update.message.reply_text("❌ Giá sản phẩm phải lớn hơn 0.")
            return
        if actual_stock == 0:
            await update.message.reply_text("❌ Danh sách nội dung sản phẩm trống. Mỗi món phải nằm trên 1 dòng.")
            return
            
        con = db_connect()
        cur = con.cursor()
        cur.execute(
            "INSERT INTO products(category_id, name, price, stock, deliver_text, is_active) VALUES(?,?,?,?,?,1)",
            (cid, name, int(price), actual_stock, "Hàng giao tự động bên dưới:"),
        )
        pid = cur.lastrowid
        
        for item in items_list:
            cur.execute(
                "INSERT INTO product_items(product_id, data_text, is_sold) VALUES(?,?,0)",
                (pid, item)
            )
            
        con.commit()
        con.close()
        
        warn_text = ""
        if stock_declared >= 0 and stock_declared != actual_stock:
            warn_text = f"\n⚠️ <i>Lưu ý: Số kho bạn nhập ({stock_declared}) khác số dòng thực tế ({actual_stock}). Hệ thống cập nhật kho = {actual_stock}.</i>"
            
        await update.message.reply_text(
            f"✅ Đã thêm sản phẩm <b>{name}</b> (Mã SP: #{pid}) thành công!\n"
            f"📦 Hệ thống nhận diện: <b>{actual_stock}</b> nội dung sản phẩm trong kho.{warn_text}",
            parse_mode=ParseMode.HTML
        )

    async def cmd_editprod(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        raw = update.message.text.replace("/editprod", "", 1).strip()
        parts = [x.strip() for x in raw.split("|", 4)]
        if len(parts) < 3:
            await update.message.reply_text(
                "✏️ <b>CÚ PHÁP SỬA SẢN PHẨM:</b>\n"
                "<code>/editprod PROD_ID | Tên SP | Giá [| Kho | Nội dung thêm]</code>\n\n"
                "<b>Ví dụ sửa tên/giá:</b>\n"
                "<code>/editprod 1 | Netflix VIP | 60000</code>\n\n"
                "<b>Ví dụ nạp thêm kho:</b>\n"
                "<code>/editprod 1 | Netflix VIP | 60000 | 2 |\n"
                "acc1@gmail.com|pass1\n"
                "acc2@gmail.com|pass2</code>",
                parse_mode=ParseMode.HTML
            )
            return
            
        pid = safe_pos_int(parts[0], 0)
        name = parts[1]
        price = safe_pos_int(parts[2], 0)
        
        if pid <= 0:
            await update.message.reply_text("❌ Mã sản phẩm (PROD_ID) phải là số > 0.")
            return
        prod = get_product(pid)
        if not prod:
            await update.message.reply_text(f"❌ Không tìm thấy sản phẩm #{pid} (hoặc sản phẩm đã bị xóa).")
            return
        if not name:
            await update.message.reply_text("❌ Tên sản phẩm không được để trống.")
            return
        if price <= 0:
            await update.message.reply_text("❌ Giá sản phẩm phải lớn hơn 0 (không được âm hoặc bằng 0).")
            return
            
        new_items_list = []
        if len(parts) >= 5:
            p3 = parts[3]
            if p3.isdigit() and "\n" not in p3:
                raw_items = parts[4]
            else:
                raw_items = p3 + " | " + parts[4]
            new_items_list = [line.strip() for line in raw_items.splitlines() if line.strip()]
        elif len(parts) == 4:
            raw_items = parts[3]
            if "\n" in raw_items or not raw_items.isdigit():
                new_items_list = [line.strip() for line in raw_items.splitlines() if line.strip()]

        actual_stock = edit_product(pid, name, price, new_items=new_items_list)
        
        add_text = f"\n📦 Đã nạp thêm <b>{len(new_items_list)}</b> món mới vào kho." if new_items_list else ""
        await update.message.reply_text(
            f"✅ Đã cập nhật sản phẩm <b>#{pid}</b> (Tên: <b>{name}</b>, Giá: <b>{price:,}đ</b>).\n"
            f"📦 Kho thực tế hiện có: <b>{actual_stock}</b> món.{add_text}",
            parse_mode=ParseMode.HTML
        )

    async def cmd_delprod(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        if len(context.args) < 1:
            await update.message.reply_text("Dùng: /delprod PROD_ID")
            return
        pid = safe_int(context.args[0], 0)
        if pid <= 0:
            await update.message.reply_text("❌ ID không hợp lệ.")
            return
        delete_product(pid)
        await update.message.reply_text(f"✅ Đã xóa (ẩn) sản phẩm #{pid}")

    async def cmd_coupon(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        raw = update.message.text.replace("/coupon", "", 1).strip()
        parts = self._parse_pipe(raw)
        if len(parts) < 6:
            await update.message.reply_text(
                "Dùng:\n/coupon CODE | giam_tien | giam_% | apply_prod_id_or_all | min_order | max_uses"
            )
            return
        code = parts[0].upper()
        damt = safe_int(parts[1], 0)
        dpct = safe_int(parts[2], 0)
        ap = parts[3].lower()
        min_order = safe_int(parts[4], 0)
        max_uses = safe_int(parts[5], 0)
        apply_pid = None if ap == "all" else safe_int(ap, 0)
        if not code:
            await update.message.reply_text("❌ CODE rỗng.")
            return
        if damt < 0 or min_order < 0 or max_uses < 0:
            await update.message.reply_text("❌ Tiền giảm, đơn tối thiểu và lượt dùng không được là số âm.")
            return
        if dpct < 0 or dpct > 100:
            await update.message.reply_text("❌ % giảm không hợp lệ (0-100).")
            return
        if damt == 0 and dpct == 0:
            await update.message.reply_text("❌ Phải có tiền giảm hoặc % giảm lớn hơn 0.")
            return
        if apply_pid is not None and apply_pid <= 0:
            await update.message.reply_text("❌ apply_prod_id_or_all phải là all hoặc số ID sản phẩm > 0.")
            return
        upsert_coupon(code, damt, dpct, apply_pid, min_order, max_uses)
        await update.message.reply_text(f"✅ Đã tạo/cập nhật coupon {code}")

    async def cmd_couponoff(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        if len(context.args) < 1:
            await update.message.reply_text("Dùng: /couponoff CODE")
            return
        code = context.args[0].upper()
        disable_coupon(code)
        await update.message.reply_text(f"✅ Đã tắt coupon {code}")

    async def cmd_setcomm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        if len(context.args) < 2:
            await update.message.reply_text("Dùng: /setcomm USER_ID PERCENT")
            return
        uid = safe_int(context.args[0], 0)
        pct = safe_int(context.args[1], 0)
        if uid <= 0 or pct < 0 or pct > 100:
            await update.message.reply_text("❌ Sai dữ liệu (USER_ID > 0, 0 <= PERCENT <= 100).")
            return
        set_aff_percent(uid, pct)
        await update.message.reply_text(f"✅ Đã set hoa hồng user {uid} = {pct}%")

    async def cmd_broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.admin_guard(update):
            return
        msg = update.message.text.replace("/broadcast", "", 1).strip()
        if not msg:
            await update.message.reply_text("Dùng: /broadcast Nội dung")
            return
        con = db_connect()
        cur = con.cursor()
        cur.execute("SELECT user_id FROM users")
        users = [int(r["user_id"]) for r in cur.fetchall()]
        con.close()

        sent = 0
        fail = 0
        for uid in users:
            try:
                await context.bot.send_message(chat_id=uid, text=msg)
                sent += 1
            except Exception:
                fail += 1
        await update.message.reply_text(f"✅ Broadcast xong. Sent={sent}, Fail={fail}")

    # =========================
    # BANK POLL LOOP
    # =========================
    async def _notify_deposit(self, bot, user_id: int, amount: int, tid: str):
        try:
            await bot.send_message(chat_id=user_id, text=f"✅ Nạp thành công {amount:,}đ\n🧾 Mã GD: {tid}")
        except Exception:
            pass

    async def _notify_admin_deposit(self, bot, user_id: int, amount: int, tid: str, desc: str):
        if int(self.cfg.admin_id or 0) <= 0:
            return
        try:
            await bot.send_message(
                chat_id=int(self.cfg.admin_id),
                text=f"💳 Nạp mới\nUser: {user_id}\nTiền: {amount:,}đ\nMã GD: {tid}\nND: {desc[:200]}",
            )
        except Exception:
            pass

    def poll_bank_and_credit_sync(self):
        if not self.cfg.bank_v3_token:
            self.log("Bank V2 chưa cấu hình (TOKEN). Bỏ qua poll.")
            return

        try:
            url = build_coderent_v2_url(
                self.cfg.bank_v3_name,
                self.cfg.bank_v3_token,
            )

            self.log("Polling bank...")

            r = requests.get(url, timeout=25)
            data = r.json()

            txs = data.get("transactions", []) or []
            self.log(f"Transactions found: {len(txs)}")
            if not txs:
                return

            # Kiểm tra xem hệ thống đã từng đồng bộ lịch sử ban đầu chưa
            con_init = db_connect()
            cur_init = con_init.cursor()
            cur_init.execute("SELECT value FROM bot_settings WHERE key='bank_synced_once'")
            synced_row = cur_init.fetchone()
            if not synced_row:
                cur_init.execute("SELECT COUNT(*) AS cnt FROM bank_seen")
                seen_cnt = cur_init.fetchone()["cnt"]
                if seen_cnt == 0:
                    self.log("ℹ️ Cơ sở dữ liệu mới: Đánh dấu các giao dịch cũ đã có trên ngân hàng là đã xử lý để tránh nạp trùng.")
                    for tx in txs:
                        tid = str(tx.get("transactionID", "")).strip()
                        if tid:
                            cur_init.execute("INSERT OR IGNORE INTO bank_seen(transaction_id, seen_at) VALUES(?,?)", (tid, now_str()))
                    cur_init.execute("INSERT OR REPLACE INTO bot_settings(key, value) VALUES('bank_synced_once', '1')")
                    con_init.commit()
                    con_init.close()
                    return
                else:
                    cur_init.execute("INSERT OR REPLACE INTO bot_settings(key, value) VALUES('bank_synced_once', '1')")
                    con_init.commit()
            con_init.close()

            # Xử lý các giao dịch nạp tiền
            for tx in txs:
                ttype = str(tx.get("type", "")).upper().strip()
                if ttype != "IN":
                    continue

                tid = str(tx.get("transactionID", "")).strip()
                amount = safe_int(tx.get("amount", 0), 0)
                desc = str(tx.get("description", "") or "")

                if not tid or amount <= 0:
                    continue

                credited = False
                user_id = 0

                con = db_connect()
                try:
                    con.execute("BEGIN IMMEDIATE")
                    cur = con.cursor()

                    cur.execute("SELECT transaction_id FROM bank_seen WHERE transaction_id=?", (tid,))
                    if cur.fetchone():
                        con.rollback()
                        continue

                    m = NAPID_RE.search(desc)
                    if not m:
                        # Giao dịch không khớp cú pháp Napid, đánh dấu đã xem để không quét lại
                        cur.execute("INSERT OR IGNORE INTO bank_seen(transaction_id, seen_at) VALUES(?,?)", (tid, now_str()))
                        con.commit()
                        continue

                    user_id = safe_int(m.group(1), 0)
                    if user_id <= 0:
                        cur.execute("INSERT OR IGNORE INTO bank_seen(transaction_id, seen_at) VALUES(?,?)", (tid, now_str()))
                        con.commit()
                        continue

                    # Tạo user nếu chưa tồn tại
                    cur.execute("""
                        INSERT OR IGNORE INTO users(user_id, username, first_name, balance, ref_by, created_at)
                        VALUES(?, '', '', 0, NULL, ?)
                    """, (user_id, now_str()))

                    # Cộng tiền vào ví
                    cur.execute("""
                        UPDATE users
                        SET balance = COALESCE(balance, 0) + ?
                        WHERE user_id=?
                    """, (amount, user_id))

                    # Ghi nhận lịch sử nạp
                    cur.execute("""
                        INSERT INTO deposits(user_id, amount, description, trans_id, status, created_at)
                        VALUES(?,?,?,?,?,?)
                    """, (user_id, amount, desc, tid, "SUCCESS", now_str()))

                    # Đánh dấu đã xử lý giao dịch
                    cur.execute("INSERT OR IGNORE INTO bank_seen(transaction_id, seen_at) VALUES(?,?)", (tid, now_str()))

                    con.commit()
                    credited = True
                except Exception as e:
                    con.rollback()
                    self.log(f"Lỗi credit giao dịch #{tid}: {e}")
                finally:
                    con.close()

                if credited and user_id > 0:
                    self.log(f"Deposit credited: user={user_id} amount={amount} tid={tid}")
                    if self._app and self._bot_loop:
                        bot = self._app.bot
                        asyncio.run_coroutine_threadsafe(
                            self._notify_deposit(bot, user_id, amount, tid),
                            self._bot_loop,
                        )
                        asyncio.run_coroutine_threadsafe(
                            self._notify_admin_deposit(bot, user_id, amount, tid, desc),
                            self._bot_loop,
                        )
                        asyncio.run_coroutine_threadsafe(
                            self._notify_deposit_log(bot, user_id, amount, tid, desc),
                            self._bot_loop,
                        )

        except Exception as e:
            self.log(f"Bank poll error: {e}")

    def bank_loop(self):
        self.log("Bank poll thread started.")
        while not self._stop_event.is_set():
            try:
                self.poll_bank_and_credit_sync()
            except Exception as e:
                self.log(f"Bank loop error: {e}")
            interval = max(5, int(self.cfg.poll_interval or 15))
            self._bank_wake_event.clear()
            self._bank_wake_event.wait(timeout=interval)
        self.log("Bank poll thread stopped.")

    # =========================
    # START/STOP
    # =========================
    def _build_application(self) -> Application:
        app = Application.builder().token(self.cfg.bot_token).build()

        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CommandHandler("checkadmin", self.cmd_checkadmin))
        app.add_handler(CommandHandler("getchatid", self.cmd_getchatid))

        app.add_handler(CommandHandler("addcat", self.cmd_addcat))
        app.add_handler(CommandHandler("editcat", self.cmd_editcat))
        app.add_handler(CommandHandler("delcat", self.cmd_delcat))
        app.add_handler(CommandHandler("addprod", self.cmd_addprod))
        app.add_handler(CommandHandler("editprod", self.cmd_editprod))
        app.add_handler(CommandHandler("delprod", self.cmd_delprod))
        app.add_handler(CommandHandler("coupon", self.cmd_coupon))
        app.add_handler(CommandHandler("couponoff", self.cmd_couponoff))
        app.add_handler(CommandHandler("setcomm", self.cmd_setcomm))
        app.add_handler(CommandHandler("broadcast", self.cmd_broadcast))

        app.add_handler(CallbackQueryHandler(self.on_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_message))
        return app

    def _bot_thread_main(self):
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
            loop = asyncio.get_event_loop()
            self._bot_loop = loop

            self._app = self._build_application()
            self.log("Telegram bot starting (polling)...")

            async def runner():
                await self._app.initialize()
                await self._app.start()

                try:
                    me = await self._app.bot.get_me()
                    self._bot_username = me.username
                    self.log(f"Bot username: @{self._bot_username}")
                except Exception:
                    pass

                await self._app.updater.start_polling(drop_pending_updates=True)

                while not self._stop_event.is_set():
                    await asyncio.sleep(0.5)

                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()

            loop.run_until_complete(runner())
            self.log("Telegram bot stopped.")

        except Exception as e:
            self.log(f"Bot thread error: {e}")

    def start(self, cfg: BotConfig):
        self.cfg = cfg
        save_config(self.cfg)

        if not self.cfg.bot_token:
            raise RuntimeError("Chưa nhập BOT TOKEN")
        if int(self.cfg.admin_id or 0) <= 0:
            raise RuntimeError("Chưa nhập ADMIN ID")

        self._stop_event.clear()
        self._bank_wake_event.clear()

        self._bot_thread = threading.Thread(target=self._bot_thread_main, daemon=True)
        self._bot_thread.start()

        self._bank_thread = threading.Thread(target=self.bank_loop, daemon=True)
        self._bank_thread.start()

        self.log("✅ Started all (Bot + Bank Poll).")

    def stop(self):
        self._stop_event.set()
        self._bank_wake_event.set()
        self.log("⛔ Stopping...")


# =========================================================
# GUI
# =========================================================
COLOR_PALETTE = {
    "bg_main": "#0a0e14",
    "card_bg": "#151b23",
    "card_sec": "#1c242f",
    "primary": "#00a3ff",
    "action": "#ff5e23",
    "success": "#26d07c",
    "danger": "#ff3b3b",
    "text_p": "#ffffff",
    "text_s": "#8b949e",
    "border": "#30363d",
}

class AppGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1150x850")
        ctk.set_appearance_mode("dark")
        self.configure(fg_color=COLOR_PALETTE["bg_main"])

        init_db()
        self.log_queue = queue.Queue()
        self.runner = BotRunner(self.log_queue)
        self.cfg = self.runner.cfg

        self._build_ui()
        self.after(200, self._poll_log_queue)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        left = ctk.CTkScrollableFrame(
            self,
            corner_radius=24,
            label_text="⚡ Cấu hình Hệ thống",
            label_font=ctk.CTkFont(size=22, weight="bold"),
            fg_color=COLOR_PALETTE["card_bg"],
            label_fg_color=COLOR_PALETTE["primary"],
            label_text_color="#ffffff",
            scrollbar_button_color=COLOR_PALETTE["border"],
            scrollbar_button_hover_color=COLOR_PALETTE["text_s"]
        )
        right = ctk.CTkFrame(self, corner_radius=24, fg_color=COLOR_PALETTE["card_bg"], border_width=1, border_color=COLOR_PALETTE["border"])
        
        left.grid(row=0, column=0, padx=20, pady=20, sticky="nsew")
        right.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")

        left.grid_columnconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        sec1 = ctk.CTkFrame(left, corner_radius=22, fg_color=COLOR_PALETTE["card_sec"], border_width=1, border_color=COLOR_PALETTE["border"])
        sec1.grid(row=1, column=0, padx=15, pady=15, sticky="ew")
        sec1.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(sec1, text="👑 Admin ID", text_color=COLOR_PALETTE["primary"], font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=15, pady=15, sticky="w")
        self.ent_admin = ctk.CTkEntry(sec1, corner_radius=14, fg_color=COLOR_PALETTE["bg_main"], border_color=COLOR_PALETTE["border"], height=40)
        self.ent_admin.grid(row=0, column=1, padx=15, pady=15, sticky="ew")
        self.ent_admin.insert(0, str(self.cfg.admin_id or ""))

        ctk.CTkLabel(sec1, text="🧾 Log Group", text_color=COLOR_PALETTE["primary"], font=ctk.CTkFont(weight="bold")).grid(row=1, column=0, padx=15, pady=15, sticky="w")
        self.ent_log_group = ctk.CTkEntry(sec1, corner_radius=14, fg_color=COLOR_PALETTE["bg_main"], border_color=COLOR_PALETTE["border"], height=40)
        self.ent_log_group.grid(row=1, column=1, padx=15, pady=15, sticky="ew")
        self.ent_log_group.insert(0, str(self.cfg.log_group_id or ""))

        ctk.CTkLabel(sec1, text="🤖 Bot Token", text_color=COLOR_PALETTE["primary"], font=ctk.CTkFont(weight="bold")).grid(row=2, column=0, padx=15, pady=15, sticky="w")
        self.ent_token = ctk.CTkEntry(sec1, corner_radius=14, fg_color=COLOR_PALETTE["bg_main"], border_color=COLOR_PALETTE["border"], height=40)
        self.ent_token.grid(row=2, column=1, padx=15, pady=15, sticky="ew")
        self.ent_token.insert(0, self.cfg.bot_token or "")

        ctk.CTkLabel(sec1, text="🎬 Video Start", text_color=COLOR_PALETTE["primary"], font=ctk.CTkFont(weight="bold")).grid(row=3, column=0, padx=15, pady=15, sticky="w")
        row = ctk.CTkFrame(sec1, fg_color="transparent")
        row.grid(row=3, column=1, padx=15, pady=15, sticky="ew")
        row.grid_columnconfigure(0, weight=1)
        self.ent_video = ctk.CTkEntry(row, corner_radius=14, fg_color=COLOR_PALETTE["bg_main"], border_color=COLOR_PALETTE["border"], height=40)
        self.ent_video.grid(row=0, column=0, padx=(0, 10), pady=0, sticky="ew")
        self.ent_video.insert(0, self.cfg.start_video_path or "")
        btn_pick = ctk.CTkButton(row, text="📁 Chọn", width=80, corner_radius=14, fg_color=COLOR_PALETTE["card_bg"], border_width=1, border_color=COLOR_PALETTE["primary"], hover_color=COLOR_PALETTE["primary"], command=self.pick_video)
        btn_pick.grid(row=0, column=1, padx=0, pady=0)

        sec2 = ctk.CTkFrame(left, corner_radius=22, fg_color=COLOR_PALETTE["card_sec"], border_width=1, border_color=COLOR_PALETTE["border"])
        sec2.grid(row=2, column=0, padx=15, pady=15, sticky="ew")
        sec2.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(sec2, text="🏦 VietQR Bank", text_color=COLOR_PALETTE["action"], font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=15, pady=15, sticky="w")
        self.cmb_vietqr_bank = ctk.CTkOptionMenu(
            sec2, values=[x[0] for x in VIETQR_BANKS], corner_radius=14, fg_color=COLOR_PALETTE["action"], button_color=COLOR_PALETTE["action"], button_hover_color="#e04a1b", command=self.on_vietqr_bank_changed
        )
        self.cmb_vietqr_bank.grid(row=0, column=1, padx=15, pady=15, sticky="ew")
        self.cmb_vietqr_bank.set(self.cfg.vietqr_bank_name or "Vietcombank")

        ctk.CTkLabel(sec2, text="💳 Số tài khoản", text_color=COLOR_PALETTE["action"], font=ctk.CTkFont(weight="bold")).grid(row=1, column=0, padx=15, pady=15, sticky="w")
        self.ent_vietqr_stk = ctk.CTkEntry(sec2, corner_radius=14, fg_color=COLOR_PALETTE["bg_main"], border_color=COLOR_PALETTE["border"], height=40)
        self.ent_vietqr_stk.grid(row=1, column=1, padx=15, pady=15, sticky="ew")
        self.ent_vietqr_stk.insert(0, self.cfg.vietqr_stk or "")

        ctk.CTkLabel(sec2, text="👤 Chủ tài khoản", text_color=COLOR_PALETTE["action"], font=ctk.CTkFont(weight="bold")).grid(row=2, column=0, padx=15, pady=15, sticky="w")
        self.ent_vietqr_ctk = ctk.CTkEntry(sec2, corner_radius=14, fg_color=COLOR_PALETTE["bg_main"], border_color=COLOR_PALETTE["border"], height=40)
        self.ent_vietqr_ctk.grid(row=2, column=1, padx=15, pady=15, sticky="ew")
        self.ent_vietqr_ctk.insert(0, self.cfg.vietqr_ctk or "")

        ctk.CTkLabel(sec2, text="🧩 Template", text_color=COLOR_PALETTE["action"], font=ctk.CTkFont(weight="bold")).grid(row=3, column=0, padx=15, pady=15, sticky="w")
        self.ent_vietqr_tpl = ctk.CTkEntry(sec2, corner_radius=14, fg_color=COLOR_PALETTE["bg_main"], border_color=COLOR_PALETTE["border"], height=40)
        self.ent_vietqr_tpl.grid(row=3, column=1, padx=15, pady=15, sticky="ew")
        self.ent_vietqr_tpl.insert(0, self.cfg.vietqr_template or DEFAULT_VIETQR_TEMPLATE)

        self.lbl_vietqr_preview = ctk.CTkLabel(
            sec2, text="🔎 QR preview: ND chuyển khoản sẽ là 'Napid <id>'", text_color=COLOR_PALETTE["text_s"], font=ctk.CTkFont(slant="italic", size=11)
        )
        self.lbl_vietqr_preview.grid(row=4, column=0, columnspan=2, padx=15, pady=(0, 15), sticky="w")

        sec3 = ctk.CTkFrame(left, corner_radius=22, fg_color=COLOR_PALETTE["card_sec"], border_width=1, border_color=COLOR_PALETTE["border"])
        sec3.grid(row=3, column=0, padx=15, pady=15, sticky="ew")
        sec3.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(sec3, text="🏦 Bank API", text_color="#8a8aff", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, padx=15, pady=15, sticky="w")
        self.cmb_bank_v3 = ctk.CTkOptionMenu(sec3, values=list(CODERENT_V2_CODES.keys()), corner_radius=14, fg_color="#6e6eff", button_color="#5a5aff", button_hover_color="#8a8aff")
        self.cmb_bank_v3.grid(row=0, column=1, padx=15, pady=15, sticky="ew")
        self.cmb_bank_v3.set(self.cfg.bank_v3_name or "Vietcombank")

        ctk.CTkLabel(sec3, text="🪙 Token API", text_color="#8a8aff", font=ctk.CTkFont(weight="bold")).grid(row=1, column=0, padx=15, pady=15, sticky="w")
        self.ent_bank_token = ctk.CTkEntry(sec3, corner_radius=14, fg_color=COLOR_PALETTE["bg_main"], border_color=COLOR_PALETTE["border"], height=40, placeholder_text="Nhập API Token...")
        self.ent_bank_token.grid(row=1, column=1, padx=15, pady=15, sticky="ew")
        self.ent_bank_token.insert(0, self.cfg.bank_v3_token or "")

        ctk.CTkLabel(sec3, text="⏱ Poll (giây)", text_color="#8a8aff", font=ctk.CTkFont(weight="bold")).grid(row=2, column=0, padx=15, pady=15, sticky="w")
        self.ent_poll = ctk.CTkEntry(sec3, corner_radius=14, fg_color=COLOR_PALETTE["bg_main"], border_color=COLOR_PALETTE["border"], height=40)
        self.ent_poll.grid(row=2, column=1, padx=15, pady=15, sticky="ew")
        self.ent_poll.insert(0, str(self.cfg.poll_interval or 15))

        ctk.CTkLabel(sec3, text="🧾 Text API", text_color="#8a8aff", font=ctk.CTkFont(weight="bold")).grid(row=3, column=0, padx=15, pady=15, sticky="w")
        self.txt_auto = ctk.CTkTextbox(sec3, corner_radius=14, height=80, border_width=1, border_color=COLOR_PALETTE["border"], fg_color=COLOR_PALETTE["bg_main"])
        self.txt_auto.grid(row=3, column=1, padx=15, pady=15, sticky="ew")
        if self.cfg.auto_bank_api_text:
            self.txt_auto.insert("1.0", self.cfg.auto_bank_api_text)

        btns = ctk.CTkFrame(left, fg_color="transparent")
        btns.grid(row=4, column=0, padx=25, pady=(20, 30), sticky="ew")
        btns.grid_columnconfigure((0, 1, 2), weight=1)

        self.btn_save = ctk.CTkButton(btns, text="💾 LƯU CONFIG", height=38, corner_radius=12, fg_color=COLOR_PALETTE["success"], hover_color="#1ea060", font=ctk.CTkFont(size=13, weight="bold"), command=self.save_cfg_ui)
        self.btn_save.grid(row=0, column=0, padx=8, pady=0, sticky="ew")

        self.btn_start = ctk.CTkButton(btns, text="🚀 START BOT", height=38, corner_radius=12, fg_color=COLOR_PALETTE["primary"], hover_color="#008ce9", font=ctk.CTkFont(size=13, weight="bold"), command=self.start_clicked)
        self.btn_start.grid(row=0, column=1, padx=8, pady=0, sticky="ew")

        self.btn_stop = ctk.CTkButton(btns, text="🛑 STOP BOT", height=38, corner_radius=12, fg_color=COLOR_PALETTE["danger"], hover_color="#d62e2e", font=ctk.CTkFont(size=13, weight="bold"), command=self.stop_clicked)
        self.btn_stop.grid(row=0, column=2, padx=8, pady=0, sticky="ew")

        t2 = ctk.CTkLabel(right, text="📟 LOG HỆ THỐNG PC", font=ctk.CTkFont(size=20, weight="bold"), text_color=COLOR_PALETTE["primary"])
        t2.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="w")

        cre = ctk.CTkLabel(right, text="by trthaodev - coderent.one", font=ctk.CTkFont(size=12, slant="italic"), text_color=COLOR_PALETTE["text_s"])
        cre.grid(row=0, column=0, padx=20, pady=(20, 10), sticky="e")

        self.txt_log = ctk.CTkTextbox(right, corner_radius=22, border_width=1, border_color=COLOR_PALETTE["border"], fg_color=COLOR_PALETTE["card_sec"], text_color="#e6edf3", font=ctk.CTkFont(size=13))
        self.txt_log.grid(row=1, column=0, padx=20, pady=15, sticky="nsew")

        hint = (
            "✅ PC chạy 24/7: không cần CRON.\n"
            "• User nạp: bot tạo QR VietQR với nội dung Napid <id>\n"
            "• Bank poll sẽ quét API V3 và cộng tiền tự động.\n"
            "• Đơn mua hàng và nạp tiền có thể log vào nhóm Telegram.\n\n"
            "Admin:\n"
            "• /checkadmin (kích hoạt quyền admin)\n"
            "• /getchatid để lấy chat id nhóm\n"
            "• Thêm chuyên mục: /addcat ...\n"
            "• Thêm sản phẩm: /addprod CAT_ID | Tên | Giá | Kho | Nội dung (mỗi món một dòng)\n"
            "• Sửa sản phẩm: /editprod PROD_ID | Tên | Giá [| Kho | Nội dung thêm]\n"
        )
        self.txt_log.insert("1.0", hint)

    def pick_video(self):
        path = filedialog.askopenfilename(
            title="Chọn video gửi khi /start",
            filetypes=[("Video files", "*.mp4 *.mov *.mkv *.avi"), ("All files", "*.*")],
        )
        if path:
            self.ent_video.delete(0, "end")
            self.ent_video.insert(0, path)

    def on_vietqr_bank_changed(self, bank_name: str):
        bank_id = dict(VIETQR_BANKS).get(bank_name, "vietcombank")
        self.cfg.vietqr_bank_name = bank_name
        self.cfg.vietqr_bank_id = bank_id

    def read_cfg_from_ui(self) -> BotConfig:
        cfg = BotConfig()
        cfg.admin_id = safe_int(self.ent_admin.get(), 0)
        cfg.log_group_id = safe_int(self.ent_log_group.get(), 0)
        cfg.bot_token = self.ent_token.get().strip()
        cfg.start_video_path = self.ent_video.get().strip()

        bank_name = self.cmb_vietqr_bank.get().strip()
        cfg.vietqr_bank_name = bank_name
        cfg.vietqr_bank_id = dict(VIETQR_BANKS).get(bank_name, "vietcombank")
        cfg.vietqr_stk = self.ent_vietqr_stk.get().strip()
        cfg.vietqr_ctk = self.ent_vietqr_ctk.get().strip()
        cfg.vietqr_template = self.ent_vietqr_tpl.get().strip() or DEFAULT_VIETQR_TEMPLATE

        cfg.bank_v3_name = self.cmb_bank_v3.get().strip()
        cfg.bank_v3_token = self.ent_bank_token.get().strip()
        cfg.poll_interval = max(5, safe_int(self.ent_poll.get(), 15))

        cfg.auto_bank_api_text = self.txt_auto.get("1.0", "end").strip()
        return cfg

    def save_cfg_ui(self):
        self.cfg = self.read_cfg_from_ui()
        save_config(self.cfg)
        self.runner.update_config(self.cfg)
        self._append_log("✅ Đã lưu cấu hình (áp dụng ngay chu kỳ poll mới).")

    def start_clicked(self):
        try:
            self.cfg = self.read_cfg_from_ui()
            save_config(self.cfg)
            self.runner.start(self.cfg)
            self._append_log("▶️ START OK.")
        except Exception as e:
            messagebox.showerror("Lỗi Start", str(e))

    def stop_clicked(self):
        try:
            self.runner.stop()
            self._append_log("⛔ Đã gửi tín hiệu STOP.")
        except Exception as e:
            messagebox.showerror("Lỗi Stop", str(e))

    def _append_log(self, line: str):
        self.txt_log.insert("end", f"\n{line}")
        self.txt_log.see("end")

    def _poll_log_queue(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                self._append_log(line)
        except queue.Empty:
            pass
        self.after(200, self._poll_log_queue)


# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":
    init_db()
    app = AppGUI()
    app.mainloop()
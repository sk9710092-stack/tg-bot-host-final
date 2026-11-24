#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
  TG HOSTING BOT  -  ADVANCED EDITION
==============================================================================
  * Every uploaded file is quarantined and sent (WITH the file attached) to the
    owner and every admin, with Approve / Reject buttons.
  * The first admin who taps a button wins - the buttons disappear from every
    other admin's inbox at once (no double-approve, no crash).
  * Approve  ->  extract / install deps / start  ->  the script goes LIVE
    immediately, and the user is notified.
  * Static safety scan + code preview for each upload.
  * Auto-install of missing pip / npm modules, crash watchdog with auto-restart,
    auto-start of previously running scripts after a server restart.
  * Ban system, broadcast (any media), subscriptions, admin tools, file manager.

  SETUP:  set the BOT_TOKEN environment variable  (or paste it in TOKEN below)
          and set OWNER_ID to YOUR OWN Telegram user id.
==============================================================================
"""
import os
import sys
import subprocess


def _bootstrap():
    """Install the bot's own dependencies if they are missing."""
    need = {"telebot": "pyTelegramBotAPI", "psutil": "psutil",
            "flask": "Flask", "requests": "requests"}
    for mod, pkg in need.items():
        try:
            __import__(mod)
        except ImportError:
            print(f"[setup] installing {pkg} ...")
            cmd = [sys.executable, "-m", "pip", "install", "-q", pkg]
            if subprocess.run(cmd).returncode != 0:
                subprocess.run(cmd + ["--break-system-packages"])


_bootstrap()

import re
import io
import json
import time
import uuid
import html
import shutil
import signal
import atexit
import hashlib
import logging
import sqlite3
import threading
import zipfile
import platform
from datetime import datetime, timedelta

import requests
import psutil
import telebot
from flask import Flask

# =============================================================================
# 1. CONFIGURATION
# =============================================================================
TOKEN = (os.environ.get("BOT_TOKEN") or "8981699293:AAGb0uj6yNDVQC3Kq2IThCLpr1eeMloDjNA").strip()

# IMPORTANT: this must be YOUR personal Telegram user id (not the bot's id).
OWNER_ID = int(os.environ.get("OWNER_ID") or "8380139605")
# Extra admins on start-up (comma separated ids) - more can be added in the bot.
EXTRA_ADMINS = [int(x) for x in (os.environ.get("ADMIN_IDS") or "6534289796").replace(" ", "").split(",")
                if x.strip().isdigit()]
YOUR_USERNAME = os.environ.get("OWNER_USERNAME") or "@West_Bangla"
UPDATE_CHANNEL = os.environ.get("UPDATE_CHANNEL") or "https://t.me/ssQvs"

FREE_USER_LIMIT = 3
SUBSCRIBED_USER_LIMIT = 15
ADMIN_LIMIT = 99
OWNER_LIMIT = float('inf')

MAX_UPLOAD_BYTES = 20 * 1024 * 1024          # Telegram bot download limit
MAX_ZIP_UNPACKED = 300 * 1024 * 1024         # zip-bomb protection
MAX_ZIP_FILES = 3000
UPLOAD_COOLDOWN = 4                          # seconds between uploads per user
AUTO_APPROVE_ADMINS = True                   # admin/owner uploads skip approval
AUTO_INSTALL = True                          # auto pip/npm install missing modules
AUTO_RESTART = True                          # restart crashed scripts
MAX_RESTARTS = 5                             # max restarts ...
RESTART_WINDOW = 600                         # ... inside this many seconds
STARTUP_WATCH = 6                            # seconds a script must survive to be "live"
MAX_SCRIPT_MEM_MB = 512                      # RAM cap for normal users (0 = off)
PENDING_TTL_DAYS = 7                         # un-reviewed uploads expire after this

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
PENDING_DIR = os.path.join(BASE_DIR, 'pending_uploads')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')

for _d in (UPLOAD_BOTS_DIR, PENDING_DIR, IROTECH_DIR):
    os.makedirs(_d, exist_ok=True)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("hostbot")
logging.getLogger("werkzeug").setLevel(logging.ERROR)

if not re.match(r'^\d{6,}:[\w-]{30,}$', TOKEN):
    print("\n[X] BOT TOKEN is missing or invalid.\n"
          "    Set the BOT_TOKEN environment variable, or paste your token into TOKEN\n"
          "    at the top of this file (get it from @BotFather).\n")
    sys.exit(1)

START_TIME = time.time()
SHUTDOWN = threading.Event()

# =============================================================================
# 2. SMALL UTILITIES
# =============================================================================
PEM = {
    "ok": '<tg-emoji emoji-id="6111726981760423576">✅</tg-emoji>',
    "no": '<tg-emoji emoji-id="6311965254817423602">❌</tg-emoji>',
    "warn": '<tg-emoji emoji-id="6314510813214284503">⚠️</tg-emoji>',
    "admin": '<tg-emoji emoji-id="5353032893096567467">📊</tg-emoji>',
    "user": '<tg-emoji emoji-id="5352861489541714456">👤</tg-emoji>',
    "money": '<tg-emoji emoji-id="6111799373434197692">💸</tg-emoji>',
    "msg": '<tg-emoji emoji-id="5337302974806922068">💬</tg-emoji>',
    "rocket": '<tg-emoji emoji-id="5352597830089347330">🚀</tg-emoji>',
    "pin": '<tg-emoji emoji-id="6111410240807245099">📌</tg-emoji>',
    "hi": '<tg-emoji emoji-id="5353027129250453493">👋</tg-emoji>',
    "add": '<tg-emoji emoji-id="6188038822509418008">➕</tg-emoji>',
    "rem": '<tg-emoji emoji-id="6188343249791358585">➖</tg-emoji>',
    "view": '<tg-emoji emoji-id="6188447235244562676">👀</tg-emoji>',
    "wait": '<tg-emoji emoji-id="6233136657722251031">⏳</tg-emoji>',
    "bell": '<tg-emoji emoji-id="6260082445018731350">🔔</tg-emoji>',
    "crown": '<tg-emoji emoji-id="6183661284467152997">👑</tg-emoji>',
    "gear": '<tg-emoji emoji-id="6260111633616475361">⚙️</tg-emoji>',
    "check": '<tg-emoji emoji-id="6188038822509418008">✅</tg-emoji>',
    "file": '<tg-emoji emoji-id="5352721946054268944">📁</tg-emoji>',
    "key": '<tg-emoji emoji-id="6233302765582424442">🔑</tg-emoji>',
    "join": '<tg-emoji emoji-id="5352597830089347330">➡️</tg-emoji>',
    # plain emojis (these keys were missing in the old file -> KeyError)
    "gift": "🎁", "lock": "🔒", "shield": "🛡", "live": "🟢", "dead": "🔴",
}

_TAG_RE = re.compile(r'<[^>]*>')
_EMOJI_RE = re.compile(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', re.S)


def esc(s):
    """HTML-escape anything user controlled (file names, usernames, logs...)."""
    return html.escape('' if s is None else str(s), quote=False)


def demoji(t):
    return _EMOJI_RE.sub(r'\1', t)


def plain(t):
    return html.unescape(_TAG_RE.sub('', demoji(t)))


def fmt_size(n):
    n = float(n or 0)
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or u == 'GB':
            return f"{n:.0f} {u}" if u == 'B' else f"{n:.1f} {u}"
        n /= 1024


def fmt_td(sec):
    sec = int(max(0, sec))
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def safe_filename(name):
    """Strip any path, keep only safe characters, keep the extension."""
    name = os.path.basename((name or '').replace('\\', '/'))
    stem, ext = os.path.splitext(name)
    stem = re.sub(r'[^\w.\-+()]', '_', stem, flags=re.UNICODE).strip('._') or 'file'
    ext = re.sub(r'[^\w.]', '', ext)[:8].lower()
    return stem[:80] + ext


def safe_stem(name):
    return os.path.splitext(safe_filename(name))[0] or 'project'


def fid_of(fn):
    return hashlib.md5(fn.encode('utf-8')).hexdigest()[:10]


def new_aid():
    return uuid.uuid4().hex[:8].upper()


# =============================================================================
# 3. TELEGRAM API LAYER (raw HTTP, with graceful fallbacks - never crashes)
# =============================================================================
API_URL = f"https://api.telegram.org/bot{TOKEN}"
_http = requests.Session()


def _call(method, payload=None, files=None, timeout=60):
    payload = payload or {}
    for attempt in range(4):
        try:
            if files:
                data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                        for k, v in payload.items()}
                r = _http.post(f"{API_URL}/{method}", data=data, files=files, timeout=timeout + 60)
            else:
                r = _http.post(f"{API_URL}/{method}", json=payload, timeout=timeout)
            res = r.json()
        except Exception as e:
            logger.warning("API %s network error: %s", method, e)
            time.sleep(1 + attempt)
            continue
        if res.get('ok'):
            return res
        if res.get('error_code') == 429:
            wait = int((res.get('parameters') or {}).get('retry_after', 3))
            time.sleep(min(wait, 30) + 1)
            continue
        return res
    return {'ok': False, 'error_code': 0, 'description': 'network error'}


def strip_fancy(markup):
    """Remove premium-emoji / colour fields from a keyboard (fallback form)."""
    if not markup:
        return markup
    m = json.loads(json.dumps(markup))
    for key in ('keyboard', 'inline_keyboard'):
        for row in m.get(key, []):
            for b in row:
                b.pop('style', None)
                b.pop('icon_custom_emoji_id', None)
    return m


def _smart(method, payload, markup=None, text_key=None, files=None):
    """Call a Telegram method; degrade gracefully (strip fancy buttons, then
    strip HTML) if Telegram rejects the request. Returns result or None."""
    attempts = [(markup, 'HTML')]
    if markup:
        attempts.append((strip_fancy(markup), 'HTML'))
    attempts.append((strip_fancy(markup) if markup else None, None))
    for mk, parse in attempts:
        p = dict(payload)
        if mk is not None:
            p['reply_markup'] = mk
        if parse:
            p['parse_mode'] = parse
        elif text_key and text_key in p:
            p[text_key] = plain(p[text_key])
        res = _call(method, p, files=files)
        if res.get('ok'):
            return res.get('result', True)
        code = res.get('error_code')
        desc = (res.get('description') or '').lower()
        if 'not modified' in desc:
            return True
        if code in (401, 403, 404) or 'chat not found' in desc or 'message to edit not found' in desc \
                or "can't be edited" in desc or 'message to delete not found' in desc:
            logger.info("API %s skipped: %s", method, desc)
            return None
        logger.warning("API %s failed (%s): %s - retrying simpler", method, code, desc)
    return None


def ibtn(text, data=None, emoji_id=None, style=None, url=None):
    b = {"text": text}
    if url:
        b["url"] = url
    else:
        b["callback_data"] = data
    if emoji_id:
        b["icon_custom_emoji_id"] = emoji_id
    if style:
        b["style"] = style
    return b


def rbtn(text, emoji_id=None, style=None):
    b = {"text": text}
    if emoji_id:
        b["icon_custom_emoji_id"] = emoji_id
    if style:
        b["style"] = style
    return b


def send(chat_id, text, kb=None, inline=None):
    """Send a message. Returns the Telegram message dict, or None on failure."""
    markup = None
    if inline:
        markup = {'inline_keyboard': inline}
    elif kb:
        markup = {'keyboard': kb, 'resize_keyboard': True}
    payload = {'chat_id': chat_id, 'text': text[:4090],
               'link_preview_options': {'is_disabled': True}}
    return _smart('sendMessage', payload, markup, 'text')


def edit_text(chat_id, mid, text, inline=None):
    payload = {'chat_id': chat_id, 'message_id': mid, 'text': text[:4090],
               'link_preview_options': {'is_disabled': True}}
    return _smart('editMessageText', payload, {'inline_keyboard': inline or []}, 'text')


def edit_caption(chat_id, mid, caption, inline=None):
    payload = {'chat_id': chat_id, 'message_id': mid, 'caption': caption[:1024]}
    return _smart('editMessageCaption', payload, {'inline_keyboard': inline or []}, 'caption')


def answer_cb(cid, text='', alert=False):
    try:
        _call('answerCallbackQuery', {'callback_query_id': cid, 'text': text[:200],
                                      'show_alert': bool(alert)}, timeout=15)
    except Exception:
        pass


def send_doc(chat_id, file_id=None, path=None, caption='', inline=None, filename=None):
    """Send a document by Telegram file_id (fast) or by uploading a local file.
    Returns the message_id or None."""
    markup = {'inline_keyboard': inline} if inline else None
    caption = caption[:1024]
    sources = []
    if file_id:
        sources.append(('id', file_id))
    if path and os.path.isfile(path):
        sources.append(('file', path))
    for kind, src in sources:
        payload = {'chat_id': chat_id, 'caption': caption}
        files = None
        if kind == 'id':
            payload['document'] = src
        else:
            with open(src, 'rb') as f:
                data = f.read()
            files = {'document': (filename or os.path.basename(src), data)}
        r = _smart('sendDocument', payload, markup, 'caption', files=files)
        if r:
            return r.get('message_id') if isinstance(r, dict) else None
    return None


# =============================================================================
# 4. DATABASE + IN-MEMORY STATE
# =============================================================================
DB_LOCK = threading.RLock()


def db_write(sql, params=()):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, timeout=30)
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.rowcount
        except Exception as e:
            logger.error("DB write error: %s | %s", e, sql[:80])
            return -1
        finally:
            conn.close()


def db_read(sql, params=()):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, timeout=30)
        try:
            return conn.execute(sql, params).fetchall()
        except Exception as e:
            logger.error("DB read error: %s | %s", e, sql[:80])
            return []
        finally:
            conn.close()


def init_db():
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, timeout=30)
        try:
            try:
                conn.execute('PRAGMA journal_mode=WAL')
            except Exception:
                pass
            for stmt in (
                'CREATE TABLE IF NOT EXISTS subscriptions (user_id INTEGER PRIMARY KEY, expiry TEXT)',
                'CREATE TABLE IF NOT EXISTS user_files (user_id INTEGER, file_name TEXT, file_type TEXT, '
                'PRIMARY KEY (user_id, file_name))',
                'CREATE TABLE IF NOT EXISTS active_users (user_id INTEGER PRIMARY KEY)',
                'CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)',
                'CREATE TABLE IF NOT EXISTS banned (user_id INTEGER PRIMARY KEY, reason TEXT)',
                'CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT)',
                'CREATE TABLE IF NOT EXISTS pending (aid TEXT PRIMARY KEY, user_id INTEGER, file_name TEXT, '
                'file_type TEXT, is_zip INTEGER, qpath TEXT, file_id TEXT, uname TEXT, fname TEXT, '
                'size INTEGER, created REAL, scan TEXT, msgs TEXT)',
            ):
                conn.execute(stmt)
            try:  # migration from the old schema
                conn.execute('ALTER TABLE user_files ADD COLUMN auto_run INTEGER DEFAULT 0')
            except sqlite3.OperationalError:
                pass
            conn.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
            for a in EXTRA_ADMINS:
                conn.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (a,))
            conn.commit()
        finally:
            conn.close()


# ---- in-memory mirrors -------------------------------------------------------
user_subscriptions = {}      # uid -> {'expiry': datetime}
user_files = {}              # uid -> [(file_name, file_type)]  (APPROVED files only)
active_users = set()
admin_ids = {OWNER_ID}
banned = set()
pending = {}                 # aid -> record   (waiting for admin decision)
HANDLED = {}                 # aid -> {'by','act','ts'}  (recent decisions)
PENDING_LOCK = threading.RLock()
states = {}                  # uid -> 'broadcast'
last_upload = {}             # uid -> timestamp
bot_locked = False


def get_setting(k, default=None):
    r = db_read('SELECT v FROM settings WHERE k=?', (k,))
    return r[0][0] if r else default


def set_setting(k, v):
    db_write('INSERT OR REPLACE INTO settings (k, v) VALUES (?, ?)', (k, str(v)))


def load_data():
    global bot_locked
    for u, e in db_read('SELECT user_id, expiry FROM subscriptions'):
        try:
            user_subscriptions[u] = {'expiry': datetime.fromisoformat(e)}
        except Exception:
            pass
    for u, fn, ft in db_read('SELECT user_id, file_name, file_type FROM user_files'):
        user_files.setdefault(u, []).append((fn, ft))
    active_users.update(x for (x,) in db_read('SELECT user_id FROM active_users'))
    admin_ids.update(x for (x,) in db_read('SELECT user_id FROM admins'))
    admin_ids.add(OWNER_ID)
    banned.update(x for (x,) in db_read('SELECT user_id FROM banned'))
    bot_locked = get_setting('bot_locked', '0') == '1'
    for row in db_read('SELECT aid,user_id,file_name,file_type,is_zip,qpath,file_id,uname,fname,size,'
                       'created,scan,msgs FROM pending'):
        aid, uid, fn, ft, z, qp, fid, un, fnm, size, cr, scan, msgs = row
        try:
            pending[aid] = {'aid': aid, 'user_id': uid, 'file_name': fn, 'file_type': ft,
                            'is_zip': bool(z), 'qpath': qp, 'file_id': fid, 'uname': un,
                            'fname': fnm, 'size': size, 'created': cr,
                            'scan': json.loads(scan or '[]'), 'msgs': json.loads(msgs or '[]')}
        except Exception as e:
            logger.error("bad pending row %s: %s", aid, e)
    logger.info("Loaded: %d users, %d admins, %d pending", len(active_users), len(admin_ids), len(pending))


def pending_save(rec):
    db_write('INSERT OR REPLACE INTO pending VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
             (rec['aid'], rec['user_id'], rec['file_name'], rec['file_type'], 1 if rec['is_zip'] else 0,
              rec['qpath'], rec['file_id'], rec['uname'] or '', rec['fname'] or '', rec['size'],
              rec['created'], json.dumps(rec['scan']), json.dumps(rec['msgs'])))


def pending_delete(aid):
    db_write('DELETE FROM pending WHERE aid=?', (aid,))


def save_user_file(uid, fn, ft):
    db_write('INSERT OR IGNORE INTO user_files (user_id, file_name, file_type, auto_run) VALUES (?,?,?,0)',
             (uid, fn, ft))
    db_write('UPDATE user_files SET file_type=? WHERE user_id=? AND file_name=?', (ft, uid, fn))
    with DB_LOCK:
        lst = [f for f in user_files.get(uid, []) if f[0] != fn]
        lst.append((fn, ft))
        user_files[uid] = lst


def remove_user_file_db(uid, fn):
    db_write('DELETE FROM user_files WHERE user_id=? AND file_name=?', (uid, fn))
    with DB_LOCK:
        lst = [f for f in user_files.get(uid, []) if f[0] != fn]
        if lst:
            user_files[uid] = lst
        else:
            user_files.pop(uid, None)


def set_auto_run(uid, fn, flag):
    db_write('UPDATE user_files SET auto_run=? WHERE user_id=? AND file_name=?', (1 if flag else 0, uid, fn))


def add_active_user(uid):
    active_users.add(uid)
    db_write('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (uid,))


def save_subscription(uid, exp):
    db_write('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?,?)', (uid, exp.isoformat()))
    user_subscriptions[uid] = {'expiry': exp}


def remove_subscription_db(uid):
    db_write('DELETE FROM subscriptions WHERE user_id=?', (uid,))
    user_subscriptions.pop(uid, None)


def add_admin_db(uid):
    db_write('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (uid,))
    admin_ids.add(uid)


def remove_admin_db(uid):
    if uid == OWNER_ID:
        return False
    had = uid in admin_ids
    db_write('DELETE FROM admins WHERE user_id=?', (uid,))
    admin_ids.discard(uid)
    return had


def ban_user(uid, reason=''):
    db_write('INSERT OR REPLACE INTO banned (user_id, reason) VALUES (?,?)', (uid, reason))
    banned.add(uid)


def unban_user(uid):
    had = uid in banned
    db_write('DELETE FROM banned WHERE user_id=?', (uid,))
    banned.discard(uid)
    return had


# ---- helpers ------------------------------------------------------------------
def get_user_folder(uid):
    f = os.path.join(UPLOAD_BOTS_DIR, str(uid))
    os.makedirs(f, exist_ok=True)
    return f


def get_user_file_limit(uid):
    if uid == OWNER_ID:
        return OWNER_LIMIT
    if uid in admin_ids:
        return ADMIN_LIMIT
    sub = user_subscriptions.get(uid)
    if sub and sub['expiry'] > datetime.now():
        return SUBSCRIBED_USER_LIMIT
    return FREE_USER_LIMIT


def count_pending(uid):
    with PENDING_LOCK:
        return sum(1 for r in pending.values() if r['user_id'] == uid)


def get_user_file_count(uid):
    return len(user_files.get(uid, [])) + count_pending(uid)


def lim_str(limit):
    return "Unlimited" if limit == float('inf') else str(limit)


def script_path(uid, fn):
    base = os.path.abspath(get_user_folder(uid))
    p = os.path.abspath(os.path.join(base, *fn.split('/')))
    if not p.startswith(base + os.sep):
        raise ValueError("unsafe path")
    return p


def log_path(uid, fn):
    return os.path.splitext(script_path(uid, fn))[0] + '.log'


def file_ft(uid, fn):
    for n, t in user_files.get(uid, []):
        if n == fn:
            return t
    return None


def find_file(uid, fid):
    for n, _t in user_files.get(uid, []):
        if fid_of(n) == fid:
            return n
    return None


def who(u):
    """Readable (HTML-safe) name for a telegram user object."""
    if u is None:
        return "System"
    name = esc(getattr(u, 'first_name', '') or 'Admin')
    un = getattr(u, 'username', None)
    return f"{name} (@{esc(un)})" if un else name


# =============================================================================
# 5. PROCESS MANAGEMENT  (start / stop / watchdog / auto-install)
# =============================================================================
bot_scripts = {}             # (uid, fn) -> info dict
SCRIPTS_LOCK = threading.RLock()
STARTING = set()

STDLIB = set(getattr(sys, 'stdlib_module_names', ())) or {
    'os', 'sys', 're', 'json', 'time', 'math', 'random', 'logging', 'threading', 'subprocess',
    'zipfile', 'tempfile', 'shutil', 'sqlite3', 'asyncio', 'datetime', 'collections', 'itertools',
    'functools', 'pathlib', 'typing', 'uuid', 'hashlib', 'base64', 'socket', 'ssl', 'http', 'urllib',
    'html', 'csv', 'io', 'traceback', 'signal', 'queue', 'string', 'struct', 'copy', 'glob'}

PIP_MAP = {
    'telebot': 'pyTelegramBotAPI', 'telegram': 'python-telegram-bot', 'PIL': 'Pillow',
    'cv2': 'opencv-python-headless', 'bs4': 'beautifulsoup4', 'yaml': 'PyYAML',
    'dotenv': 'python-dotenv', 'sklearn': 'scikit-learn', 'skimage': 'scikit-image',
    'Crypto': 'pycryptodome', 'dateutil': 'python-dateutil', 'jwt': 'PyJWT', 'serial': 'pyserial',
    'OpenSSL': 'pyOpenSSL', 'discord': 'discord.py', 'bson': 'pymongo', 'psycopg2': 'psycopg2-binary',
    'MySQLdb': 'mysqlclient', 'attr': 'attrs', 'speech_recognition': 'SpeechRecognition',
    'fake_useragent': 'fake-useragent', 'yt_dlp': 'yt-dlp', 'flask_cors': 'Flask-CORS',
    'googleapiclient': 'google-api-python-client', 'docx': 'python-docx', 'pptx': 'python-pptx',
    'magic': 'python-magic', 'socks': 'PySocks', 'Levenshtein': 'python-Levenshtein',
    'telethon': 'telethon', 'pyrogram': 'pyrogram', 'aiogram': 'aiogram', 'flask': 'Flask',
}


def child_env():
    """Environment for hosted scripts - never leak the host bot's token."""
    env = {}
    for k, v in os.environ.items():
        if k == 'BOT_TOKEN' or v == TOKEN:
            continue
        env[k] = v
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


def is_running(owner, fn):
    with SCRIPTS_LOCK:
        info = bot_scripts.get((owner, fn))
    return bool(info and info['process'].poll() is None)


def close_log(info):
    try:
        lf = info.get('log_file')
        if lf and not lf.closed:
            lf.close()
    except Exception:
        pass


def kill_tree(proc, grace=3):
    if proc is None:
        return
    try:
        parent = psutil.Process(proc.pid)
        procs = parent.children(recursive=True) + [parent]
    except psutil.Error:
        procs = []
    for p in procs:
        try:
            p.terminate()
        except psutil.Error:
            pass
    try:
        _gone, alive = psutil.wait_procs(procs, timeout=grace)
    except Exception:
        alive = []
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    try:
        proc.wait(timeout=2)
    except Exception:
        pass


def proc_mem_mb(proc):
    try:
        p = psutil.Process(proc.pid)
        rss = 0
        for x in [p] + p.children(recursive=True):
            try:
                rss += x.memory_info().rss
            except psutil.Error:
                pass
        return rss / 1048576.0
    except psutil.Error:
        return 0.0


def read_log_tail(path, limit=3000):
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            if size > limit * 4:
                f.seek(-limit * 4, os.SEEK_END)
            return f.read().decode('utf-8', errors='ignore')[-limit:]
    except Exception:
        return ''


def build_cmd(ft, path):
    if ft == 'js':
        return ['node', path]
    return [sys.executable, '-u', path]


def launch(owner, fn, append_log=False, restarts=None):
    """Start the process once. Returns (True, info) or (False, error_text)."""
    ft = file_ft(owner, fn)
    if ft is None:
        return False, "File record not found."
    try:
        path = script_path(owner, fn)
    except ValueError:
        return False, "Unsafe file path."
    if not os.path.isfile(path):
        return False, "Script file is missing on the server."
    key = (owner, fn)
    with SCRIPTS_LOCK:
        cur = bot_scripts.get(key)
        if cur and cur['process'].poll() is None:
            return False, "Already running."
    lp = log_path(owner, fn)
    try:
        if append_log and os.path.exists(lp) and os.path.getsize(lp) > 5 * 1024 * 1024:
            append_log = False
        lf = open(lp, 'ab' if append_log else 'wb')
    except Exception as e:
        return False, f"Cannot open log file: {e}"
    kw = dict(cwd=os.path.dirname(path), stdout=lf, stderr=subprocess.STDOUT,
              stdin=subprocess.DEVNULL, env=child_env())
    if os.name == 'nt':
        kw['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    else:
        kw['start_new_session'] = True
    try:
        proc = subprocess.Popen(build_cmd(ft, path), **kw)
    except FileNotFoundError:
        lf.close()
        return False, ("Node.js ('node') is not installed on the server." if ft == 'js'
                       else "Python executable not found.")
    except Exception as e:
        lf.close()
        return False, f"Failed to start: {e}"
    info = {'process': proc, 'log_file': lf, 'log_path': lp, 'file_name': fn, 'owner': owner,
            'type': ft, 'start_time': time.time(), 'starting': True,
            'restarts': list(restarts or [])}
    with SCRIPTS_LOCK:
        bot_scripts[key] = info
    return True, info


def stop_script(owner, fn, mark=True):
    with SCRIPTS_LOCK:
        info = bot_scripts.pop((owner, fn), None)
    if info:
        kill_tree(info['process'])
        close_log(info)
    if mark:
        set_auto_run(owner, fn, 0)
    return bool(info)


# ---- dependency installation --------------------------------------------------
def pip_install(args, timeout=300, cwd=None):
    cmd = [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '-q'] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                           encoding='utf-8', errors='ignore')
        if r.returncode != 0 and 'externally-managed-environment' in (r.stderr or ''):
            r = subprocess.run(cmd + ['--break-system-packages'], capture_output=True, text=True,
                               timeout=timeout, cwd=cwd, encoding='utf-8', errors='ignore')
        return r.returncode == 0, ((r.stderr or '') + (r.stdout or ''))[-1500:]
    except subprocess.TimeoutExpired:
        return False, "pip timed out."
    except Exception as e:
        return False, str(e)


def npm_install(cwd, args=None, timeout=600):
    npm = shutil.which('npm')
    if not npm:
        return False, "npm is not installed on the server."
    try:
        r = subprocess.run([npm, 'install'] + list(args or []), capture_output=True, text=True,
                           timeout=timeout, cwd=cwd, encoding='utf-8', errors='ignore')
        return r.returncode == 0, ((r.stderr or '') + (r.stdout or ''))[-1500:]
    except subprocess.TimeoutExpired:
        return False, "npm timed out."
    except Exception as e:
        return False, str(e)


def find_missing_module(text, ft):
    if ft == 'js':
        ms = re.findall(r"Cannot find module '([^']+)'", text or '')
        for mod in reversed(ms):
            if mod.startswith('.') or mod.startswith('/'):
                continue
            parts = mod.split('/')
            return '/'.join(parts[:2]) if mod.startswith('@') and len(parts) > 1 else parts[0]
        return None
    ms = re.findall(r"No module named '([^']+)'", text or '')
    return ms[-1].split('.')[0] if ms else None


def install_missing(mod, ft, folder):
    if ft == 'js':
        if not re.match(r'^(@[\w.\-]+/)?[\w.\-]+$', mod):
            return False, "invalid module name"
        return npm_install(folder, [mod])
    if mod in STDLIB:
        return False, f"'{mod}' is a built-in module"
    if os.path.exists(os.path.join(folder, mod + '.py')) or os.path.isdir(os.path.join(folder, mod)):
        return False, f"'{mod}' exists in your files but cannot be imported"
    pkg = PIP_MAP.get(mod, mod)
    if not re.match(r'^[A-Za-z0-9][A-Za-z0-9._\-]*$', pkg):
        return False, "invalid package name"
    return pip_install([pkg])


def install_project_deps(root):
    warns = []
    req = os.path.join(root, 'requirements.txt')
    if os.path.isfile(req):
        ok, out = pip_install(['-r', req], timeout=900, cwd=root)
        if not ok:
            warns.append("requirements.txt had errors: " + out[-300:])
    if os.path.isfile(os.path.join(root, 'package.json')):
        ok, out = npm_install(root)
        if not ok:
            warns.append("npm install had errors: " + out[-300:])
    return warns


# ---- start with auto-install --------------------------------------------------
def start_script_full(owner, fn, notify=None, append_log=False, restarts=None):
    """Start a script, watch it for a few seconds, auto-install missing modules.
    Returns (state, payload):
        ('live', info)            -> running
        ('finished', html_text)   -> ran and exited with code 0
        ('failed', html_text)     -> could not start / crashed
    """
    key = (owner, fn)
    with SCRIPTS_LOCK:
        if key in STARTING:
            return 'failed', "A start is already in progress."
        STARTING.add(key)
    try:
        ft = file_ft(owner, fn)
        try:
            folder = os.path.dirname(script_path(owner, fn))
        except ValueError:
            return 'failed', "Unsafe file path."
        tried = set()
        while True:
            ok, res = launch(owner, fn, append_log, restarts)
            if not ok:
                return 'failed', esc(res)
            info = res
            proc = info['process']
            deadline = time.time() + STARTUP_WATCH
            while time.time() < deadline and proc.poll() is None:
                time.sleep(0.3)
            if proc.poll() is None:
                info['starting'] = False
                set_auto_run(owner, fn, 1)
                return 'live', info
            rc = proc.returncode
            with SCRIPTS_LOCK:
                if bot_scripts.get(key) is info:
                    bot_scripts.pop(key, None)
            close_log(info)
            tail = read_log_tail(info['log_path'], 2500)
            mod = find_missing_module(tail, ft)
            if AUTO_INSTALL and mod and mod not in tried and len(tried) < 10:
                tried.add(mod)
                if notify:
                    send(notify, f"📦 Installing missing module <code>{esc(mod)}</code> ...")
                ok2, out = install_missing(mod, ft, folder)
                if ok2:
                    append_log = False
                    continue
                return 'failed', (f"Could not install <code>{esc(mod)}</code>:\n"
                                  f"<pre>{esc(out[-700:])}</pre>")
            shown = esc(tail[-1200:]) or "(no output)"
            if rc == 0:
                set_auto_run(owner, fn, 0)
                return 'finished', f"The script ran and exited normally (code 0).\n<pre>{shown}</pre>"
            return 'failed', f"The script crashed at startup (exit code {rc}).\n<pre>{shown}</pre>"
    except Exception as e:
        logger.exception("start_script_full")
        return 'failed', esc(str(e))
    finally:
        with SCRIPTS_LOCK:
            STARTING.discard(key)


# ---- watchdog -----------------------------------------------------------------
def _restart_job(owner, fn, restarts):
    n = len(restarts)
    time.sleep(min(3 * n, 20))
    if SHUTDOWN.is_set() or file_ft(owner, fn) is None:
        return
    # a crash caused by a missing module gets fixed first (inside start_script_full)
    state, payload = start_script_full(owner, fn, notify=owner, append_log=True, restarts=restarts)
    if state == 'live':
        send(owner, f"{PEM['ok']} <code>{esc(fn)}</code> restarted automatically "
                    f"(restart {n}/{MAX_RESTARTS}).")
    else:
        set_auto_run(owner, fn, 0)
        send(owner, f"{PEM['no']} <code>{esc(fn)}</code> could not be restarted.\n{payload}")


def handle_exit(key, info, rc):
    owner, fn = key
    tail = read_log_tail(info['log_path'], 1200)
    if rc == 0:
        set_auto_run(owner, fn, 0)
        send(owner, f"{PEM['ok']} <code>{esc(fn)}</code> finished (exit code 0).")
        return
    now = time.time()
    restarts = [t for t in info.get('restarts', []) if now - t < RESTART_WINDOW]
    if AUTO_RESTART and len(restarts) < MAX_RESTARTS:
        restarts.append(now)
        send(owner, f"{PEM['warn']} <code>{esc(fn)}</code> crashed (exit {rc}). "
                    f"Restarting {len(restarts)}/{MAX_RESTARTS} ...\n<pre>{esc(tail[-600:])}</pre>")
        threading.Thread(target=_restart_job, args=(owner, fn, restarts), daemon=True).start()
    else:
        set_auto_run(owner, fn, 0)
        send(owner, f"{PEM['no']} <code>{esc(fn)}</code> keeps crashing and was stopped.\n"
                    f"<pre>{esc(tail[-800:])}</pre>\nFix the code and start it again from <b>Check Files</b>.")


def watchdog_loop():
    while not SHUTDOWN.is_set():
        time.sleep(5)
        try:
            for key, info in list(bot_scripts.items()):
                if info.get('starting'):
                    continue
                proc = info['process']
                rc = proc.poll()
                if rc is None:
                    owner = key[0]
                    if MAX_SCRIPT_MEM_MB and owner not in admin_ids:
                        mb = proc_mem_mb(proc)
                        if mb > MAX_SCRIPT_MEM_MB:
                            if stop_script(owner, key[1], mark=True):
                                send(owner, f"{PEM['no']} <code>{esc(key[1])}</code> was stopped: "
                                            f"it used {mb:.0f} MB RAM (limit {MAX_SCRIPT_MEM_MB} MB).")
                    continue
                with SCRIPTS_LOCK:
                    if bot_scripts.get(key) is not info:
                        continue
                    bot_scripts.pop(key, None)
                close_log(info)
                handle_exit(key, info, rc)
        except Exception:
            logger.exception("watchdog")


def autostart_all():
    """After a server restart, bring back everything that was running."""
    time.sleep(3)
    rows = db_read('SELECT user_id, file_name, file_type FROM user_files WHERE auto_run=1')
    started = failed = 0
    for uid, fn, _ft in rows:
        if SHUTDOWN.is_set():
            return
        try:
            if not os.path.isfile(script_path(uid, fn)):
                set_auto_run(uid, fn, 0)
                continue
        except ValueError:
            continue
        state, _p = start_script_full(uid, fn, notify=None)
        if state == 'live':
            started += 1
        else:
            failed += 1
            set_auto_run(uid, fn, 0)
        time.sleep(0.5)
    if rows:
        logger.info("Auto-start: %d started, %d failed", started, failed)
        send(OWNER_ID, f"♻️ <b>Auto-start after restart</b>\n🟢 Started: {started}\n🔴 Failed: {failed}")


def delete_file(owner, fn):
    stop_script(owner, fn, mark=False)
    folder = get_user_folder(owner)
    try:
        if '/' in fn:
            top = fn.split('/')[0]
            remove_user_file_db(owner, fn)
            others = [n for n, _t in user_files.get(owner, []) if '/' in n and n.split('/')[0] == top]
            if not others:
                shutil.rmtree(os.path.join(folder, top), ignore_errors=True)
        else:
            remove_user_file_db(owner, fn)
            for p in (script_path(owner, fn), log_path(owner, fn)):
                if os.path.isfile(p):
                    os.remove(p)
    except Exception as e:
        logger.error("delete_file: %s", e)


def cleanup():
    SHUTDOWN.set()
    logger.warning("Shutting down - stopping hosted scripts ...")
    for key in list(bot_scripts.keys()):
        info = bot_scripts.pop(key, None)
        if info:
            kill_tree(info['process'], grace=2)
            close_log(info)


atexit.register(cleanup)


# =============================================================================
# 6. SAFETY SCAN + ZIP HANDLING
# =============================================================================
def _rules(raw):
    return [(sev, re.compile(rx, re.I), desc) for sev, rx, desc in raw]


PY_RULES = _rules([
    ('high', r'\bos\.system\s*\(', 'os.system() shell command'),
    ('high', r'\bsubprocess\b', 'runs system commands (subprocess)'),
    ('high', r'\b(eval|exec)\s*\(', 'eval()/exec() dynamic code'),
    ('high', r'__import__\s*\(', '__import__() dynamic import'),
    ('high', r'\b(marshal\.loads|zlib\.decompress|b64decode)\b', 'decodes hidden/obfuscated payload'),
    ('high', r'(/etc/passwd|/etc/shadow|\.ssh/|id_rsa|\.aws/|\.bash_history)', 'touches sensitive system files'),
    ('high', r'(bot_data\.db|upload_bots|pending_uploads|TG_HOSTING)', 'touches the host bot data/files'),
    ('high', r'(rm\s+-rf|mkfs|dd\s+if=|:\(\)\s*\{)', 'destructive shell command'),
    ('high', r'\bctypes\b', 'ctypes low-level access'),
    ('med', r'os\.environ|os\.getenv|\bgetenv\(', 'reads environment variables (secrets?)'),
    ('med', r'\bsocket\.', 'raw network sockets'),
    ('med', r'shutil\.rmtree|os\.(remove|unlink|rmdir|removedirs)\b', 'deletes files/folders'),
    ('med', r'os\.kill|killpg|\bpsutil\b', 'controls other processes'),
    ('med', r'\bpty\b|pexpect', 'terminal/pty access'),
    ('med', r'\b(curl|wget)\s', 'downloads via shell'),
    ('med', r'Fernet|\bAES\b|cryptography', 'encryption (ransomware pattern)'),
    ('med', r'\.\./\.\.', 'parent-directory traversal'),
    ('med', r'pynput|pyautogui|ImageGrab', 'keyboard/screen capture'),
    ('low', r'requests\.(get|post)|urllib|aiohttp|httpx', 'makes web requests'),
    ('low', r'open\s*\([^)]*[\'"]\s*[wa]', 'writes files'),
])
JS_RULES = _rules([
    ('high', r'child_process|\bexecSync\b|\bspawn(Sync)?\s*\(', 'runs system commands (child_process)'),
    ('high', r'\beval\s*\(|new Function\s*\(', 'eval()/new Function() dynamic code'),
    ('high', r'(/etc/passwd|\.ssh/|id_rsa|\.aws/)', 'touches sensitive system files'),
    ('high', r'(bot_data\.db|upload_bots|pending_uploads|TG_HOSTING)', 'touches the host bot data/files'),
    ('high', r'rm\s+-rf', 'destructive shell command'),
    ('med', r'process\.env', 'reads environment variables (secrets?)'),
    ('med', r'fs\.(rm|rmdir|unlink)(Sync)?\b', 'deletes files/folders'),
    ('med', r'require\([\'"](net|dgram|tls)[\'"]\)', 'raw network sockets'),
    ('med', r'\.\./\.\.', 'parent-directory traversal'),
    ('low', r'\b(axios|node-fetch|https?\.request|fetch\()', 'makes web requests'),
    ('low', r'fs\.(write|append)File', 'writes files'),
])


def scan_text(text, ext, label=None):
    rules = JS_RULES if ext == '.js' else PY_RULES
    comment = '//' if ext == '.js' else '#'
    found = {}
    for ln, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith(comment):
            continue
        for sev, rx, desc in rules:
            if rx.search(line):
                f = found.setdefault((label, desc), {'sev': sev, 'desc': desc, 'count': 0,
                                                     'lines': [], 'file': label})
                f['count'] += 1
                if len(f['lines']) < 4:
                    f['lines'].append(ln)
    return list(found.values())


def risk_of(findings):
    sev = {f['sev'] for f in findings}
    for s in ('high', 'med', 'low'):
        if s in sev:
            return s
    return 'clean'


RISK_LABEL = {'high': '🔴 HIGH RISK', 'med': '🟡 Medium', 'low': '🟢 Low', 'clean': '🟢 Clean'}


def scan_summary(findings):
    h = sum(1 for f in findings if f['sev'] == 'high')
    m = sum(1 for f in findings if f['sev'] == 'med')
    l = sum(1 for f in findings if f['sev'] == 'low')
    return f"{RISK_LABEL[risk_of(findings)]} ({h} high · {m} medium · {l} low)"


def format_scan_report(rec):
    f = rec['scan']
    if not f:
        return "🛡 <b>Scan Report</b>\n\n🟢 No suspicious patterns found.\n\n<i>Heuristic scan only - still read the code.</i>"
    order = {'high': 0, 'med': 1, 'low': 2}
    icon = {'high': '🔴', 'med': '🟡', 'low': '🔵'}
    lines = ["🛡 <b>Scan Report</b>", scan_summary(f), ""]
    for x in sorted(f, key=lambda z: order[z['sev']])[:30]:
        where = (esc(x['file']) + ':' if x.get('file') else 'line ') + ', '.join(str(i) for i in x['lines'])
        lines.append(f"{icon[x['sev']]} {esc(x['desc'])} ×{x['count']} <i>({where})</i>")
    lines.append("\n<i>Heuristic scan only - always read the code before approving.</i>")
    return "\n".join(lines)


def pick_main(items):
    items = list(items)
    for n in ('main.py', 'bot.py', 'app.py', 'index.py', 'run.py'):
        if n in items:
            return n, 'py'
    for n in ('index.js', 'main.js', 'bot.js', 'app.js'):
        if n in items:
            return n, 'js'
    py = sorted(f for f in items if f.lower().endswith('.py'))
    if py:
        return py[0], 'py'
    js = sorted(f for f in items if f.lower().endswith('.js'))
    if js:
        return js[0], 'js'
    return None, None


def zip_root_files(names):
    names = [n for n in names if not n.startswith('__MACOSX/') and not n.endswith('.DS_Store')]
    prefix = ''
    for _ in range(5):
        segs = {n[len(prefix):].split('/')[0] for n in names if n.startswith(prefix)}
        if len(segs) == 1:
            seg = next(iter(segs))
            if any(n.startswith(prefix + seg + '/') for n in names):
                prefix += seg + '/'
                continue
        break
    return [n[len(prefix):] for n in names if n.startswith(prefix) and '/' not in n[len(prefix):]]


def inspect_zip(path):
    """Validate a ZIP WITHOUT extracting it. Returns (ok, error, findings, names)."""
    findings, names = [], []
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if not infos:
                return False, "The ZIP is empty.", [], []
            if len(infos) > MAX_ZIP_FILES:
                return False, f"Too many files in ZIP (max {MAX_ZIP_FILES}).", [], []
            total = 0
            for i in infos:
                n = i.filename.replace('\\', '/')
                if n.startswith('/') or '..' in n.split('/') or re.match(r'^[A-Za-z]:', n):
                    return False, f"Unsafe path inside ZIP: {n}", [], []
                if ((i.external_attr >> 16) & 0o170000) == 0o120000:
                    return False, "ZIP contains symbolic links (not allowed).", [], []
                total += i.file_size
                if not n.endswith('/'):
                    names.append(n)
            if total > MAX_ZIP_UNPACKED:
                return False, f"ZIP is too large when unpacked (max {MAX_ZIP_UNPACKED // 1048576} MB).", [], []
            bad = z.testzip()
            if bad:
                return False, f"Corrupt file inside ZIP: {bad}", [], []
            scanned = 0
            for i in infos:
                ext = os.path.splitext(i.filename)[1].lower()
                if ext in ('.py', '.js') and i.file_size <= 1500000 and scanned < 80:
                    scanned += 1
                    findings += scan_text(z.read(i).decode('utf-8', 'ignore'), ext, label=i.filename)
    except zipfile.BadZipFile:
        return False, "Invalid or corrupt ZIP file.", [], []
    except Exception as e:
        return False, f"Could not read ZIP: {e}", [], []
    main, _ft = pick_main(zip_root_files(names))
    if not main:
        return False, "No .py or .js file found in the ZIP root folder.", [], names
    return True, '', findings[:60], names


def safe_extract(zip_path, dest):
    base = os.path.abspath(dest)
    os.makedirs(base, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for i in z.infolist():
            name = i.filename.replace('\\', '/')
            if name.startswith('__MACOSX/') or name.endswith('.DS_Store'):
                continue
            if ((i.external_attr >> 16) & 0o170000) == 0o120000:
                continue
            target = os.path.abspath(os.path.join(base, name))
            if not (target == base or target.startswith(base + os.sep)):
                raise zipfile.BadZipFile(f"Unsafe path: {name}")
            if name.endswith('/'):
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(i) as src, open(target, 'wb') as out:
                shutil.copyfileobj(src, out)


def single_root(dest):
    root = dest
    for _ in range(5):
        try:
            entries = [e for e in os.listdir(root) if e not in ('__MACOSX', '.DS_Store')]
        except OSError:
            break
        if len(entries) == 1 and os.path.isdir(os.path.join(root, entries[0])):
            root = os.path.join(root, entries[0])
        else:
            break
    return root


# =============================================================================
# 7. APPROVAL SYSTEM  (file + buttons to every admin, first tap wins)
# =============================================================================
def approval_buttons(aid):
    return [
        [ibtn("✅ Approve & Run", f"ap:ok:{aid}", "6111726981760423576", "success"),
         ibtn("❌ Reject", f"ap:no:{aid}", "6311965254817423602", "danger")],
        [ibtn("🛡 Scan Report", f"ap:scan:{aid}"), ibtn("👁 View Code", f"ap:view:{aid}")],
    ]


def build_caption(rec, status=None):
    kind = 'ZIP project' if rec['is_zip'] else rec['file_type'].upper()
    cap = (f"{PEM['bell']} <b>New file - approval needed</b>\n\n"
           f"{PEM['user']} {esc(rec['fname'][:40])} · @{esc(rec['uname'] or 'N/A')} · "
           f"<code>{rec['user_id']}</code>\n"
           f"{PEM['file']} <code>{esc(rec['file_name'][:60])}</code> · {kind} · {fmt_size(rec['size'])}\n"
           f"{PEM['shield']} {scan_summary(rec['scan'])}\n"
           f"{PEM['pin']} ID: <code>{rec['aid']}</code>")
    if status:
        cap += f"\n\n{status}"
    if len(cap) > 1000:
        cap = demoji(cap)
    return cap[:1024]


def update_cards(rec, status):
    """Edit the approval message in EVERY admin inbox: status text, buttons removed."""
    cap = build_caption(rec, status)
    for chat_id, mid in list(rec.get('msgs', [])):
        try:
            edit_caption(chat_id, mid, cap, inline=None)
        except Exception as e:
            logger.error("update_cards: %s", e)


def notify_admins_new(rec, only_admin=None):
    cap = build_caption(rec)
    targets = [only_admin] if only_admin else sorted(admin_ids)
    sent = 0
    for a in targets:
        try:
            mid = send_doc(a, file_id=rec['file_id'], path=rec['qpath'], caption=cap,
                           inline=approval_buttons(rec['aid']), filename=rec['file_name'])
        except Exception as e:
            logger.error("notify admin %s: %s", a, e)
            mid = None
        if mid:
            with PENDING_LOCK:
                rec['msgs'].append([a, mid])
            sent += 1
    pending_save(rec)
    return sent


def decide(aid, act, admin):
    """Atomically claim an approval. Only ONE admin can ever win - this is what
    prevents double-approve races and crashes. Returns (ok, message)."""
    aid = aid.upper()
    with PENDING_LOCK:
        rec = pending.pop(aid, None)
        if rec:
            HANDLED[aid] = {'by': who(admin), 'act': act, 'ts': time.time()}
            for k in [k for k, v in HANDLED.items() if time.time() - v['ts'] > 3600]:
                HANDLED.pop(k, None)
    if not rec:
        h = HANDLED.get(aid)
        if h:
            return False, f"Already {'approved' if h['act'] == 'ok' else 'rejected'} by {plain(h['by'])}."
        return False, "Not found - already handled or expired."
    pending_delete(aid)
    word = 'Approved' if act == 'ok' else 'Rejected'
    update_cards(rec, f"{PEM['wait']} <b>{word}</b> by {who(admin)} - "
                      f"{'installing & starting ...' if act == 'ok' else 'removing ...'}")
    threading.Thread(target=process_decision, args=(rec, act, admin), daemon=True).start()
    return True, f"{word} - working on it."


def finalize_approved(rec):
    """Move the quarantined upload into place, install deps and START it.
    Returns (state, final_file_name, payload, warnings)."""
    uid = rec['user_id']
    folder = get_user_folder(uid)
    warnings = []
    try:
        if not os.path.isfile(rec['qpath']):
            return 'error', rec['file_name'], "The uploaded file is missing from quarantine.", warnings
        if rec['is_zip']:
            proj = safe_stem(rec['file_name'])
            dest = os.path.join(folder, proj)
            for (o, f) in list(bot_scripts.keys()):
                if o == uid and f.split('/')[0] == proj and '/' in f:
                    stop_script(o, f, mark=False)
            for n, _t in list(user_files.get(uid, [])):
                if '/' in n and n.split('/')[0] == proj:
                    remove_user_file_db(uid, n)
            shutil.rmtree(dest, ignore_errors=True)
            safe_extract(rec['qpath'], dest)
            root = single_root(dest)
            main, ft = pick_main(f for f in os.listdir(root) if os.path.isfile(os.path.join(root, f)))
            if not main:
                shutil.rmtree(dest, ignore_errors=True)
                return 'error', rec['file_name'], "No .py or .js file found in the ZIP root.", warnings
            warnings += install_project_deps(root)
            fn = os.path.relpath(os.path.join(root, main), folder).replace(os.sep, '/')
        else:
            ft = rec['file_type']
            fn = rec['file_name']
            if is_running(uid, fn):
                stop_script(uid, fn, mark=False)
            shutil.copyfile(rec['qpath'], script_path(uid, fn))
        save_user_file(uid, fn, ft)
        state, payload = start_script_full(uid, fn, notify=uid)
        return state, fn, payload, warnings
    except Exception as e:
        logger.exception("finalize_approved")
        return 'error', rec['file_name'], esc(str(e))[:500], warnings
    finally:
        shutil.rmtree(os.path.join(PENDING_DIR, rec['aid']), ignore_errors=True)


def process_decision(rec, act, admin, auto=False, reason='', reply_chat=None):
    aid = rec['aid']
    uid = rec['user_id']
    by = who(admin)
    try:
        if act == 'no':
            shutil.rmtree(os.path.join(PENDING_DIR, aid), ignore_errors=True)
            txt = (f"{PEM['no']} <b>File rejected</b>\n\n{PEM['file']} <code>{esc(rec['file_name'])}</code>\n"
                   + (f"{PEM['msg']} {esc(reason)}\n" if reason else "")
                   + f"\nContact {esc(YOUR_USERNAME)} if you think this is a mistake.")
            send(uid, txt, kb=get_main_kb(uid))
            status = f"{PEM['no']} <b>Rejected</b> by {by}"
            update_cards(rec, status)
            if reply_chat and all(c != reply_chat for c, _m in rec.get('msgs', [])):
                send(reply_chat, status + f"\n<code>{esc(rec['file_name'])}</code>")
            return

        state, fn, payload, warnings = finalize_approved(rec)
        warn_txt = ("\n\n" + PEM['warn'] + " " + "\n".join(esc(w) for w in warnings)[:600]) if warnings else ""
        fid = fid_of(fn)
        manage = [[ibtn("📂 Manage this file", f"fm:open:{uid}:{fid}")]]
        by_txt = "auto-approved (admin upload)" if auto else f"approved by {by}"
        if state == 'live':
            pid = payload['process'].pid
            send(uid, f"{PEM['ok']} <b>Approved - your bot is LIVE!</b> {PEM['rocket']}\n\n"
                      f"{PEM['file']} <code>{esc(fn)}</code>\n{PEM['gear']} PID: <code>{pid}</code>"
                      f"{warn_txt}", kb=get_main_kb(uid), inline=manage)
            status = f"🟢 <b>LIVE</b> - {by_txt} · PID {pid}"
        elif state == 'finished':
            send(uid, f"{PEM['ok']} <b>Approved</b> - <code>{esc(fn)}</code>\n\n{payload}{warn_txt}",
                 kb=get_main_kb(uid), inline=manage)
            status = f"✅ <b>Approved</b> by {by} - script ran and exited"
        else:
            send(uid, f"{PEM['warn']} <b>Approved, but it could not run</b>\n"
                      f"{PEM['file']} <code>{esc(fn)}</code>\n\n{payload}{warn_txt}\n\n"
                      f"Fix the problem and upload again.", kb=get_main_kb(uid))
            status = f"⚠️ <b>Approved</b> by {by} - but it failed to start"
        update_cards(rec, status)
        if reply_chat and all(c != reply_chat for c, _m in rec.get('msgs', [])):
            send(reply_chat, status + f"\n<code>{esc(fn)}</code>")
    except Exception:
        logger.exception("process_decision")


def expire_pending():
    cutoff = time.time() - PENDING_TTL_DAYS * 86400
    with PENDING_LOCK:
        old = [a for a, r in pending.items() if r['created'] < cutoff]
        recs = [pending.pop(a) for a in old]
    for rec in recs:
        pending_delete(rec['aid'])
        process_decision(rec, 'no', None, reason="Expired - not reviewed in time.")


def maintenance_loop():
    while not SHUTDOWN.is_set():
        for _ in range(360):
            if SHUTDOWN.is_set():
                return
            time.sleep(10)
        try:
            expire_pending()
        except Exception:
            logger.exception("maintenance")


# =============================================================================
# 8. UPLOAD HANDLING
# =============================================================================
ALLOWED_EXT = ('.py', '.js', '.zip')


def process_upload(m):
    uid = m.from_user.id
    chat = m.chat.id
    doc = m.document
    kb = get_main_kb(uid)
    try:
        if bot_locked and uid not in admin_ids:
            send(chat, f"{PEM['warn']} The bot is locked by the admin right now.", kb=kb)
            return
        now = time.time()
        if uid not in admin_ids and now - last_upload.get(uid, 0) < UPLOAD_COOLDOWN:
            send(chat, f"{PEM['wait']} Please wait a few seconds between uploads.", kb=kb)
            return
        last_upload[uid] = now
        if not doc.file_name:
            send(chat, f"{PEM['no']} The file has no name.", kb=kb)
            return
        fn = safe_filename(doc.file_name)
        ext = os.path.splitext(fn)[1].lower()
        if ext not in ALLOWED_EXT:
            send(chat, f"{PEM['no']} Only <b>.py</b>, <b>.js</b> and <b>.zip</b> files are allowed.", kb=kb)
            return
        size = doc.file_size or 0
        if size > MAX_UPLOAD_BYTES:
            send(chat, f"{PEM['no']} Maximum file size is {MAX_UPLOAD_BYTES // 1048576} MB.", kb=kb)
            return
        limit = get_user_file_limit(uid)
        stem = os.path.splitext(fn)[0]
        replacing = any(n == fn or ('/' in n and ext == '.zip' and n.split('/')[0] == stem)
                        for n, _t in user_files.get(uid, []))
        if not replacing and get_user_file_count(uid) >= limit:
            send(chat, f"{PEM['warn']} File limit reached ({get_user_file_count(uid)}/{lim_str(limit)}). "
                       f"Delete a file or ask {esc(YOUR_USERNAME)} for a subscription.", kb=kb)
            return
        st = send(chat, f"{PEM['wait']} Receiving <code>{esc(fn)}</code> ...")
        try:
            fi = bot.get_file(doc.file_id)
            data = bot.download_file(fi.file_path)
        except telebot.apihelper.ApiTelegramException as e:
            msg = "File too large for Telegram bots." if 'too big' in str(e).lower() else f"Telegram error: {esc(e)}"
            send(chat, f"{PEM['no']} {msg}", kb=kb)
            return
        aid = new_aid()
        qdir = os.path.join(PENDING_DIR, aid)
        os.makedirs(qdir, exist_ok=True)
        qpath = os.path.join(qdir, fn)
        with open(qpath, 'wb') as f:
            f.write(data)

        findings = []
        if ext == '.zip':
            ok, err, findings, _names = inspect_zip(qpath)
            if not ok:
                shutil.rmtree(qdir, ignore_errors=True)
                send(chat, f"{PEM['no']} <b>ZIP rejected:</b> {esc(err)}", kb=kb)
                return
        else:
            text = data.decode('utf-8', errors='ignore')
            if ext == '.py':
                try:
                    compile(text, fn, 'exec')
                except (SyntaxError, ValueError) as e:
                    shutil.rmtree(qdir, ignore_errors=True)
                    line = getattr(e, 'lineno', '?')
                    send(chat, f"{PEM['no']} <b>Syntax error</b> in <code>{esc(fn)}</code> "
                               f"(line {line}): {esc(getattr(e, 'msg', e))}\nFix it and upload again.", kb=kb)
                    return
            findings = scan_text(text, ext)

        rec = {'aid': aid, 'user_id': uid, 'file_name': fn,
               'file_type': 'zip' if ext == '.zip' else ext[1:], 'is_zip': ext == '.zip',
               'qpath': qpath, 'file_id': doc.file_id, 'uname': m.from_user.username,
               'fname': m.from_user.first_name or 'User', 'size': len(data),
               'created': time.time(), 'scan': findings, 'msgs': []}

        if AUTO_APPROVE_ADMINS and uid in admin_ids:
            send(chat, f"{PEM['rocket']} Admin upload - installing and starting <code>{esc(fn)}</code> ...", kb=kb)
            process_decision(rec, 'ok', m.from_user, auto=True)
            return

        with PENDING_LOCK:
            pending[aid] = rec
        pending_save(rec)
        sent = notify_admins_new(rec)
        if sent == 0:
            logger.error("No admin could receive approval %s", aid)
        send(chat, f"{PEM['ok']} <b>File received!</b>\n\n{PEM['file']} <code>{esc(fn)}</code>\n"
                   f"{PEM['wait']} Waiting for admin approval.\n{PEM['pin']} ID: <code>{aid}</code>\n\n"
                   f"You will be notified as soon as it is reviewed.", kb=kb)
    except Exception as e:
        logger.exception("process_upload")
        send(chat, f"{PEM['no']} Upload failed: {esc(e)}", kb=kb)


# =============================================================================
# 9. KEYBOARDS + VIEWS
# =============================================================================
BTN_UPDATES = "📢 Updates Channel"
BTN_UPLOAD = "📤 Upload File"
BTN_FILES = "📂 Check Files"
BTN_SPEED = "⚡ Bot Speed"
BTN_STATS = "📊 Statistics"
BTN_SUBS = "💳 Subscriptions"
BTN_BROADCAST = "📢 Broadcast"
BTN_LOCK = "🔒 Lock Bot"
BTN_UNLOCK = "🔓 Unlock Bot"
BTN_RUNALL = "🟢 Running All Code"
BTN_ADMIN = "👑 Admin Panel"
BTN_CONTACT = "📞 Contact Owner"
BTN_PENDING = "⏳ Pending Approvals"
BTN_BACK = "🔙 Back"
ALL_BUTTONS = {BTN_UPDATES, BTN_UPLOAD, BTN_FILES, BTN_SPEED, BTN_STATS, BTN_SUBS, BTN_BROADCAST,
               BTN_LOCK, BTN_UNLOCK, BTN_RUNALL, BTN_ADMIN, BTN_CONTACT, BTN_PENDING, BTN_BACK, "🟢 Run All"}


def get_main_kb(uid):
    rows = [
        [rbtn(BTN_UPDATES, "5352597830089347330", "primary")],
        [rbtn(BTN_UPLOAD, "5352721946054268944", "primary"), rbtn(BTN_FILES, "6188447235244562676", "primary")],
        [rbtn(BTN_SPEED, "6186175669991379791", "primary"), rbtn(BTN_STATS, "5353032893096567467", "success")],
    ]
    if uid in admin_ids:
        rows += [
            [rbtn(BTN_SUBS, "6111799373434197692", "primary"), rbtn(BTN_BROADCAST, "5337302974806922068", "success")],
            [rbtn(BTN_UNLOCK if bot_locked else BTN_LOCK, "6311965254817423602", "danger"),
             rbtn(BTN_RUNALL, "5352597830089347330", "success")],
            [rbtn(BTN_PENDING, "6233136657722251031", "primary"), rbtn(BTN_ADMIN, "6183661284467152997", "primary")],
            [rbtn(BTN_CONTACT, "5337302974806922068", "primary")],
        ]
    else:
        rows.append([rbtn(BTN_CONTACT, "5337302974806922068", "primary")])
    return rows


def sys_stats_text():
    try:
        cpu = psutil.cpu_percent(interval=0.4)
        ram = psutil.virtual_memory()
        disk = shutil.disk_usage(BASE_DIR)
        return (f"🖥 CPU: {cpu:.0f}%\n🧠 RAM: {ram.percent:.0f}% ({fmt_size(ram.used)} / {fmt_size(ram.total)})\n"
                f"💽 Disk: {disk.used * 100 // max(disk.total, 1)}% ({fmt_size(disk.free)} free)\n"
                f"⏱ Uptime: {fmt_td(time.time() - START_TIME)}\n🐍 Python {platform.python_version()}")
    except Exception as e:
        return f"(stats unavailable: {esc(e)})"


def files_list_view(owner):
    files = sorted(user_files.get(owner, []))
    pend = [r for r in list(pending.values()) if r['user_id'] == owner]
    if not files and not pend:
        return f"{PEM['file']} No files yet. Use <b>{BTN_UPLOAD}</b> to add one.", []
    text = f"{PEM['file']} <b>Files ({len(files)})</b> - tap one to manage it:"
    if pend:
        text += "\n\n" + PEM['wait'] + " <b>Waiting for approval:</b>\n" + \
                "\n".join(f"• <code>{esc(r['file_name'])}</code>" for r in pend)
    rows = []
    for fn, ft in files:
        icon = '🟢' if is_running(owner, fn) else '🔴'
        rows.append([ibtn(f"{icon} {fn} ({ft})"[:60], f"fm:open:{owner}:{fid_of(fn)}")])
    return text, rows


def panel_view(owner, fn):
    ft = file_ft(owner, fn) or '?'
    with SCRIPTS_LOCK:
        info = bot_scripts.get((owner, fn))
    running = bool(info and info['process'].poll() is None)
    lines = [f"📄 <b>{esc(fn)}</b>", f"Type: {ft.upper()}",
             f"Status: {'🟢 Running' if running else '🔴 Stopped'}"]
    if running:
        lines.append(f"⏱ Uptime: {fmt_td(time.time() - info['start_time'])}")
        lines.append(f"🧠 RAM: {proc_mem_mb(info['process']):.0f} MB · PID {info['process'].pid}")
    fid = fid_of(fn)
    first = (ibtn("⏹ Stop", f"fm:stop:{owner}:{fid}", "6311965254817423602", "danger") if running
             else ibtn("▶️ Start", f"fm:start:{owner}:{fid}", "6111726981760423576", "success"))
    rows = [
        [first, ibtn("🔄 Restart", f"fm:restart:{owner}:{fid}")],
        [ibtn("📜 Logs", f"fm:logs:{owner}:{fid}"), ibtn("📥 Log file", f"fm:dl:{owner}:{fid}")],
        [ibtn("📄 Get file", f"fm:get:{owner}:{fid}"), ibtn("🗑 Delete", f"fm:del:{owner}:{fid}")],
        [ibtn("🔙 Back", f"fm:list:{owner}")],
    ]
    return "\n".join(lines), rows


def show_panel(chat, mid, owner, fn):
    t, rows = panel_view(owner, fn)
    edit_text(chat, mid, t, rows)


def report_start(chat, fn, state, payload):
    if state == 'live':
        send(chat, f"{PEM['ok']} <b>{esc(fn)}</b> is live {PEM['rocket']} (PID <code>{payload['process'].pid}</code>)")
    elif state == 'finished':
        send(chat, f"{PEM['ok']} <b>{esc(fn)}</b>\n{payload}")
    else:
        send(chat, f"{PEM['no']} <b>{esc(fn)}</b> could not start.\n{payload}")


# =============================================================================
# 10. BOT OBJECT + HANDLERS
# =============================================================================
bot = telebot.TeleBot(TOKEN, parse_mode=None, threaded=True, num_threads=24)

ALL_TYPES = ['text', 'document', 'photo', 'video', 'audio', 'voice', 'sticker', 'animation',
             'video_note', 'contact', 'location']
ADMIN_CMDS = {'listadmins', 'addadmin', 'removeadmin', 'addsub', 'removesub', 'checksub', 'ban', 'unban',
              'banlist', 'approve', 'reject', 'files', 'pending', 'running', 'stopall'}


def touch_user(u):
    if u.id not in active_users:
        add_active_user(u.id)
        if u.id != OWNER_ID:
            send(OWNER_ID, f"🎉 <b>New user</b>\n👤 {esc(u.first_name)}\n✳️ @{esc(u.username or 'N/A')}\n"
                           f"🆔 <code>{u.id}</code>")


def send_welcome(chat_id, u):
    uid = u.id
    if bot_locked and uid not in admin_ids:
        send(chat_id, f"{PEM['warn']} The bot is locked by the admin.", kb=get_main_kb(uid))
        return
    limit = get_user_file_limit(uid)
    exp_info = ""
    if uid == OWNER_ID:
        st = f"{PEM['crown']} Owner"
    elif uid in admin_ids:
        st = f"{PEM['admin']} Admin"
    elif uid in user_subscriptions:
        e = user_subscriptions[uid]['expiry']
        if e > datetime.now():
            st = f"{PEM['check']} Premium"
            exp_info = f"\n{PEM['wait']} Expires in {(e - datetime.now()).days} days"
        else:
            st = f"{PEM['user']} Free (expired)"
            remove_subscription_db(uid)
    else:
        st = f"{PEM['user']} Free user"
    send(chat_id,
         f"{PEM['hi']} <b>Welcome, {esc(u.first_name)}!</b>\n\n"
         f"{PEM['user']} ID: <code>{uid}</code>\n✳️ Username: @{esc(u.username or 'Not set')}\n"
         f"🔰 Status: {st}{exp_info}\n{PEM['file']} Files: {get_user_file_count(uid)} / {lim_str(limit)}\n\n"
         f"{PEM['gear']} Host and run Python (<code>.py</code>), JS (<code>.js</code>) or <code>.zip</code> projects.\n"
         f"{PEM['warn']} Every file needs <b>admin approval</b> before it runs.\n\n"
         f"{PEM['pin']} Use the buttons below.", kb=get_main_kb(uid))


def running_overview():
    rows = []
    lines = [f"🟢 <b>Running scripts</b>"]
    n = 0
    for (o, fn), info in list(bot_scripts.items()):
        if info['process'].poll() is None:
            n += 1
            lines.append(f"• <code>{esc(fn)}</code> - user <code>{o}</code> · "
                         f"{fmt_td(time.time() - info['start_time'])} · {proc_mem_mb(info['process']):.0f} MB")
            rows.append([ibtn(f"⚙️ {fn}"[:50], f"fm:open:{o}:{fid_of(fn)}")])
    if n == 0:
        return "🔴 No scripts are running.", []
    return "\n".join(lines[:1] + [f"Total: {n}"] + lines[1:]), rows[:40]


def run_all_scripts_bg(chat_id):
    started = failed = already = 0
    users = set()
    for uid, files in list(user_files.items()):
        for fn, _ft in list(files):
            if is_running(uid, fn):
                already += 1
                continue
            users.add(uid)
            state, _p = start_script_full(uid, fn, notify=None)
            if state == 'live':
                started += 1
            else:
                failed += 1
            time.sleep(0.4)
    send(chat_id, f"{PEM['ok']} <b>Run-all finished</b>\n🟢 Started: {started}\n⏭ Already running: {already}\n"
                  f"🔴 Failed: {failed}\n👥 Users: {len(users)}")


def do_broadcast(m):
    chat = m.chat.id
    users = [u for u in list(active_users) if u not in banned]
    sent = failed = blocked = 0
    for i, u in enumerate(users):
        res = _call('copyMessage', {'chat_id': u, 'from_chat_id': chat, 'message_id': m.message_id}, timeout=30)
        if res.get('ok'):
            sent += 1
        elif res.get('error_code') in (400, 403):
            blocked += 1
        else:
            failed += 1
        time.sleep(0.05)
    send(chat, f"{PEM['ok']} <b>Broadcast complete</b>\n\n✅ Sent: {sent}\n🚫 Blocked/unreachable: {blocked}\n"
               f"❌ Failed: {failed}\n👥 Total: {len(users)}", kb=get_main_kb(m.from_user.id))


def handle_btn(m, t):
    global bot_locked
    uid = m.from_user.id
    chat = m.chat.id
    kb = get_main_kb(uid)
    is_admin = uid in admin_ids

    if t == BTN_UPDATES:
        send(chat, f"{PEM['msg']} <b>Updates Channel:</b>\n{esc(UPDATE_CHANNEL)}", kb=kb)
    elif t == BTN_UPLOAD:
        limit = get_user_file_limit(uid)
        if get_user_file_count(uid) >= limit:
            send(chat, f"{PEM['warn']} File limit reached ({get_user_file_count(uid)}/{lim_str(limit)}).", kb=kb)
            return
        extra = "" if not (AUTO_APPROVE_ADMINS and is_admin) else "\n🚀 Admin uploads start instantly."
        send(chat, f"{PEM['file']} Send a <b>.py</b>, <b>.js</b> or <b>.zip</b> file now.\n"
                   f"{PEM['warn']} Admin approval is required.{extra}", kb=kb)
    elif t == BTN_FILES:
        text, rows = files_list_view(uid)
        send(chat, text, kb=kb) if not rows else send(chat, text, inline=rows)
    elif t == BTN_SPEED:
        t0 = time.time()
        _call('sendChatAction', {'chat_id': chat, 'action': 'typing'}, timeout=15)
        rt = round((time.time() - t0) * 1000, 1)
        if uid == OWNER_ID:
            lvl = f"{PEM['crown']} Owner"
        elif is_admin:
            lvl = f"{PEM['admin']} Admin"
        elif get_user_file_limit(uid) == SUBSCRIBED_USER_LIMIT:
            lvl = f"{PEM['check']} Premium"
        else:
            lvl = f"{PEM['user']} Free"
        send(chat, f"{PEM['rocket']} <b>Speed:</b> {rt} ms\n🚦 {'🔒 Locked' if bot_locked else '🔓 Unlocked'}\n"
                   f"👤 {lvl}\n\n{sys_stats_text()}", kb=kb)
    elif t == BTN_CONTACT:
        send(chat, f"{PEM['msg']} <b>Contact Owner:</b>\nhttps://t.me/{esc(YOUR_USERNAME.replace('@', ''))}", kb=kb)
    elif t == BTN_STATS:
        total_files = sum(len(f) for f in user_files.values())
        running = mine = 0
        for (o, _fn), info in list(bot_scripts.items()):
            if info['process'].poll() is None:
                running += 1
                if o == uid:
                    mine += 1
        msg = (f"{PEM['admin']} <b>Statistics</b>\n\n{PEM['user']} Users: {len(active_users)}\n"
               f"{PEM['file']} Files: {total_files}\n🟢 Running: {running}\n"
               f"{PEM['wait']} Pending approval: {len(pending)}\n🤖 Your running bots: {mine}")
        if is_admin:
            msg += f"\n🔒 Bot: {'Locked' if bot_locked else 'Unlocked'}\n🚫 Banned: {len(banned)}\n\n{sys_stats_text()}"
        send(chat, msg, kb=kb)
    elif t == BTN_BACK:
        send(chat, f"{PEM['pin']} Main menu.", kb=kb)
    elif not is_admin:
        send(chat, f"{PEM['no']} Admin only.", kb=kb)
    elif t == BTN_SUBS:
        send(chat, f"{PEM['money']} <b>Subscription Management</b>\n\n<code>addsub ID DAYS</code>\n"
                   f"<code>removesub ID</code>\n<code>checksub ID</code>", kb=kb)
    elif t == BTN_BROADCAST:
        states[uid] = 'broadcast'
        send(chat, f"{PEM['msg']} Send the message to broadcast (text, photo, video, file ...).\n"
                   f"Send /cancel to abort.", kb=kb)
    elif t in (BTN_LOCK, BTN_UNLOCK):
        bot_locked = not bot_locked
        set_setting('bot_locked', '1' if bot_locked else '0')
        send(chat, "🔒 Bot locked." if bot_locked else "🔓 Bot unlocked.", kb=get_main_kb(uid))
    elif t in (BTN_RUNALL, "🟢 Run All"):
        send(chat, f"{PEM['wait']} Starting all scripts ...", kb=kb)
        threading.Thread(target=run_all_scripts_bg, args=(chat,), daemon=True).start()
    elif t == BTN_PENDING:
        send_pending_to(chat)
    elif t == BTN_ADMIN:
        send(chat, f"{PEM['crown']} <b>Admin Panel</b>\n\n"
                   f"<code>addadmin ID</code> · <code>removeadmin ID</code> · <code>listadmins</code>\n"
                   f"<code>addsub ID DAYS</code> · <code>removesub ID</code> · <code>checksub ID</code>\n"
                   f"<code>ban ID [reason]</code> · <code>unban ID</code> · <code>banlist</code>\n"
                   f"<code>files ID</code> - open a user's files\n"
                   f"<code>approve AID</code> · <code>reject AID</code>",
             inline=[[ibtn("⏳ Pending", "ad:pending"), ibtn("🟢 Running", "ad:running")],
                     [ibtn("🖥 Server", "ad:server"), ibtn("🛑 Stop ALL", "ad:stopall")]])


def send_pending_to(chat):
    with PENDING_LOCK:
        recs = sorted(pending.values(), key=lambda r: r['created'])
    if not recs:
        send(chat, f"{PEM['ok']} No files are waiting for approval.", kb=get_main_kb(chat))
        return
    send(chat, f"{PEM['wait']} <b>{len(recs)} file(s) waiting</b> - sending them to you now ...")
    for rec in recs:
        notify_admins_new(rec, only_admin=chat)


def admin_cmd(m, text):
    uid = m.from_user.id
    chat = m.chat.id
    kb = get_main_kb(uid)
    parts = text.split()
    cmd = parts[0].lower()
    args = parts[1:]

    def need_int(i):
        try:
            return int(args[i])
        except Exception:
            return None

    if cmd == 'listadmins':
        lst = "\n".join(f"• <code>{a}</code>{' (Owner)' if a == OWNER_ID else ''}" for a in sorted(admin_ids))
        send(chat, f"{PEM['crown']} <b>Admins:</b>\n\n{lst}", kb=kb)
    elif cmd == 'addadmin':
        if uid != OWNER_ID:
            send(chat, f"{PEM['no']} Owner only.", kb=kb)
            return
        nid = need_int(0)
        if nid is None:
            send(chat, f"{PEM['no']} Usage: <code>addadmin ID</code>", kb=kb)
        elif nid in admin_ids:
            send(chat, f"{PEM['warn']} Already an admin.", kb=kb)
        else:
            add_admin_db(nid)
            send(chat, f"{PEM['ok']} Added admin <code>{nid}</code>. They will now receive approval requests.", kb=kb)
            send(nid, f"{PEM['crown']} You are now an <b>Admin</b>! Send /start to refresh your menu.")
            if pending:
                send(nid, f"{PEM['wait']} {len(pending)} file(s) are waiting. Tap <b>{BTN_PENDING}</b> to review.",
                     kb=get_main_kb(nid))
    elif cmd == 'removeadmin':
        if uid != OWNER_ID:
            send(chat, f"{PEM['no']} Owner only.", kb=kb)
            return
        rid = need_int(0)
        if rid is None:
            send(chat, f"{PEM['no']} Usage: <code>removeadmin ID</code>", kb=kb)
        elif remove_admin_db(rid):
            send(chat, f"{PEM['ok']} Removed admin <code>{rid}</code>.", kb=kb)
            send(rid, f"{PEM['warn']} You are no longer an admin.", kb=get_main_kb(rid))
        else:
            send(chat, f"{PEM['warn']} That user is not an admin (the owner cannot be removed).", kb=kb)
    elif cmd == 'addsub':
        sid, days = need_int(0), need_int(1)
        if not sid or not days or sid <= 0 or days <= 0:
            send(chat, f"{PEM['no']} Usage: <code>addsub ID DAYS</code>", kb=kb)
            return
        cur = user_subscriptions.get(sid, {}).get('expiry')
        start = cur if cur and cur > datetime.now() else datetime.now()
        ne = start + timedelta(days=days)
        save_subscription(sid, ne)
        send(chat, f"{PEM['ok']} Subscription added. Expires: {ne:%Y-%m-%d}", kb=kb)
        send(sid, f"{PEM['gift']} <b>Subscription active!</b> Expires: {ne:%Y-%m-%d}\n"
                  f"Your file limit is now {SUBSCRIBED_USER_LIMIT}.")
    elif cmd == 'removesub':
        sid = need_int(0)
        if sid is None:
            send(chat, f"{PEM['no']} Usage: <code>removesub ID</code>", kb=kb)
            return
        remove_subscription_db(sid)
        send(chat, f"{PEM['ok']} Subscription removed.", kb=kb)
        send(sid, f"{PEM['no']} Your subscription was removed.")
    elif cmd == 'checksub':
        sid = need_int(0)
        if sid is None:
            send(chat, f"{PEM['no']} Usage: <code>checksub ID</code>", kb=kb)
            return
        e = user_subscriptions.get(sid, {}).get('expiry')
        if not e:
            send(chat, f"{PEM['user']} <code>{sid}</code> has no subscription.", kb=kb)
        elif e > datetime.now():
            send(chat, f"{PEM['ok']} <code>{sid}</code> active until {e:%Y-%m-%d} ({(e - datetime.now()).days} days).", kb=kb)
        else:
            remove_subscription_db(sid)
            send(chat, f"{PEM['warn']} <code>{sid}</code> expired.", kb=kb)
    elif cmd == 'ban':
        bid = need_int(0)
        if bid is None:
            send(chat, f"{PEM['no']} Usage: <code>ban ID [reason]</code>", kb=kb)
        elif bid in admin_ids:
            send(chat, f"{PEM['no']} You cannot ban an admin.", kb=kb)
        else:
            ban_user(bid, " ".join(args[1:]))
            for (o, fn) in [k for k in list(bot_scripts.keys()) if k[0] == bid]:
                stop_script(o, fn)
            send(chat, f"{PEM['ok']} Banned <code>{bid}</code> (their scripts were stopped).", kb=kb)
            send(bid, f"{PEM['no']} You have been banned from this bot.")
    elif cmd == 'unban':
        bid = need_int(0)
        if bid is None:
            send(chat, f"{PEM['no']} Usage: <code>unban ID</code>", kb=kb)
        elif unban_user(bid):
            send(chat, f"{PEM['ok']} Unbanned <code>{bid}</code>.", kb=kb)
            send(bid, f"{PEM['ok']} You have been unbanned.", kb=get_main_kb(bid))
        else:
            send(chat, f"{PEM['warn']} That user is not banned.", kb=kb)
    elif cmd == 'banlist':
        rows = db_read('SELECT user_id, reason FROM banned')
        send(chat, "🚫 <b>Banned users</b>\n\n" + ("\n".join(f"• <code>{u}</code> {esc(r or '')}" for u, r in rows)
                                                   or "(none)"), kb=kb)
    elif cmd in ('approve', 'reject'):
        if not args:
            send(chat, f"{PEM['no']} Usage: <code>{cmd} ID</code>", kb=kb)
            return
        ok, msg = decide(args[0], 'ok' if cmd == 'approve' else 'no', m.from_user)
        send(chat, f"{PEM['ok'] if ok else PEM['warn']} {esc(msg)}", kb=kb)
    elif cmd == 'files':
        oid = need_int(0)
        if oid is None:
            send(chat, f"{PEM['no']} Usage: <code>files USER_ID</code>", kb=kb)
            return
        text, rows = files_list_view(oid)
        send(chat, f"👤 <code>{oid}</code>\n{text}", inline=rows or None, kb=None if rows else kb)
    elif cmd == 'pending':
        send_pending_to(chat)
    elif cmd == 'running':
        t, rows = running_overview()
        send(chat, t, inline=rows or None, kb=None if rows else kb)
    elif cmd == 'stopall':
        n = 0
        for (o, fn) in list(bot_scripts.keys()):
            if stop_script(o, fn):
                n += 1
        send(chat, f"{PEM['ok']} Stopped {n} script(s).", kb=kb)


def route(m):
    if m.chat.type != 'private' or m.from_user is None or m.from_user.is_bot:
        return
    u = m.from_user
    uid = u.id
    touch_user(u)
    kb = get_main_kb(uid)
    if uid in banned and uid not in admin_ids:
        send(m.chat.id, f"{PEM['no']} You are banned from using this bot.")
        return
    text = (m.text or '').strip()

    st = states.get(uid)
    if st == 'broadcast':
        states.pop(uid, None)
        if uid not in admin_ids:
            return
        if text.lower() == '/cancel':
            send(m.chat.id, "Cancelled.", kb=kb)
        else:
            send(m.chat.id, f"{PEM['wait']} Broadcasting to {len(active_users)} users ...", kb=kb)
            threading.Thread(target=do_broadcast, args=(m,), daemon=True).start()
        return

    if m.content_type == 'document':
        threading.Thread(target=process_upload, args=(m,), daemon=True).start()
        return
    if not text:
        return

    if text.startswith('/'):
        cmd = text.split()[0].split('@')[0].lower()
        if cmd in ('/start', '/help'):
            send_welcome(m.chat.id, u)
        elif cmd == '/admin':
            if uid in admin_ids:
                handle_btn(m, BTN_ADMIN)
            else:
                send(m.chat.id, f"{PEM['no']} Not an admin.", kb=kb)
        elif cmd == '/pending' and uid in admin_ids:
            send_pending_to(m.chat.id)
        elif cmd == '/cancel':
            send(m.chat.id, "Nothing to cancel.", kb=kb)
        else:
            send(m.chat.id, f"{PEM['pin']} Use the buttons below.", kb=kb)
        return

    if bot_locked and uid not in admin_ids:
        send(m.chat.id, f"{PEM['warn']} The bot is locked by the admin.", kb=kb)
        return

    if text in ALL_BUTTONS:
        handle_btn(m, text)
        return

    first = text.split()[0].lower()
    if first in ADMIN_CMDS:
        if uid not in admin_ids:
            send(m.chat.id, f"{PEM['no']} Admin only.", kb=kb)
        else:
            admin_cmd(m, text)
        return
    # unknown text (also refreshes an old/stale reply keyboard)
    send(m.chat.id, f"{PEM['pin']} Use the buttons below.", kb=kb)


@bot.message_handler(content_types=ALL_TYPES)
def on_message(m):
    try:
        route(m)
    except Exception:
        logger.exception("route")
        try:
            send(m.chat.id, f"{PEM['warn']} Something went wrong. Please try again.")
        except Exception:
            pass


# ---- inline callbacks ------------------------------------------------------------
def cb_approval(c):
    p = c.data.split(':')
    act = p[1]
    aid = (p[2] if len(p) > 2 else '').upper()
    admin = c.from_user
    chat, mid = c.message.chat.id, c.message.message_id
    if admin.id not in admin_ids:
        answer_cb(c.id, "Admins only.", True)
        return
    if act in ('scan', 'view'):
        rec = pending.get(aid)
        if not rec:
            answer_cb(c.id, "Already handled.", True)
            return
        answer_cb(c.id)
        if act == 'scan':
            send(chat, format_scan_report(rec))
        else:
            try:
                if rec['is_zip']:
                    with zipfile.ZipFile(rec['qpath']) as z:
                        names = [i.filename for i in z.infolist() if not i.filename.endswith('/')]
                    body = "\n".join(names[:80]) + (f"\n... +{len(names) - 80} more" if len(names) > 80 else "")
                    send(chat, f"📦 <b>ZIP contents ({len(names)} files)</b>\n<pre>{esc(body)[:3500]}</pre>")
                else:
                    with open(rec['qpath'], 'r', encoding='utf-8', errors='ignore') as f:
                        code = f.read(3000)
                    more = "\n... (open the attached file for the rest)" if os.path.getsize(rec['qpath']) > 3000 else ""
                    send(chat, f"👁 <b>{esc(rec['file_name'])}</b>\n<pre>{esc(code)}</pre>{more}")
            except Exception as e:
                send(chat, f"{PEM['no']} Cannot show code: {esc(e)}")
        return
    ok, msg = decide(aid, act, admin)
    if ok:
        answer_cb(c.id, "✅ Approved - starting ..." if act == 'ok' else "❌ Rejected.")
    else:
        answer_cb(c.id, msg, True)
        # make sure this admin's stale card loses its buttons too
        h = HANDLED.get(aid)
        stale = f"{'✅ Approved' if h and h['act'] == 'ok' else '❌ Rejected'} by {h['by']}" if h else "Already handled"
        edit_caption(chat, mid, f"{PEM['file']} {stale}  (ID <code>{esc(aid)}</code>)", inline=None)


def cb_files(c):
    p = c.data.split(':')
    act = p[1]
    owner = int(p[2])
    fid = p[3] if len(p) > 3 else ''
    viewer = c.from_user.id
    chat, mid = c.message.chat.id, c.message.message_id
    if not (viewer == owner or viewer in admin_ids):
        answer_cb(c.id, "This is not your file.", True)
        return
    if viewer in banned and viewer not in admin_ids:
        answer_cb(c.id, "You are banned.", True)
        return
    if bot_locked and viewer not in admin_ids:
        answer_cb(c.id, "The bot is locked by the admin.", True)
        return
    if act == 'list':
        answer_cb(c.id)
        t, rows = files_list_view(owner)
        edit_text(chat, mid, t, rows or None)
        return
    fn = find_file(owner, fid)
    if not fn:
        answer_cb(c.id, "That file no longer exists.", True)
        t, rows = files_list_view(owner)
        edit_text(chat, mid, t, rows or None)
        return

    if act == 'open':
        answer_cb(c.id)
        show_panel(chat, mid, owner, fn)
    elif act == 'start':
        if is_running(owner, fn):
            answer_cb(c.id, "Already running.")
            show_panel(chat, mid, owner, fn)
            return
        answer_cb(c.id, "Starting ...")

        def job():
            edit_text(chat, mid, f"{PEM['wait']} Starting <b>{esc(fn)}</b> ...")
            state, payload = start_script_full(owner, fn, notify=chat)
            show_panel(chat, mid, owner, fn)
            if state != 'live':
                report_start(chat, fn, state, payload)
        threading.Thread(target=job, daemon=True).start()
    elif act == 'stop':
        stop_script(owner, fn)
        answer_cb(c.id, "Stopped.")
        show_panel(chat, mid, owner, fn)
    elif act == 'restart':
        answer_cb(c.id, "Restarting ...")

        def job2():
            edit_text(chat, mid, f"{PEM['wait']} Restarting <b>{esc(fn)}</b> ...")
            stop_script(owner, fn, mark=False)
            state, payload = start_script_full(owner, fn, notify=chat)
            show_panel(chat, mid, owner, fn)
            if state != 'live':
                report_start(chat, fn, state, payload)
        threading.Thread(target=job2, daemon=True).start()
    elif act == 'logs':
        answer_cb(c.id)
        tail = read_log_tail(log_path(owner, fn), 2500)
        while len(esc(tail)) > 3300:
            tail = tail[len(tail) // 5:]
        body = esc(tail) if tail.strip() else "(no output yet)"
        edit_text(chat, mid, f"📜 <b>Logs - {esc(fn)}</b>\n<pre>{body}</pre>",
                  [[ibtn("🔄 Refresh", f"fm:logs:{owner}:{fid}"), ibtn("🔙 Back", f"fm:open:{owner}:{fid}")]])
    elif act == 'dl':
        lp = log_path(owner, fn)
        if os.path.isfile(lp) and os.path.getsize(lp) > 0:
            answer_cb(c.id, "Sending ...")
            send_doc(chat, path=lp, caption=f"📥 Log: {esc(fn)}"[:200])
        else:
            answer_cb(c.id, "No log yet.", True)
    elif act == 'get':
        answer_cb(c.id, "Sending ...")
        send_doc(chat, path=script_path(owner, fn), caption=f"📄 {esc(fn)}"[:200])
    elif act == 'del':
        answer_cb(c.id)
        edit_text(chat, mid, f"{PEM['warn']} Delete <b>{esc(fn)}</b> permanently?\nThe script will be stopped.",
                  [[ibtn("🗑 Yes, delete", f"fm:delok:{owner}:{fid}", style="danger"),
                    ibtn("↩️ Cancel", f"fm:open:{owner}:{fid}")]])
    elif act == 'delok':
        delete_file(owner, fn)
        answer_cb(c.id, "Deleted.")
        t, rows = files_list_view(owner)
        edit_text(chat, mid, f"{PEM['ok']} Deleted <code>{esc(fn)}</code>\n\n{t}", rows or None)
    else:
        answer_cb(c.id)


def cb_admin(c):
    act = c.data.split(':')[1]
    chat, mid = c.message.chat.id, c.message.message_id
    if c.from_user.id not in admin_ids:
        answer_cb(c.id, "Admins only.", True)
        return
    answer_cb(c.id)
    if act == 'pending':
        send_pending_to(chat)
    elif act == 'running':
        t, rows = running_overview()
        send(chat, t, inline=rows or None)
    elif act == 'server':
        send(chat, f"🖥 <b>Server</b>\n\n{sys_stats_text()}\n🟢 Running scripts: "
                   f"{sum(1 for i in list(bot_scripts.values()) if i['process'].poll() is None)}")
    elif act == 'stopall':
        edit_text(chat, mid, f"{PEM['warn']} Stop <b>ALL</b> hosted scripts?",
                  [[ibtn("🛑 Yes, stop all", "ad:stopallok", style="danger"), ibtn("↩️ Cancel", "ad:cancel")]])
    elif act == 'stopallok':
        n = sum(1 for k in list(bot_scripts.keys()) if stop_script(k[0], k[1]))
        edit_text(chat, mid, f"{PEM['ok']} Stopped {n} script(s).")
    elif act == 'cancel':
        edit_text(chat, mid, "Cancelled.")


@bot.callback_query_handler(func=lambda c: True)
def on_callback(c):
    try:
        data = c.data or ''
        if data.startswith('ap:'):
            cb_approval(c)
        elif data.startswith('fm:'):
            cb_files(c)
        elif data.startswith('ad:'):
            cb_admin(c)
        else:
            answer_cb(c.id)
    except Exception:
        logger.exception("callback")
        answer_cb(c.id, "Something went wrong.", True)


# =============================================================================
# 11. KEEP-ALIVE WEB SERVER + MAIN
# =============================================================================
app = Flask(__name__)


@app.route('/')
def home():
    return "Marco File Host is running."


@app.route('/health')
def health():
    return {"status": "ok", "uptime": int(time.time() - START_TIME),
            "running": sum(1 for i in list(bot_scripts.values()) if i['process'].poll() is None),
            "pending": len(pending)}


def run_flask():
    try:
        app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), threaded=True, use_reloader=False)
    except Exception as e:
        logger.error("Flask not started: %s", e)


def keep_alive():
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("Keep-alive web server started.")


def _on_term(*_a):
    cleanup()
    os._exit(0)


if __name__ == '__main__':
    init_db()
    load_data()
    logger.info("=" * 44)
    logger.info("Bot starting | owner=%s | admins=%s | pending=%d", OWNER_ID, sorted(admin_ids), len(pending))
    logger.info("=" * 44)
    try:
        signal.signal(signal.SIGTERM, _on_term)
    except Exception:
        pass
    keep_alive()
    try:
        bot.remove_webhook()
    except Exception:
        pass
    threading.Thread(target=watchdog_loop, daemon=True).start()
    threading.Thread(target=maintenance_loop, daemon=True).start()
    threading.Thread(target=autostart_all, daemon=True).start()
    logger.info("Polling started ...")
    while not SHUTDOWN.is_set():
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=30,
                                 allowed_updates=['message', 'callback_query'],
                                 logger_level=logging.WARNING)
        except requests.exceptions.ReadTimeout:
            logger.warning("ReadTimeout - retrying in 5s")
            time.sleep(5)
        except requests.exceptions.ConnectionError as ce:
            logger.error("Connection error: %s - retrying in 15s", ce)
            time.sleep(15)
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.critical("Polling error: %s", e, exc_info=True)
            time.sleep(10)
        time.sleep(1)

import os
import time
import requests
import threading
import tempfile
import json
import re
import secrets
import psycopg2
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@nabihere")
BOT_USERNAME = os.getenv("BOT_USERNAME", "NabiStarAppBot")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
BASE_URL = f"https://api.telegram.org/bot{TOKEN}"
DATABASE_URL = os.getenv("DATABASE_URL_POOLED") or os.getenv("DATABASE_URL", "")

ADSGRAM_UNIT_ID = os.getenv("ADSGRAM_UNIT_ID", "bot-58438")
ADSGRAM_BLOCK_ID = ADSGRAM_UNIT_ID.replace("bot-", "").strip()
ADSGRAM_TOKEN = os.getenv("ADSGRAM_TOKEN", "")
ADSGRAM_REWARD_SECRET = os.getenv("ADSGRAM_REWARD_SECRET", "nabistar_gizli_anahtar_123")
AD_REWARD_POINTS = int(os.getenv("AD_REWARD_POINTS", "1"))

FIXED_CHANNELS = [
    {"title": "Kanal 1", "check_id": "-1003954612863", "invite_link": "https://t.me/+aASAtJxGnho2ZTQ8"},
    {"title": "Kanal 2", "check_id": "-1004356348392", "invite_link": "https://t.me/+RSiMlXnCkPE4MDE0"},
]

# --- EMOJI ID'LERİ (Eski + Yeni Premium) ---
EMOJI = {
    # Eski ID'ler
    "flag_aze": "5911197578640233518",
    "flag_tr": "5910995113881901195",
    "flag_en": "5913463998522592692",
    "flag_ru": "5913274246867456342",
    "star_15": "5438496463044752972", 
    "ref": "6048521298139683235",
    "shop": "6048693908580344264",
    "profile": "6048466764939926219",
    "task": "6276192588173154244",
    "gift": "6048521298139683235",
    "pubg_uc": "5224542568410268366",
    "channel_lock": "6048709688290189104",
    "check": "6048521298139683235",
    "watch_ad": "5357362507893716538",
    "earn_points": "5233625831535830232",

    # Senin gönderdiğin YENİ Premium ID'ler
    "new_1": "6276190011192775282", # 🎯 Hedef / Görev
    "new_2": "5769482310915199790", # 💰 Para / Bakiye
    "new_3": "5397782960512444700", # 👤 Profil
    "new_4": "5208541126583136130", # ✅ Onay / Tik
    "new_5": "5420323339723881652", # 🎉 Parti / Başarı
    "new_6": "5996720655360332940", # ✨ Işıltı
    "new_7": "6122885942450590110", # 📭 Boş Kutu
    "new_8": "6120729709954208730", # 🛒 Mağaza
    "new_9": "6120731625509623335", # 📦 Paket
    "new_10": "5244933196230972438", # 🎮 Oyun / UC
    "new_11": "5422439311196834318", # 👥 İnsanlar / Referans
    "new_12": "5413879192267805083", # 💡 Ampul / Bilgi
    "new_13": "5282843764451195532", # ⭐ Yıldız
}

# --- NEON POSTGRESQL ---
def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL veya DATABASE_URL_POOLED ayarlanmamış.")
    return psycopg2.connect(DATABASE_URL, connect_timeout=15)

def init_db():
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                balance BIGINT DEFAULT 0,
                lang TEXT DEFAULT 'tr',
                referred_by BIGINT,
                last_daily TEXT,
                total_refs BIGINT DEFAULT 0,
                joined_at TEXT,
                ref_used INTEGER DEFAULT 0,
                terms_accepted INTEGER DEFAULT 0,
                ref_rewarded INTEGER DEFAULT 0,
                waiting_for_details INTEGER DEFAULT 0,
                pending_product TEXT,
                pending_cost BIGINT,
                refs_since_order BIGINT DEFAULT 0,
                adsgram_last_reward TEXT
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS orders (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT,
                username TEXT,
                product TEXT,
                cost BIGINT,
                user_details TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS required_channels (
                id BIGSERIAL PRIMARY KEY,
                title TEXT,
                check_id TEXT UNIQUE,
                invite_link TEXT
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS tasks (
                id BIGSERIAL PRIMARY KEY,
                title TEXT,
                reward BIGINT,
                link TEXT
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS user_tasks (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT,
                task_id BIGINT,
                completed_at TEXT,
                UNIQUE(user_id, task_id)
            )""")
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS refs_since_order BIGINT DEFAULT 0")
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS adsgram_last_reward TEXT")
            for ch in FIXED_CHANNELS:
                c.execute(
                    """INSERT INTO required_channels (title, check_id, invite_link)
                       VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                    (ch["title"], ch["check_id"], ch["invite_link"])
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def db_execute(q, p=(), fetchone=False, fetchall=False):
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            query = q.strip()
            ignore_insert = query.upper().startswith("INSERT OR IGNORE INTO")
            if ignore_insert:
                query = re.sub(r"(?i)^INSERT OR IGNORE INTO", "INSERT INTO", query, count=1)
                query = query.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
            query = query.replace("?", "%s")
            c.execute(query, p)
            result = c.fetchone() if fetchone else (c.fetchall() if fetchall else None)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def db_complete_order(uid, username, product, cost, details):
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """UPDATE users
                   SET balance = balance - %s,
                       refs_since_order = 0,
                       waiting_for_details = 0,
                       pending_product = NULL,
                       pending_cost = NULL
                   WHERE user_id = %s AND balance >= %s AND refs_since_order >= 4""",
                (cost, uid, cost)
            )
            if c.rowcount != 1:
                conn.rollback()
                return False
            c.execute(
                """INSERT INTO orders
                   (user_id, username, product, cost, user_details, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (uid, username, product, cost, details, datetime.now().isoformat())
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

init_db()

def get_user_db(uid):
    return db_execute(
        """SELECT balance, lang, last_daily, total_refs, ref_used, terms_accepted,
                  referred_by, ref_rewarded, waiting_for_details, pending_product,
                  pending_cost, refs_since_order
           FROM users WHERE user_id=?""",
        (uid,), fetchone=True
    )

def register_user(uid, username, ref=None):
    ex = db_execute("SELECT user_id FROM users WHERE user_id=?", (uid,), fetchone=True)
    if ex:
        db_execute("UPDATE users SET username=? WHERE user_id=?", (username, uid))
        return False
    valid = False
    if ref and ref != uid:
        if db_execute("SELECT user_id FROM users WHERE user_id=?", (ref,), fetchone=True):
            valid = True
    db_execute(
        """INSERT INTO users (user_id, username, referred_by, joined_at, ref_used, ref_rewarded)
           VALUES (?,?,?,?,?,?)""",
        (uid, username, ref if valid else None, datetime.now().isoformat(), 1 if valid else 0, 0)
    )
    return True

def process_referral_reward(uid):
    u = get_user_db(uid)
    if not u:
        return
    ref_by, rewarded = u[6], u[7]
    if ref_by and rewarded == 0:
        conn = get_db_connection()
        try:
            with conn.cursor() as c:
                c.execute("SELECT user_id FROM users WHERE user_id=%s", (ref_by,))
                if c.fetchone():
                    c.execute(
                        """UPDATE users
                           SET balance = balance + 10,
                               total_refs = total_refs + 1,
                               refs_since_order = refs_since_order + 1
                           WHERE user_id=%s""",
                        (ref_by,)
                    )
                    c.execute("UPDATE users SET ref_rewarded=1 WHERE user_id=%s", (uid,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

# --- ÇEVİRİLER (Emojiler senin verdiğin ID'lerle değiştirildi) ---
TR = {
    "tr": {
        "choose_lang": "🌐 Lütfen dilinizi seçin:",
        "welcome": (
            "👋 <b>Merhaba, hoş geldin!</b>\n\n"
            "Botumuzu kullanmaya başlamadan önce aşağıdaki <b>zorunlu kanallarımıza</b> katılman gerekiyor. "
            "Kanallara katıldıktan sonra <b>«Katıldım, Kontrol Et»</b> butonuna basarak devam edebilirsin.\n\n"
            "<tg-emoji emoji-id=\"5208541126583136130\">📌</tg-emoji> <b>Önemli Not:</b> Özel (private) kanallarımıza katılırken <b>katılma isteği</b> göndermen gerekir. "
            "Botumuz isteğini <b>otomatik olarak onaylayacak</b> ve seni içeri alacaktır. "
            "Eğer istek göndermezsen bot seni ana menüye geçirmez.\n\n"
            "👇 <i>Aşağıdaki kanallara sırayla katıl ve kontrol et:</i>"
        ),
        "check_sub": "Katıldım, Kontrol Et",
        "not_sub": "⚠️ Lütfen önce tüm kanallara katıl!",
        "main_menu": (
            "<tg-emoji emoji-id=\"5996720655360332940\">✨</tg-emoji> <b>Ana Menüye Hoş Geldin!</b>\n\n"
            "<tg-emoji emoji-id=\"5769482310915199790\">💰</tg-emoji> <b>Puan nasıl kazanılır?</b>\n"
            "1️⃣ <b>Referans Kas:</b> Arkadaşlarını davet et, her biri için <b>+10 puan</b> kazan.\n"
            "2️⃣ <b>Günlük Ödül:</b> Her 24 saatte bir <b>+5 puan</b> al.\n"
            "3️⃣ <b>Görevler:</b> Admin tarafından eklenen görevleri tamamla, ekstra puan kazan.\n\n"
            "<tg-emoji emoji-id=\"6120729709954208730\">🛒</tg-emoji> Kazandığın puanları <b>Mağaza</b> bölümünde Telegram Yıldızı veya PUBG UC'ye dönüştürebilirsin.\n\n"
            "👇 <i>Bir buton seç:</i>"
        ),
        "btn_profile": "Profilim", "btn_ref": "Referans Kas", "btn_shop": "Mağaza",
        "btn_tasks": "Görevler", "btn_daily": "Günlük Ödül", "btn_lang": "Dil Değiştir", "btn_back": "Geri Dön",
        "profile_title": "<tg-emoji emoji-id=\"5397782960512444700\">👤</tg-emoji> <b>Profil Bilgileri</b>",
        "profile_body": (
            "<tg-emoji emoji-id=\"6120731625509623335\">📦</tg-emoji> Telegram ID: <code>{user_id}</code>\n"
            "<tg-emoji emoji-id=\"5397782960512444700\">👤</tg-emoji> Kullanıcı adı: @{username}\n"
            "<tg-emoji emoji-id=\"5769482310915199790\">💰</tg-emoji> Bakiye: <b>{balance} puan</b>\n"
            "<tg-emoji emoji-id=\"5422439311196834318\">👥</tg-emoji> Toplam davet: <b>{refs}</b>\n"
            "📅 Katılım tarihi: {joined}\n\n"
            "<tg-emoji emoji-id=\"5413879192267805083\">💡</tg-emoji> <i>Daha fazla puan için arkadaşlarını davet et ve günlük ödülünü topla!</i>"
        ),
        "ref_title": "<tg-emoji emoji-id=\"5422439311196834318\">👥</tg-emoji> <b>Referans Sistemi</b>",
        "ref_body": (
            "🚀 <b>Nasıl çalışır?</b>\n"
            "Aşağıdaki davet linkini arkadaşlarınla paylaş. Arkadaşın linke tıklayıp botu başlattığında ve "
            "zorunlu kanallara katıldığında <b>+10 puan</b> kazanırsın.\n\n"
            "🔗 <b>Senin davet linkin:</b>\n<code>{link}</code>\n\n"
            "<tg-emoji emoji-id=\"5422439311196834318\">👥</tg-emoji> Şu ana kadar davet ettiğin kişi sayısı: <b>{refs}</b>\n\n"
            "<tg-emoji emoji-id=\"5413879192267805083\">💡</tg-emoji> <i>Ne kadar çok davet, o kadar çok puan!</i>"
        ),
        "shop_title": (
            "<tg-emoji emoji-id=\"6120729709954208730\">🛒</tg-emoji> <b>Mağaza</b>\n\n"
            "Kazandığın puanları aşağıdaki ürünlerle takas edebilirsin. "
            "Sipariş verebilmek için en az <b>4 aktif referansa</b> sahip olman gerekir.\n\n"
            "👇 <i>Bir ürün seç:</i>"
        ),
        "shop_item_15": "15 Yıldız — 50 Puan", "shop_item_30": "30 Yıldız — 80 Puan",
        "shop_item_50": "50 Yıldız — 110 Puan", "shop_item_60_uc": "60 UC — 60 Puan",
        "shop_item_120_uc": "120 UC — 100 Puan",
        "buy_success": "<tg-emoji emoji-id=\"5420323339723881652\">🎉</tg-emoji> Siparişin başarıyla alındı! Admin ekibimiz en kısa sürede seninle iletişime geçecek.",
        "buy_fail": "❌ Yetersiz bakiye! Daha fazla puan kazanmak için görevleri tamamla.",
        "buy_fail_ref": "❌ Sipariş verebilmek için en az 4 referansa sahip olmalısın!",
        "ask_details": (
            "<tg-emoji emoji-id=\"5244933196230972438\">🎮</tg-emoji> <b>Son adım!</b>\n\n"
            "Lütfen oyun içi <b>ID'ni</b> veya teslimat için gerekli <b>detayları</b> yaz.\n"
            "Örnek: <code>123456789</code>\n\n"
            "📩 <i>Bu bilgi admin ekibimize iletilecek ve siparişin işleme alınacak.</i>"
        ),
        "order_received_admin": "<tg-emoji emoji-id=\"6120729709954208730\">🛒</tg-emoji> <b>Yeni Sipariş!</b>\n\n<tg-emoji emoji-id=\"5397782960512444700\">👤</tg-emoji> @{username}\n🆔 <code>{user_id}</code>\n📦 {product}\n💰 {cost}\n📝 <code>{details}</code>",
        "tasks_title": "<tg-emoji emoji-id=\"6276190011192775282\">🎯</tg-emoji> <b>Görevler & Ödüller</b>",
        "tasks_empty": (
            "<tg-emoji emoji-id=\"6122885942450590110\">📭</tg-emoji> <b>Şu anda aktif görev bulunmuyor.</b>\n\n"
            "Admin ekibimiz yakında yeni görevler ekleyecek. "
            "O zamana kadar referans kasabilir veya günlük ödülünü toplayabilirsin!\n\n"
            "🔔 <i>Yeni görevler eklendiğinde burada göreceksin.</i>"
        ),
        "tasks_list": (
            "📋 <b>Aktif Görevler</b>\n\n"
            "Aşağıdaki görevlerden birini tamamlamak için üzerine tıkla. "
            "Her görevi yalnızca <b>bir kez</b> tamamlayabilirsin.\n\n"
            "👇 <i>Bir görev seç:</i>"
        ),
        "task_completed_already": "<tg-emoji emoji-id=\"5208541126583136130\">✅</tg-emoji> Bu görevi zaten tamamladın!",
        "task_reward": "<tg-emoji emoji-id=\"5420323339723881652\">🎉</tg-emoji> Görev tamamlandı! <b>+{reward} puan</b> kazandın!\n\n<tg-emoji emoji-id=\"5769482310915199790\">💰</tg-emoji> Yeni bakiyen: <b>{balance}</b>",
        "task_not_joined": "⚠️ Önce görevin linkine katılman gerek!",
        "daily_claimed": "<tg-emoji emoji-id=\"5208541126583136130\">✅</tg-emoji> Bugünkü günlük ödülünü zaten aldın! Yarın tekrar gel.",
        "daily_success": "<tg-emoji emoji-id=\"5420323339723881652\">🎉</tg-emoji> Günlük ödül: <b>+5 puan</b>!\n\n<tg-emoji emoji-id=\"5769482310915199790\">💰</tg-emoji> Yeni bakiyen: <b>{balance}</b>",
        "lang_changed": "<tg-emoji emoji-id=\"5208541126583136130\">✅</tg-emoji> Dil başarıyla değiştirildi!",
        "terms_text": (
            "🇹🇷 <b>KULLANIM KOŞULLARI VE GİZLİLİK</b>\n\n"
            "Botumuzu kullanarak aşağıdaki maddeleri kabul etmiş sayılırsınız:\n\n"
            "<tg-emoji emoji-id=\"6276190011192775282\">🎯</tg-emoji> <b>1. Yasal Uyum:</b> Bot Telegram kurallarına tam uyumludur, yasa dışı faaliyet yasaktır.\n"
            "<tg-emoji emoji-id=\"5769482310915199790\">💰</tg-emoji> <b>2. Puan Sistemi:</b> Puanların nakit karşılığı yoktur, yalnızca bot içi ödüller içindir.\n"
            "<tg-emoji emoji-id=\"5422439311196834318\">👥</tg-emoji> <b>3. Referans Şartı:</b> Davet edilenlerin zorunlu kanallara katılması gerekir.\n"
            "<tg-emoji emoji-id=\"6120729709954208730\">🛒</tg-emoji> <b>4. Sipariş Şartı:</b> Mağazadan sipariş için en az 4 aktif referans gerekir.\n\n"
            "👇 <i>Devam etmek için aşağıdaki butona dokun:</i>"
        ),
        "terms_btn": "Kuralları Kabul Ediyorum", "terms_ok": "<tg-emoji emoji-id=\"5208541126583136130\">✅</tg-emoji> Kurallar kabul edildi, hoş geldin!",
        "stats_text": "📊 <b>İstatistikler</b>\n\n<tg-emoji emoji-id=\"5422439311196834318\">👥</tg-emoji> Toplam: <b>{total}</b>\n🔗 Ref: <b>{refs}</b>\n📢 Kanal: <b>{channels}</b>\n📋 Görev: <b>{tasks}</b>",
        "broadcast_prompt": "📢 Duyuru mesajını yaz:",
        "broadcast_success": "<tg-emoji emoji-id=\"5208541126583136130\">✅</tg-emoji> Duyuru {count} kullanıcıya gönderildi!",
        "add_channel_prompt": "📢 Format:\n<code>Başlık | KontrolID | Link</code>\n\nÖrn:\n<code>Kanal | @username | https://t.me/...</code>\n<code>Private | -100123... | https://t.me/+...</code>",
        "add_task_prompt": "📋 Format:\n<code>Başlık | Ödül | Link</code>\n\nÖrn:\n<code>Kanala katıl | 15 | https://t.me/+...</code>",
        "channel_added": "<tg-emoji emoji-id=\"5208541126583136130\">✅</tg-emoji> Kanal eklendi: {title}", "task_added": "<tg-emoji emoji-id=\"5208541126583136130\">✅</tg-emoji> Görev eklendi: {title} (+{reward})",
        "invalid_format": "❌ Format hatalı! Tekrar dene.",
        "sub_joined": "<tg-emoji emoji-id=\"5208541126583136130\">✅</tg-emoji> Katılım onaylandı!",
    },
}

def T(lang, key, **kw):
    d = TR.get(lang, TR["tr"])
    t = d.get(key, TR["tr"].get(key, ""))
    return t.format(**kw) if kw else t

# --- API ---
def api(method, payload=None):
    try:
        return requests.post(f"{BASE_URL}/{method}", json=payload or {}, timeout=15).json()
    except Exception:
        return {"ok": False}

def send_message(chat_id, text, reply_markup=None, parse_mode="HTML"):
    p = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        p["reply_markup"] = reply_markup
    return api("sendMessage", p)

def edit_message(chat_id, mid, text, reply_markup=None, parse_mode="HTML"):
    p = {"chat_id": chat_id, "message_id": mid, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        p["reply_markup"] = reply_markup
    return api("editMessageText", p)

def answer_callback(cq_id, text="", show_alert=False):
    return api("answerCallbackQuery", {"callback_query_id": cq_id, "text": text, "show_alert": show_alert})

def check_membership(check_id, uid):
    try:
        r = requests.get(f"{BASE_URL}/getChatMember",
                         params={"chat_id": check_id, "user_id": uid}, timeout=10)
        d = r.json()
        if d.get("ok"):
            return d["result"]["status"] in ["member", "administrator", "creator"]
        return False
    except Exception:
        return False

def check_all_subscriptions(uid):
    if not check_membership(CHANNEL_USERNAME, uid):
        return False
    for row in db_execute("SELECT check_id FROM required_channels", fetchall=True) or []:
        if not check_membership(row[0], uid):
            return False
    return True

# --- DATABASE BACKUP ---
def send_db_backup(admin_id):
    backup_path = None
    try:
        conn = get_db_connection()
        export = {}
        try:
            with conn.cursor() as c:
                for table in ("users", "orders", "required_channels", "tasks", "user_tasks"):
                    c.execute(f"SELECT * FROM {table}")
                    columns = [d[0] for d in c.description]
                    export[table] = [
                        dict(zip(columns, row)) for row in c.fetchall()
                    ]
        finally:
            conn.close()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", encoding="utf-8", delete=False
        ) as tmp:
            backup_path = tmp.name
            json.dump(export, tmp, ensure_ascii=False, indent=2, default=str)

        size_mb = os.path.getsize(backup_path) / (1024 * 1024)
        with open(backup_path, "rb") as backup_file:
            response = requests.post(
                f"{BASE_URL}/sendDocument",
                data={"chat_id": admin_id, "caption": f"✅ Neon JSON yedeği hazır. Boyut: {size_mb:.2f} MB"},
                files={"document": ("neon_backup.json", backup_file)},
                timeout=120,
            )
        if not response.ok or not response.json().get("ok"):
            send_message(admin_id, "❌ Neon yedeği Telegram'a gönderilemedi.")
    except Exception as e:
        print("Backup error:", e)
        send_message(admin_id, f"❌ Yedekleme hatası: {e}")
    finally:
        if backup_path and os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except OSError:
                pass

# --- KLAVYELER ---
def lang_keyboard():
    return {"inline_keyboard": [
        [{"text": "Türkçe", "callback_data": "lang_tr", "icon_custom_emoji_id": EMOJI["flag_tr"], "style": "primary"}],
        [{"text": "Azəri", "callback_data": "lang_aze", "icon_custom_emoji_id": EMOJI["flag_aze"], "style": "primary"}],
        [{"text": "English", "callback_data": "lang_en", "icon_custom_emoji_id": EMOJI["flag_en"], "style": "primary"}],
        [{"text": "Русский", "callback_data": "lang_ru", "icon_custom_emoji_id": EMOJI["flag_ru"], "style": "primary"}],
    ]}

def terms_keyboard(lang):
    return {"inline_keyboard": [[{"text": T(lang, "terms_btn"), "callback_data": "accept_terms", "style": "success"}]]}

def sub_keyboard(lang):
    rows = []
    rows.append([{
        "text": "Ana Kanal",
        "url": f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}",
        "icon_custom_emoji_id": EMOJI["channel_lock"],
        "style": "primary",
    }])
    for ch in db_execute("SELECT title, invite_link FROM required_channels", fetchall=True) or []:
        rows.append([{
            "text": ch[0] or "Kanal",
            "url": ch[1],
            "icon_custom_emoji_id": EMOJI["channel_lock"],
            "style": "primary",
        }])
    rows.append([{
        "text": T(lang, "check_sub"),
        "callback_data": "check_sub",
        "icon_custom_emoji_id": EMOJI["check"],
        "style": "success",
    }])
    return {"inline_keyboard": rows}

def main_keyboard(lang):
    return {"inline_keyboard": [
        [{"text": T(lang, 'btn_profile'), "callback_data": "menu_profile", "icon_custom_emoji_id": EMOJI["profile"], "style": "primary"}],
        [{"text": T(lang, 'btn_ref'), "callback_data": "menu_ref", "icon_custom_emoji_id": EMOJI["ref"], "style": "success"}],
        [{"text": T(lang, 'btn_shop'), "callback_data": "menu_shop", "icon_custom_emoji_id": EMOJI["shop"], "style": "primary"}],
        [{"text": "Reklam izle", "callback_data": "watch_ad", "icon_custom_emoji_id": EMOJI["watch_ad"], "style": "success"}],
        [{"text": "Puan kazan", "callback_data": "points_info", "icon_custom_emoji_id": EMOJI["earn_points"], "style": "primary"}],
        [{"text": T(lang, 'btn_tasks'), "callback_data": "menu_tasks", "icon_custom_emoji_id": EMOJI["task"], "style": "primary"}],
        [{"text": T(lang, 'btn_daily'), "callback_data": "daily", "icon_custom_emoji_id": EMOJI["gift"], "style": "success"}],
        [{"text": T(lang, 'btn_lang'), "callback_data": "change_lang", "style": "danger"}],
    ]}

def shop_keyboard(lang):
    return {"inline_keyboard": [
        [{"text": T(lang, 'shop_item_15'), "callback_data": "buy_15", "icon_custom_emoji_id": EMOJI["star_15"], "style": "success"}],
        [{"text": T(lang, 'shop_item_30'), "callback_data": "buy_30", "icon_custom_emoji_id": EMOJI["star_15"], "style": "success"}],
        [{"text": T(lang, 'shop_item_50'), "callback_data": "buy_50", "icon_custom_emoji_id": EMOJI["star_15"], "style": "success"}],
        [{"text": T(lang, 'shop_item_60_uc'), "callback_data": "buy_60_uc", "icon_custom_emoji_id": EMOJI["pubg_uc"], "style": "primary"}],
        [{"text": T(lang, 'shop_item_120_uc'), "callback_data": "buy_120_uc", "icon_custom_emoji_id": EMOJI["pubg_uc"], "style": "primary"}],
        [{"text": "⬅️ " + T(lang, 'btn_back'), "callback_data": "back_home", "style": "danger"}],
    ]}

def back_keyboard(lang):
    return {"inline_keyboard": [[{"text": "⬅️ " + T(lang, 'btn_back'), "callback_data": "back_home", "style": "danger"}]]}

def tasks_keyboard(lang, uid):
    rows = []
    tasks = db_execute("SELECT id, title, reward, link FROM tasks", fetchall=True) or []
    done = {r[0] for r in (db_execute("SELECT task_id FROM user_tasks WHERE user_id=?", (uid,), fetchall=True) or [])}
    for t in tasks:
        tid, title, reward, link = t
        mark = "✅ " if tid in done else "🎯 "
        rows.append([{
            "text": f"{mark}{title} (+{reward})",
            "callback_data": f"task_{tid}",
            "icon_custom_emoji_id": EMOJI["task"],
            "style": "success" if tid in done else "primary",
        }])
    rows.append([{"text": "⬅️ " + T(lang, 'btn_back'), "callback_data": "back_home", "style": "danger"}])
    return {"inline_keyboard": rows}

def admin_keyboard():
    return {"inline_keyboard": [
        [{"text": "📢 Duyuru", "callback_data": "admin_broadcast", "style": "primary"}],
        [{"text": "📊 İstatistik", "callback_data": "admin_stats", "style": "success"}],
        [{"text": "➕ Kanal Ekle", "callback_data": "admin_add_channel", "style": "primary"}],
        [{"text": "➕ Görev Ekle", "callback_data": "admin_add_task", "style": "success"}],
        [{"text": "📋 Kanalları Listele", "callback_data": "admin_list_channels", "style": "primary"}],
        [{"text": "📋 Görevleri Listele", "callback_data": "admin_list_tasks", "style": "primary"}],
    ]}

admin_states = {}

# --- ADSGRAM BOT ADS ---
def get_adsgram_ad(uid, lang):
    if not ADSGRAM_TOKEN or not ADSGRAM_BLOCK_ID:
        return None, "AdsGram ayarları eksik."
    language_map = {"tr": "tr", "aze": "en", "en": "en", "ru": "ru"}
    try:
        response = requests.get(
            "https://api.adsgram.ai/advbot",
            params={
                "tgid": uid,
                "blockid": ADSGRAM_BLOCK_ID,
                "language": language_map.get(lang, "en"),
                "token": ADSGRAM_TOKEN,
            },
            timeout=20,
        )
        if response.status_code != 200:
            return None, f"AdsGram API hata kodu: {response.status_code}"
        data = response.json()
        if not data or not data.get("text_html"):
            return None, "Şu anda gösterilecek reklam bulunamadı. Biraz sonra tekrar dene."
        return data, None
    except Exception as exc:
        print("AdsGram request error:", exc)
        return None, "Reklam yüklenemedi. Lütfen daha sonra tekrar dene."

def send_adsgram_ad(chat_id, uid, lang):
    ad, error = get_adsgram_ad(uid, lang)
    if error:
        send_message(chat_id, f"📢 {error}")
        return
    rows = []
    if ad.get("click_url"):
        rows.append([{
            "text": ad.get("button_name") or "Reklamı aç",
            "url": ad["click_url"],
            "style": "primary",
        }])
    if ad.get("reward_url"):
        rows.append([{
            "text": ad.get("button_reward_name") or "Ödülü al",
            "url": ad["reward_url"],
            "style": "success",
        }])
        rows.append([{
            "text": "🔄 Puanımı Kontrol Et",
            "callback_data": "back_home", 
            "style": "primary"
        }])
    
    markup = {"inline_keyboard": rows} if rows else None
    caption = ad["text_html"]
    image_url = ad.get("image_url")
    if image_url:
        result = api("sendPhoto", {
            "chat_id": chat_id,
            "photo": image_url,
            "caption": caption[:1024],
            "parse_mode": "HTML",
            "reply_markup": markup,
            "protect_content": True,
        })
        if not result.get("ok"):
            send_message(chat_id, caption, reply_markup=markup)
    else:
        result = api("sendMessage", {
            "chat_id": chat_id,
            "text": caption[:4096],
            "parse_mode": "HTML",
            "reply_markup": markup,
            "protect_content": True,
            "disable_web_page_preview": True,
        })
        if not result.get("ok"):
            send_message(chat_id, "Reklam gösterilemedi. Biraz sonra tekrar dene.")

# --- HANDLERS ---
def handle_start(chat_id, uid, username, args):
    ref = int(args[0]) if args and args[0].isdigit() and int(args[0]) != uid else None
    is_new = register_user(uid, username or "", ref)
    u = get_user_db(uid)
    lang = u[1] if u else "tr"

    if is_new:
        send_message(chat_id, T("tr", "choose_lang"), reply_markup=lang_keyboard())
        return

    if not (u[5] if u else 0):
        send_message(chat_id, T(lang, "terms_text"), reply_markup=terms_keyboard(lang))
        return

    if check_all_subscriptions(uid):
        process_referral_reward(uid)
        send_message(chat_id, T(lang, "main_menu"), reply_markup=main_keyboard(lang))
    else:
        send_message(chat_id, T(lang, "welcome"), reply_markup=sub_keyboard(lang))

def handle_callback(cq):
    cq_id, data = cq["id"], cq["data"]
    chat_id = cq["message"]["chat"]["id"]
    mid = cq["message"]["message_id"]
    uid = cq["from"]["id"]
    username = cq["from"].get("username", "")

    u = get_user_db(uid)
    lang = u[1] if u else "tr"
    balance = u[0] if u else 0
    refs = u[3] if u else 0
    refs_since_order = u[11] if u and len(u) > 11 else 0

    if data.startswith("lang_"):
        nl = data.split("_")[1]
        if nl not in TR:
            nl = "tr"
        db_execute("UPDATE users SET lang=? WHERE user_id=?", (nl, uid))
        edit_message(chat_id, mid, T(nl, "terms_text"), reply_markup=terms_keyboard(nl))
        answer_callback(cq_id, T(nl, "lang_changed"))
        return

    if data == "accept_terms":
        db_execute("UPDATE users SET terms_accepted=1 WHERE user_id=?", (uid,))
        answer_callback(cq_id, T(lang, "terms_ok"), show_alert=True)
        if check_all_subscriptions(uid):
            process_referral_reward(uid)
            edit_message(chat_id, mid, T(lang, "main_menu"), reply_markup=main_keyboard(lang))
        else:
            edit_message(chat_id, mid, T(lang, "welcome"), reply_markup=sub_keyboard(lang))
        return

    if data == "check_sub":
        if check_all_subscriptions(uid):
            process_referral_reward(uid)
            uu = get_user_db(uid)
            l = uu[1] if uu else lang
            edit_message(chat_id, mid, T(l, "main_menu"), reply_markup=main_keyboard(l))
            answer_callback(cq_id, "✅")
        else:
            answer_callback(cq_id, T(lang, "not_sub"), show_alert=True)
        return

    if data == "back_home":
        db_execute("UPDATE users SET waiting_for_details=0 WHERE user_id=?", (uid,))
        edit_message(chat_id, mid, T(lang, "main_menu"), reply_markup=main_keyboard(lang))
        answer_callback(cq_id)
        return

    if data == "change_lang":
        edit_message(chat_id, mid, T(lang, "choose_lang"), reply_markup=lang_keyboard())
        answer_callback(cq_id)
        return

    if data == "watch_ad":
        answer_callback(cq_id, "Reklam hazırlanıyor…")
        send_adsgram_ad(chat_id, uid, lang)
        return

    if data == "points_info":
        current = get_user_db(uid)
        current_balance = current[0] if current else 0
        answer_callback(cq_id)
        send_message(
            chat_id,
            f"<tg-emoji emoji-id=\"5769482310915199790\">💰</tg-emoji> <b>Puan kazanma</b>\n\n"
            f"📺 Her tamamlanan AdsGram reklamı: <b>+{AD_REWARD_POINTS} puan</b>\n"
            f"<tg-emoji emoji-id=\"5422439311196834318\">👥</tg-emoji> Her geçerli referans: <b>+10 puan</b>\n"
            f"<tg-emoji emoji-id=\"5420323339723881652\">🎉</tg-emoji> Günlük ödül: <b>+5 puan</b>\n"
            f"📋 Görevler: görev ödülüne göre\n\n"
            f"💳 Mevcut bakiyen: <b>{current_balance} puan</b>",
            reply_markup=back_keyboard(lang)
        )
        return

    if data == "menu_profile":
        j = db_execute("SELECT joined_at FROM users WHERE user_id=?", (uid,), fetchone=True)
        joined = j[0][:10] if j and j[0] else "-"
        body = T(lang, "profile_body", user_id=uid, username=username or "yok", balance=balance, refs=refs, joined=joined)
        edit_message(chat_id, mid, f"{T(lang,'profile_title')}\n\n{body}", reply_markup=back_keyboard(lang))
        answer_callback(cq_id)
        return

    if data == "menu_ref":
        link = f"https://t.me/{BOT_USERNAME}?start={uid}"
        edit_message(chat_id, mid, f"{T(lang,'ref_title')}\n\n{T(lang,'ref_body', link=link, refs=refs)}", reply_markup=back_keyboard(lang))
        answer_callback(cq_id)
        return

    if data == "menu_shop":
        edit_message(chat_id, mid, T(lang, "shop_title"), reply_markup=shop_keyboard(lang))
        answer_callback(cq_id)
        return

    if data == "menu_tasks":
        tasks = db_execute("SELECT id FROM tasks", fetchall=True) or []
        text = T(lang, "tasks_list") if tasks else T(lang, "tasks_empty")
        edit_message(chat_id, mid, f"{T(lang,'tasks_title')}\n\n{text}", reply_markup=tasks_keyboard(lang, uid))
        answer_callback(cq_id)
        return

    if data.startswith("task_"):
        tid = int(data.split("_")[1])
        task = db_execute("SELECT title, reward, link FROM tasks WHERE id=?", (tid,), fetchone=True)
        if not task:
            answer_callback(cq_id, "❌ Görev bulunamadı")
            return
        already = db_execute("SELECT id FROM user_tasks WHERE user_id=? AND task_id=?", (uid, tid), fetchone=True)
        if already:
            answer_callback(cq_id, T(lang, "task_completed_already"), show_alert=True)
            return
        title, reward, link = task
        db_execute("INSERT INTO user_tasks (user_id, task_id, completed_at) VALUES (?,?,?)",
                   (uid, tid, datetime.now().isoformat()))
        db_execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (reward, uid))
        nb = balance + reward
        answer_callback(cq_id, T(lang, "task_reward", reward=reward, balance=nb), show_alert=True)
        edit_message(chat_id, mid, T(lang, "main_menu"), reply_markup=main_keyboard(lang))
        return

    if data == "daily":
        today = date.today().isoformat()
        last = u[2] if u else None
        if last == today:
            answer_callback(cq_id, T(lang, "daily_claimed"), show_alert=True)
            return
        db_execute("UPDATE users SET balance = balance + 5, last_daily=? WHERE user_id=?", (today, uid))
        answer_callback(cq_id, T(lang, "daily_success", balance=balance+5), show_alert=True)
        edit_message(chat_id, mid, T(lang, "main_menu"), reply_markup=main_keyboard(lang))
        return

    if data in ("buy_15", "buy_30", "buy_50", "buy_60_uc", "buy_120_uc"):
        if refs_since_order < 4:
            answer_callback(
                cq_id,
                f"❌ Bu sipariş için 4 yeni referans gerekli. Şu an: {refs_since_order}/4",
                show_alert=True
            )
            return
        costs = {"buy_15": 50, "buy_30": 80, "buy_50": 110, "buy_60_uc": 60, "buy_120_uc": 100}
        prods = {"buy_15": "15 Yıldız", "buy_30": "30 Yıldız", "buy_50": "50 Yıldız", "buy_60_uc": "60 UC", "buy_120_uc": "120 UC"}
        cost, pname = costs[data], prods[data]
        if balance >= cost:
            db_execute("UPDATE users SET waiting_for_details=1, pending_product=?, pending_cost=? WHERE user_id=?", (pname, cost, uid))
            edit_message(chat_id, mid, T(lang, "ask_details"), reply_markup=back_keyboard(lang))
            answer_callback(cq_id)
        else:
            answer_callback(cq_id, T(lang, "buy_fail"), show_alert=True)
        return

    if uid in ADMIN_IDS:
        if data == "admin_stats":
            total = db_execute("SELECT COUNT(*) FROM users", fetchone=True)[0]
            refs_t = db_execute("SELECT SUM(total_refs) FROM users", fetchone=True)[0] or 0
            chs = db_execute("SELECT COUNT(*) FROM required_channels", fetchone=True)[0]
            tks = db_execute("SELECT COUNT(*) FROM tasks", fetchone=True)[0]
            edit_message(chat_id, mid, T(lang, "stats_text", total=total, refs=refs_t, channels=chs, tasks=tks), reply_markup=admin_keyboard())
            answer_callback(cq_id)
            return
        if data == "admin_broadcast":
            admin_states[uid] = "broadcast"
            send_message(chat_id, T(lang, "broadcast_prompt"))
            answer_callback(cq_id)
            return
        if data == "admin_add_channel":
            admin_states[uid] = "add_channel"
            send_message(chat_id, T(lang, "add_channel_prompt"))
            answer_callback(cq_id)
            return
        if data == "admin_add_task":
            admin_states[uid] = "add_task"
            send_message(chat_id, T(lang, "add_task_prompt"))
            answer_callback(cq_id)
            return
        if data == "admin_list_channels":
            chs = db_execute("SELECT title, check_id, invite_link FROM required_channels", fetchall=True) or []
            txt = "📢 <b>Kanallar</b>\n\n" + "\n".join([f"• {c[0]} | <code>{c[1]}</code> | {c[2]}" for c in chs]) if chs else "Boş"
            send_message(chat_id, txt, reply_markup=admin_keyboard())
            answer_callback(cq_id)
            return
        if data == "admin_list_tasks":
            tks = db_execute("SELECT id, title, reward, link FROM tasks", fetchall=True) or []
            txt = "📋 <b>Görevler</b>\n\n" + "\n".join([f"#{t[0]} {t[1]} (+{t[2]}) {t[3]}" for t in tks]) if tks else "Boş"
            send_message(chat_id, txt, reply_markup=admin_keyboard())
            answer_callback(cq_id)
            return

    answer_callback(cq_id)

def handle_message(chat_id, uid, username, text):
    u = get_user_db(uid)
    if not u:
        return False
    lang = u[1]
    waiting = u[8]
    pname, pcost = u[9], u[10]
    balance = u[0]

    if waiting == 1:
        current = get_user_db(uid)
        current_balance = current[0] if current else 0
        current_order_refs = current[11] if current and len(current) > 11 else 0
        if current_balance >= pcost and current_order_refs >= 4:
            completed = db_complete_order(uid, username, pname, pcost, text)
            if completed:
                send_message(
                    chat_id,
                    "🎉 Siparişin alındı! Bu siparişte kullandığın 4 referans sıfırlandı. "
                    "Yeni sipariş için yeniden 4 yeni referans getirmen gerekiyor.",
                    reply_markup=main_keyboard(lang)
                )
                for admin in ADMIN_IDS:
                    send_message(admin, T(lang, "order_received_admin", username=username or "yok", user_id=uid, product=pname, cost=pcost, details=text))
            else:
                db_execute("UPDATE users SET waiting_for_details=0, pending_product=NULL, pending_cost=NULL WHERE user_id=?", (uid,))
                send_message(chat_id, "❌ Sipariş tamamlanamadı. Bakiye veya yeni referans sayını kontrol et.", reply_markup=main_keyboard(lang))
        else:
            db_execute("UPDATE users SET waiting_for_details=0, pending_product=NULL, pending_cost=NULL WHERE user_id=?", (uid,))
            if current_order_refs < 4:
                send_message(chat_id, "❌ Sipariş için 4 yeni referans şartı var. Yeni referansların: "
                             f"{current_order_refs}/4.", reply_markup=main_keyboard(lang))
            else:
                send_message(chat_id, T(lang, "buy_fail"), reply_markup=main_keyboard(lang))
        return True

    if uid in ADMIN_IDS:
        state = admin_states.get(uid)
        if state == "broadcast":
            users = db_execute("SELECT user_id FROM users", fetchall=True) or []
            ok = 0
            for row in users:
                try:
                    send_message(row[0], text)
                    ok += 1
                    time.sleep(0.05)
                except Exception:
                    pass
            send_message(chat_id, T(lang, "broadcast_success", count=ok))
            admin_states.pop(uid, None)
            return True
        if state == "add_channel":
            parts = [p.strip() for p in text.split("|")]
            if len(parts) == 3:
                try:
                    db_execute("INSERT OR IGNORE INTO required_channels (title, check_id, invite_link) VALUES (?,?,?)", (parts[0], parts[1], parts[2]))
                    send_message(chat_id, T(lang, "channel_added", title=parts[0]))
                except Exception as e:
                    send_message(chat_id, f"❌ {e}")
            else:
                send_message(chat_id, T(lang, "invalid_format"))
            admin_states.pop(uid, None)
            return True
        if state == "add_task":
            parts = [p.strip() for p in text.split("|")]
            if len(parts) == 3 and parts[1].isdigit():
                db_execute("INSERT INTO tasks (title, reward, link) VALUES (?,?,?)", (parts[0], int(parts[1]), parts[2]))
                send_message(chat_id, T(lang, "task_added", title=parts[0], reward=parts[1]))
            else:
                send_message(chat_id, T(lang, "invalid_format"))
            admin_states.pop(uid, None)
            return True

    return False

def handle_join_request(jr):
    chat_id = jr["chat"]["id"]
    uid = jr["from"]["id"]
    username = jr["from"].get("username", "")
    register_user(uid, username)
    api("approveChatJoinRequest", {"chat_id": chat_id, "user_id": uid})

def run_bot():
    print("✅ Bot başlatıldı.")
    try:
        me = requests.get(f"{BASE_URL}/getMe", timeout=10).json()
        print(f"🤖 @{me['result']['username']}")
    except Exception as e:
        print("⚠️ getMe:", e)

    offset = 0
    allowed = '["message","callback_query","chat_join_request"]'
    while True:
        try:
            r = requests.get(f"{BASE_URL}/getUpdates",
                             params={"offset": offset, "timeout": 30, "allowed_updates": allowed},
                             timeout=40)
            if r.status_code != 200:
                time.sleep(2)
                continue
            for update in r.json().get("result", []):
                offset = update["update_id"] + 1
                try:
                    if "message" in update:
                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        uid = msg["from"]["id"]
                        username = msg["from"].get("username", "")
                        text = msg.get("text", "")

                        if text.strip().split("@")[0] == "/backup" and uid in ADMIN_IDS:
                            send_message(chat_id, "⏳ Database backup hazırlanır...")
                            send_db_backup(chat_id)
                            continue

                        if text.startswith("/admin") and uid in ADMIN_IDS:
                            send_message(chat_id, "🛠 <b>Admin</b>", reply_markup=admin_keyboard())
                            continue
                        if handle_message(chat_id, uid, username, text):
                            continue
                        if text.startswith("/start"):
                            args = text.split()[1:]
                            handle_start(chat_id, uid, username, args)
                    elif "callback_query" in update:
                        handle_callback(update["callback_query"])
                    elif "chat_join_request" in update:
                        handle_join_request(update["chat_join_request"])
                except Exception as e:
                    print("Handler:", e)
        except Exception as e:
            print("Döngü:", e)
            time.sleep(3)

# --- RENDER HEALTH + ADSGRAM REWARD CALLBACK ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/reward":
            params = parse_qs(parsed.query)
            user_values = params.get("userid", [])
            key_values = params.get("key", [])
            uid_text = user_values[0] if user_values else ""
            supplied_key = key_values[0] if key_values else ""

            if not ADSGRAM_REWARD_SECRET or not secrets.compare_digest(
                supplied_key, ADSGRAM_REWARD_SECRET
            ):
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Forbidden")
                return

            if not uid_text.isdigit():
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Bad user id")
                return

            uid = int(uid_text)
            now = datetime.now()
            cutoff = (now - timedelta(seconds=8)).isoformat()
            try:
                updated = db_execute(
                    """UPDATE users
                       SET balance = balance + ?,
                           adsgram_last_reward = ?
                       WHERE user_id = ?
                         AND (adsgram_last_reward IS NULL OR adsgram_last_reward < ?)
                       RETURNING user_id""",
                    (AD_REWARD_POINTS, now.isoformat(), uid, cutoff),
                    fetchone=True
                )
                if updated:
                    try:
                        send_message(
                            uid,
                            f"🎉 Reklam tamamlandı! <b>+{AD_REWARD_POINTS} puan</b> kazandın."
                        )
                    except Exception:
                        pass
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"OK")
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"Already rewarded or user not found")
            except Exception as exc:
                print("AdsGram reward callback error:", exc)
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"Internal Error")
            return

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *a):
        pass

def run_health():
    port = int(os.environ.get("PORT", 10000))
    from http.server import ThreadingHTTPServer
    ThreadingHTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

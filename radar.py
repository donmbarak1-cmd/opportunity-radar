# -*- coding: utf-8 -*-
"""
رادار الفرص (Opportunity Radar) v1.0
=====================================
يراقب منصات مكافآت تصحيح الأكواد ويرسل لك الفرص الجديدة فوراً:

  1) GitHub          ← بورنتي Algora (مشاكل برمجية بمكافآت مالية مضمونة الدفع)
  2) Reddit          ← r/forhire و r/jobbit (أشخاص يبحثون عن مصلح أكواد)

الإشعارات تصل عبر Telegram (أو تُطبع في الكونسول إذا لم تُضبط النقطة بعد).
خالٍ من أي مكتبات خارجية — يحتاج فقط Python 3.11+ الموجود على جهازك.

التشغيل:
    python radar.py          ← يعمل باستمرار ويفحص كل فترة (الافتراضي: 15 دقيقة)
    python radar.py --once   ← فحص واحد ثم يخرج (مناسب لمجدول المهام)
"""

import json
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.parse
import urllib.request
from email.header import Header
from email.mime.text import MIMEText
from datetime import datetime, timezone
from pathlib import Path

# ─────────────────────────────── إعداد عام ───────────────────────────────
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SEEN_FILE = DATA_DIR / "seen.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) OpportunityRadar/1.0"
)

# كلمات تدل على غش/عملات رقمية/إعلانات — نتجاهلها حمايةً من الضجيج
DEFAULT_BLOCKED_WORDS = [
    "crypto", "token", "mining", "nft", "airdrop", "casino", "betting",
    "steam card", "gift card", "forex", "trading bot signal",
]

DEFAULT_CONFIG = {
    "check_interval_minutes": 15,
    "console_language": "en",  # "ar" عربية / "en" إنجليزية — إنجليزية أنسب لتيرمينال ويندوز
    "blocked_words": DEFAULT_BLOCKED_WORDS,
    "github": {
        "enabled": True,
        "max_age_days": 30,
        # استعلامات بحث GitHub Issues. المهمة: labels المبالغ ($) التي تنشئها
        # منصات البورنتي (Algora وأمثالها) على المشاكل المدفوعة.
        "text_queries": [],
        "amount_label_queries": [
            'label:"$50"', 'label:"$100"', 'label:"$200"',
            'label:"$500"', 'label:"$1K"', 'label:"$2K"',
        ],
        "coin_label_query": 'label:"🪙"',
    },
    "reddit": {
        "enabled": True,
        "subreddits": ["forhire", "jobbit"],
        "keywords_en": [
            "fix", "bug", "debug", "error", "code", "script",
            "wordpress", "php", "python", "javascript", "website",
        ],
        "keywords_ar": ["تصحيح", "إصلاح", "خطأ", "كود", "برمجة", "موقع"],
    },
    "telegram": {"bot_token": "", "chat_id": ""},
    "whatsapp": {
        "enabled": True,
        "phone": "",   # رقمك الذي عليه واتساب مع رمز الدولة: +213xxxxxxxxx
        "apikey": "",  # مفتاح CallMeBot — يصلك بعد رسالة التفعيل (اقرأ README)
    },
    "ntfy": {
        "enabled": True,
        "topic": "",   # إشعارات فورية بدون حساب! حمّل تطبيق ntfy واشترك بموضوع — اقرأ README
    },
    "email": {
        "enabled": True,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "from_email": "",    # بريد Gmail المرسل
        "app_password": "",  # كلمة مرور تطبيقات (16 حرفاً) — وليست كلمة مرور حسابك
        "to_email": "",      # البريد الذي ياستقبل التنبيهات
    },
    "discord": {
        "enabled": True,
        "webhook_url": "",   # رابط Webhook من إعدادات قناة ديسكورد
    },
    # تجاوز عبر متغيرات البيئة (اختياري): TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
}


# ─────────────────────────────── أدوات مساعدة ───────────────────────────────
def log(msg: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {msg}")


# لغة رسائل الكونسول — تُقرأ من config.json (console_language)
CONSOLE_LANG = "ar"


def T(ar: str, en: str) -> str:
    """اختيار لغة رسالة الكونسول: en = إنجليزية (تعمل بأي تيرمينال) / ar = عربية."""
    return en if CONSOLE_LANG == "en" else ar


def esc(s: str) -> str:
    """تهريب النص لوضع HTML الخاص بتيليجرام."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fetch_json(url: str, timeout: int = 30, accept: str = "application/json"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def load_config() -> dict:
    """يحمّل config.json، وينشئه من الافتراضي إذا لم يوجد."""
    global CONSOLE_LANG
    path = ROOT / "config.json"
    created = False
    if not path.exists():
        path.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        created = True
    cfg = json.loads(path.read_text(encoding="utf-8"))
    # دمج المفاتيح الناقصة من الافتراضي (توافق الإصدارات المستقبلية)
    for key, val in DEFAULT_CONFIG.items():
        cfg.setdefault(key, val)
    CONSOLE_LANG = "en" if str(cfg.get("console_language", "ar")).lower().startswith("en") else "ar"
    if created:
        log(T("تم إنشاء ملف الإعدادات config.json — عدّل قنوات الإشعارات داخله لاحقاً",
              "Created config.json — edit notification channels inside it later"))
    return cfg


def load_seen() -> dict:
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_seen(seen: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    cutoff = time.time() - 45 * 86400  # احتفظ بـ45 يوماً فقط
    pruned = {}
    for item_id, ts in seen.items():
        try:
            if float(ts) >= cutoff:
                pruned[item_id] = ts
        except (TypeError, ValueError):
            continue
    SEEN_FILE.write_text(
        json.dumps(pruned, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def is_blocked(cfg: dict, item: dict) -> bool:
    """فحص كلمات الغش على العنوان والوصف."""
    text = (item.get("title", "") + " " + item.get("desc", "")).lower()
    return any(w in text for w in cfg.get("blocked_words", []))


# ─────────────────────────────── المصدر 1: GitHub بورنتي ───────────────────────────────
REWARD_RE = re.compile(r"^\$?\s*([\d][\d.,]*)\s*(K|k)?$")


def _label_amount(label: str):
    """يستخرج المبلغ من label مثل: $50 / $2K / 🪙 100 / 🪙"""
    clean = label.replace("🪙", " ").strip()
    m = REWARD_RE.match(clean)
    if not m:
        return None, "🪙" in label
    value = float(m.group(1).replace(",", ""))
    if m.group(2):
        value *= 1000
    return value, True


def source_github(cfg: dict) -> list:
    gc = cfg.get("github", {})
    items = []
    searches = (
        list(gc.get("text_queries", []))
        + list(gc.get("amount_label_queries", []))
        + [gc.get("coin_label_query")]
    )
    for q in searches:
        if not q:
            continue
        url = (
            "https://api.github.com/search/issues?q="
            + urllib.parse.quote(f"{q} type:issue state:open")
            + "&sort=created&order=desc&per_page=30"
        )
        try:
            data = fetch_json(url)
        except Exception as e:  # noqa: BLE001
            log(f"⚠️  GitHub [{q}]: {e}")
            continue
        for it in data.get("items", []):
            labels = [l.get("name", "") for l in it.get("labels", [])]
            amounts = []
            is_bounty = False
            for lab in labels:
                value, flagged = _label_amount(lab)
                if value is not None:
                    amounts.append(value)
                is_bounty = is_bounty or flagged
            if not amounts and not is_bounty:
                continue  # ليس بورنتي — مجرد ذكر كلمة في النص (ضجيج/بوتات)
            created = it.get("created_at") or ""
            try:
                ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
            except ValueError:
                ts = 0
            age_days = max(0.0, (time.time() - ts) / 86400)
            if age_days > gc.get("max_age_days", 30):
                continue
            reward = f"${max(amounts):g}" if amounts else ""
            if amounts and max(amounts) < 10:  # تجاهل المبالغ الرمزية
                continue
            repo = (it.get("repository_url") or "").replace(
                "https://api.github.com/repos/", ""
            )
            assigned = bool(it.get("assignee") or it.get("assignees"))
            items.append(
                {
                    "id": f"gh-{it.get('id')}",
                    "source": f"GitHub بورنتي ({repo})",
                    "title": it.get("title") or "بدون عنوان",
                    "url": it.get("html_url") or "",
                    "reward": reward,
                    "bids": 0,
                    "age_h": age_days * 24,
                    "ts": ts,
                    "desc": (it.get("body") or "")[:200],
                    "assigned": assigned,
                }
            )
    uniq = {}
    for it in items:
        uniq[it["id"]] = it
    return sorted(uniq.values(), key=lambda i: i["ts"], reverse=True)


# ─────────────────────────────── المصدر 3: Reddit ───────────────────────────────
def source_reddit(cfg: dict) -> list:
    rc = cfg.get("reddit", {})
    keywords = [k.lower() for k in rc.get("keywords_en", [])] + [
        k.lower() for k in rc.get("keywords_ar", [])
    ]
    items = []
    for sub in rc.get("subreddits", []):
        url = f"https://www.reddit.com/r/{sub}/new.json?limit=25"
        try:
            data = fetch_json(url)
        except Exception as e:  # noqa: BLE001
            log(T(f"⚠️  Reddit r/{sub}: {e} (قد يكون محجوباً من شبكتك — سأتجاهله)",
                  f"⚠️  Reddit r/{sub}: {e} (blocked on this network — skipping)"))
            continue
        posts = (
            ((data.get("data") or {}).get("children") or [])
        )
        for child in posts:
            d = child.get("data") or {}
            title = (d.get("title") or "").lower()
            if "[for hire]" in title or "for hire" in title:
                continue  # إعلان باحث عن عمل — ليس فرصة لنا
            if not any(k in title for k in keywords):
                continue
            ts = d.get("created_utc") or 0
            items.append(
                {
                    "id": f"rd-{d.get('id')}",
                    "source": f"Reddit r/{sub}",
                    "title": d.get("title") or "بدون عنوان",
                    "url": "https://www.reddit.com" + (d.get("permalink") or ""),
                    "reward": "",
                    "bids": 0,
                    "age_h": max(0.0, (time.time() - ts) / 3600),
                    "ts": ts,
                    "desc": (d.get("selftext") or "")[:200],
                }
            )
    uniq = {}
    for it in items:
        uniq[it["id"]] = it
    return sorted(uniq.values(), key=lambda i: i["ts"], reverse=True)


# ─────────────────────────────── الإشعارات ───────────────────────────────
def wa_send(cfg: dict, plain_text: str) -> bool:
    """إرسال واتساب عبر CallMeBot (مجاني للاستخدام الشخصي)."""
    import os

    wa = cfg.get("whatsapp", {})
    phone = os.environ.get("WHATSAPP_PHONE", wa.get("phone") or "")
    apikey = os.environ.get("WHATSAPP_APIKEY", wa.get("apikey") or "")
    if not phone or not apikey:
        return False
    url = (
        "https://api.callmebot.com/whatsapp.php"
        f"?phone={urllib.parse.quote(phone)}"
        f"&text={urllib.parse.quote(plain_text)}"
        f"&apikey={urllib.parse.quote(apikey)}"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace").lower()
            if resp.status == 200 and "error" not in body:
                return True
            log("⚠️  واتساب CallMeBot رد غير متوقع — تحقق من الرقم والمفتاح")
            return False
    except Exception as e:  # noqa: BLE001
        log(f"⚠️  فشل إرسال واتساب: {e}")
        return False


def ntfy_send(cfg: dict, plain_text: str) -> bool:
    """إشعارات فورية عبر ntfy.sh — بدون حساب، الموضوع هو عنوان التوصيل."""
    nt = cfg.get("ntfy", {})
    if not nt.get("enabled", True):
        return False
    topic = os.environ.get("NTFY_TOPIC", nt.get("topic") or "")
    if not topic:
        return False
    url = "https://ntfy.sh/" + urllib.parse.quote(topic, safe="")
    req = urllib.request.Request(
        url,
        data=plain_text.encode("utf-8"),
        headers={
            "Title": "Opportunity Radar",
            "Priority": "high",
            "Tags": "white_check_mark",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except Exception as e:  # noqa: BLE001
        log(f"⚠️  فشل إرسال NTFY: {e}")
        return False


def discord_send(cfg: dict, plain_text: str) -> bool:
    """إرسال عبر Webhook ديسكورد."""
    dc = cfg.get("discord", {})
    url = os.environ.get("DISCORD_WEBHOOK", dc.get("webhook_url") or "")
    if not url:
        return False
    payload = json.dumps({"content": plain_text}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in (200, 204)
    except Exception as e:  # noqa: BLE001
        log(f"⚠️  فشل إرسال ديسكورد: {e}")
        return False


def email_send(cfg: dict, body: str) -> bool:
    """إرسال بريد إلكتروني عبر SMTP (Gmail افتراضياً) بكلمة مرور تطبيقات."""
    em = cfg.get("email", {})
    if not em.get("enabled", True):
        return False
    from_email = os.environ.get("RADAR_EMAIL_FROM", em.get("from_email") or "")
    app_password = os.environ.get("RADAR_EMAIL_PASS", em.get("app_password") or "")
    to_email = os.environ.get("RADAR_EMAIL_TO", em.get("to_email") or "")
    if not (from_email and app_password and to_email):
        return False
    host = em.get("smtp_host") or "smtp.gmail.com"
    port = int(em.get("smtp_port") or 587)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header("🎯 فرص تصحيح أكواد جديدة", "utf-8")
    msg["From"] = from_email
    msg["To"] = to_email
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()
        with server:
            server.login(from_email, app_password)
            server.sendmail(from_email, [to_email], msg.as_string())
        return True
    except Exception as e:  # noqa: BLE001
        log(f"⚠️  فشل إرسال البريد: {e}")
        return False


def format_item(it: dict) -> str:
    lines = [f"🔹 <b>{esc(it['title'])}</b>"]
    meta = []
    if it.get("reward"):
        meta.append(f"💵 {esc(it['reward'])}")
    if it.get("bids"):
        meta.append(f"👥 {it['bids']} عرض")
    if it.get("age_h") is not None:
        h = it["age_h"]
        meta.append(f"⏱ منذ {h:.0f} ساعة" if h >= 1 else "⏱ جديد جداً")
    if it.get("assigned"):
        meta.append("📌 مُسند لشخص")
    if meta:
        lines.append(" • ".join(meta))
    lines.append(esc(it["source"]))
    lines.append(it["url"])
    return "\n".join(lines)


def format_item_wa(it: dict) -> str:
    """نسخة تنسيق واتساب: *غامق* بدل وسوم HTML."""
    lines = [f"🔹 *{it['title']}*"]
    meta = []
    if it.get("reward"):
        meta.append(f"💵 {it['reward']}")
    if it.get("bids"):
        meta.append(f"👥 {it['bids']} عرض")
    if it.get("age_h") is not None:
        h = it["age_h"]
        meta.append(f"⏱ منذ {h:.0f} ساعة" if h >= 1 else "⏱ جديد جداً")
    if it.get("assigned"):
        meta.append("📌 مُسند لشخص")
    if meta:
        lines.append(" • ".join(meta))
    lines.append(it["source"])
    lines.append(it["url"])
    return "\n".join(lines)


def _chunk_join(blocks: list, header: str, limit: int) -> list:
    prefix = (header + "\n\n") if header else ""
    chunks, cur = [], prefix
    for block in blocks:
        piece = block + "\n\n"
        if len(cur) + len(piece) > limit and cur != prefix:
            chunks.append(cur)
            cur = prefix
        cur += piece
    if cur.strip():
        chunks.append(cur)
    return chunks


def tg_send(cfg: dict, html_text: str) -> bool:
    tg = cfg.get("telegram", {})
    token = tg.get("bot_token") or ""
    chat = str(tg.get("chat_id") or "")
    # دعم متغيرات البيئة كأفضلية
    token = token or ""
    import os

    token = os.environ.get("TELEGRAM_BOT_TOKEN", token)
    chat = os.environ.get("TELEGRAM_CHAT_ID", chat)
    if not token or not chat:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": chat,
            "text": html_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            ok = json.loads(resp.read().decode("utf-8", "replace")).get("ok")
            if not ok:
                log("⚠️  تيليجرام رفض الرسالة — تحقق من التوكن وchat_id")
            return bool(ok)
    except Exception as e:  # noqa: BLE001
        log(f"⚠️  فشل إرسال تيليجرام: {e}")
        return False


def notify(cfg: dict, items: list, header: str) -> None:
    if not items:
        return
    tg_blocks = [format_item(it) for it in items]
    plain_blocks = [format_item_wa(it) for it in items]
    plain_header = header.replace("<b>", "*").replace("</b>", "*")
    clean_header = header.replace("<b>", "").replace("</b>", "")

    sent_any = False
    # القناة 1: تيليجرام (HTML)
    for chunk in _chunk_join(tg_blocks, header, 3800):
        sent_any = tg_send(cfg, chunk.rstrip()) or sent_any
    # القناة 2: واتساب عبر CallMeBot (رسائل قصيرة 900 حرف)
    for chunk in _chunk_join(plain_blocks, plain_header, 900):
        sent_any = wa_send(cfg, chunk.rstrip()) or sent_any
    # القناة 3: NTFY إشعارات فورية بدون حساب
    for chunk in _chunk_join(plain_blocks, clean_header, 3500):
        sent_any = ntfy_send(cfg, chunk.rstrip()) or sent_any
    # القناة 4: ديسكورد (حد الرسالة 2000 حرف)
    for chunk in _chunk_join(plain_blocks, plain_header, 1800):
        sent_any = discord_send(cfg, chunk.rstrip()) or sent_any
    # القناة 5: البريد الإلكتروني — رسالة واحدة بكل الفرص
    body = clean_header + "\n\n" + "\n\n".join(plain_blocks)
    sent_any = email_send(cfg, body) or sent_any

    if not sent_any:
        # لا قناة مضبوطة بعد — اطبع في الكونسول
        for chunk in _chunk_join(tg_blocks, header, 3800):
            plain = chunk.replace("<b>", "").replace("</b>", "")
            print("\n" + "=" * 60)
            print(plain.rstrip())
        print("=" * 60)


# ─────────────────────────────── الدورة الرئيسية ───────────────────────────────
def run_cycle(cfg: dict, seen: dict) -> None:
    log(T("🔍 جاري فحص المصادر...", "🔍 Scanning sources..."))
    sources = []
    if (cfg.get("github") or {}).get("enabled", True):
        sources.append(("GitHub", source_github))
    if (cfg.get("reddit") or {}).get("enabled", True):
        sources.append(("Reddit", source_reddit))

    all_items = []
    for name, fn in sources:
        try:
            got = fn(cfg)
            log(T(f"   • {name}: {len(got)} فرصة مطابقة",
                  f"   • {name}: {len(got)} matching"))
            all_items.extend(got)
        except Exception as e:  # noqa: BLE001
            log(T(f"   • {name}: فشل كامل — {e}",
                  f"   • {name}: FAILED — {e}"))

    # فلترة الغش + المكرر
    fresh = [i for i in all_items if i["id"] not in seen and not is_blocked(cfg, i)]
    fresh.sort(key=lambda i: i.get("ts") or 0, reverse=True)

    if not seen and all_items:
        # أول تشغيل: لا تغرق المستخدم — أرسل معاينة لأحدث 5 فقط
        log(T(f"أول تشغيل: سُجلت {len(all_items)} فرصة قائمة، وإرسال معاينة لأحدث 5",
              f"First run: registered {len(all_items)} existing opportunities, previewing newest 5"))
        notify(cfg, fresh[:5], "🚀 <b>رادار الفرص يعمل الآن!</b> معاينة أحدث 5 فرص:")
        fresh = []
    elif fresh:
        log(T(f"🆕 {len(fresh)} فرصة جديدة!",
              f"🆕 {len(fresh)} NEW opportunities!"))
        notify(cfg, fresh, f"🎯 <b>{len(fresh)} فرصة تصحيح أكواد جديدة!</b>")
    else:
        log(T("لا فرص جديدة في هذه الدورة",
              "No new opportunities in this cycle"))

    # تسجيل كل ما شوهد (حتى لا يعاد إرساله)
    now = time.time()
    for it in all_items:
        seen[it["id"]] = now
    save_seen(seen)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    once = "--once" in sys.argv
    cfg = load_config()
    seen = load_seen()
    first = not seen

    if CONSOLE_LANG == "en":
        print("╔══════════════════════════════════════╗")
        print("║        Opportunity Radar  v1.0       ║")
        print("╚══════════════════════════════════════╝")
    else:
        print("╔══════════════════════════════════════╗")
        print("║   رادار الفرص — Opportunity Radar    ║")
        print("╚══════════════════════════════════════╝")
    if first:
        log(T("أول تشغيل — سيتم بناء قاعدة الفرص الحالية أولاً",
              "First run — registering current opportunities as baseline"))
    channels = []
    tg = cfg.get("telegram", {})
    if tg.get("bot_token") and tg.get("chat_id"):
        channels.append("Telegram")
    wa = cfg.get("whatsapp", {})
    if wa.get("phone") and wa.get("apikey"):
        channels.append("WhatsApp")
    nt = cfg.get("ntfy", {})
    if nt.get("topic"):
        channels.append("NTFY")
    em = cfg.get("email", {})
    if em.get("from_email") and em.get("app_password") and em.get("to_email"):
        channels.append("Email")
    dc = cfg.get("discord", {})
    if dc.get("webhook_url"):
        channels.append("Discord")
    if channels:
        log(T("📬 قنوات الإشعارات المفعّلة: ", "📬 Active notification channels: ") + ", ".join(channels))
    else:
        log(T("ℹ️  لا قناة إشعارات مضبوطة بعد — ستظهر الفرص هنا في الكونسول",
              "ℹ️  No notification channel configured yet — opportunities will print here"))
        log(T("   خيارات سهلة في README.md: NTFY (بدون حساب) / إيميل / واتساب / ديسكورد",
              "   Easy options in README.md: NTFY (no account) / Email / WhatsApp / Discord"))

    if once:
        run_cycle(cfg, seen)
        return

    interval = max(3, int(cfg.get("check_interval_minutes", 15))) * 60
    log(T(f"⚙️  وضع المراقبة المستمرة: فحص كل {interval // 60} دقيقة (Ctrl+C للإيقاف)",
          f"⚙️  Continuous monitoring: checking every {interval // 60} minutes (Ctrl+C to stop)"))
    while True:
        try:
            run_cycle(cfg, seen)
        except Exception as e:  # noqa: BLE001 — الدورة القادمة تعوض أي خطأ
            log(T(f"❌ خطأ في الدورة (سأعيد المحاولة): {e}",
                  f"❌ Cycle error (will retry): {e}"))
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            log(T("تم الإيقاف بإرادتك — إلى اللقاء 👋",
                  "Stopped by user — goodbye 👋"))
            return


if __name__ == "__main__":
    main()

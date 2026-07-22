# -*- coding: utf-8 -*-
"""Морской обзор — ежедневный сборщик видео о происшествиях на море.

Читает открытые RSS-ленты YouTube-каналов из channels.json, отбирает свежие
видео про удары БПЛА по судам, атаки, шторма и действия ВМС, и строит:
  docs/index.html            — страница обзора со встроенными видео
  docs/archive/ГГГГ-ММ-ДД.html — архивная копия за день
  docs/tg.txt                — текст утреннего поста для Telegram

Видео НЕ скачиваются и НЕ перезаливаются: страница показывает их штатным
плеером YouTube, все просмотры и права остаются у авторов.
"""

import json
import os
import re
import socket
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
ARCHIVE = DOCS / "archive"
SITE_URL = "https://trainingsw75-cloud.github.io/morskoy-obzor/"

MSK = timezone(timedelta(hours=3))
WINDOW_HOURS = 36        # обычное окно свежести
WIDE_WINDOW_HOURS = 72   # расширенное, если новостей мало
MIN_ITEMS = 5
MAX_ITEMS = 16
TG_MAX_LINKS = 8

NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

# --- ключевые слова -------------------------------------------------------
# МОРЕ: слово должно указывать на море/судно/флот
MARINE = re.compile(
    r"\bмор(е|я|ю|ем)\b|\bморск|\bморськ|\bморяк|\bсудн|\bтанкер|\bкорабл|\bкорабел"
    r"|\bфлот|\bвмс\b|\bвмф\b|\bкатер|\bпорт|\bгаван|\bсухогруз|\bбалкер"
    r"|\bконтейнеровоз|\bпаром|\bфрегат|\bэсминц|\bэсминец|\bкрейсер|\bподлодк"
    r"|\bподводн|\bпідводн|\bбуксир|\bбарж|\bматрос|\bэкипаж|\bверф|\bсудоход"
    r"|\bship(s|ping)?\b|\bvessel|\btanker|\bcargo\b|\bnav(y|al)\b|\bfleet"
    r"|\bmaritime|\bports?\b|\bharbou?r|\bstrait|\bseas?\b|\boffshore|\bsailor"
    r"|\bcrew\b|\bfreighter|\bbulker|\bwarship|\bfrigate|\bdestroyer|\bsubmarine"
    r"|\bcoast guard|\bhormuz|\bсуэц|\bsuez|\bбосфор|\bbosph?orus|\bred sea"
    r"|\bblack sea|\bbaltic",
    re.IGNORECASE,
)

# УГРОЗА: происшествие/боевые действия/стихия
THREAT = re.compile(
    r"\bбпла\b|\bдрон|\bбеспилотн|\bбезпілотн|\bбезэкипаж|\bудар|\bатак|\bракет"
    r"|\bвзрыв|\bвибух|\bобстрел|\bобстріл|\bпожар|\bгорит\b|\bгорят\b|\bкрушен"
    r"|\bзатону|\bтонет|\bутонул|\bпотонул|\bстолкнов|\bторпед|\bзахват|\bпират"
    r"|\bзадержа|\bарестова|\bна мель|\bмель\b|\bбедстви|\bsos\b|\bспасат"
    r"|\bперехват|\bуничтож|\bзнищ|\bпораз|\bвлуч|\bхусит|\bподрыв|\bминир"
    r"|\bэвакуа|\bшторм|\bураган|\bтайфун"
    r"|\bstrike|\battack|\bdrone|\bmissile|\bexplos|\bblast|\bfire\b|\bsink"
    r"|\bsank|\bcapsiz|\bcollision|\bcollide|\bseiz|\bhijack|\bintercept"
    r"|\bhouthi|\brescue|\bdistress|\baground|\bstorm|\bhurricane|\btyphoon"
    r"|\bwreck|\btorpedo",
    re.IGNORECASE,
)

# Достаточно одного такого выражения
SOLO = re.compile(
    r"морск(ой|ие|их|им)\s+дрон|безэкипажн|\bбэк\b|морск(ая|ие)\s+мин"
    r"|\bхусит|\bhouthi|\bred sea|\bhormuz|\bормуз",
    re.IGNORECASE,
)

MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def maybe_enable_socks():
    """Локальный запуск на машине Романа: YT_SOCKS=127.0.0.1:10808."""
    proxy = os.environ.get("YT_SOCKS")
    if not proxy:
        return
    import socks  # PySocks
    host, port = proxy.rsplit(":", 1)
    socks.set_default_proxy(socks.SOCKS5, host, int(port), rdns=True)
    socket.socket = socks.socksocket
    # иначе urllib вдобавок применит системный прокси Windows поверх SOCKS
    urllib.request.install_opener(
        urllib.request.build_opener(urllib.request.ProxyHandler({}))
    )


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (sea-review-bot)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def parse_feed(xml_bytes, channel):
    items = []
    root = ET.fromstring(xml_bytes)
    for entry in root.findall("a:entry", NS):
        vid = entry.findtext("yt:videoId", "", NS)
        title = (entry.findtext("a:title", "", NS) or "").strip()
        published = entry.findtext("a:published", "", NS)
        desc = ""
        group = entry.find("media:group", NS)
        if group is not None:
            desc = (group.findtext("media:description", "", NS) or "")[:400]
        if not vid or not title or not published:
            continue
        try:
            dt = datetime.fromisoformat(published)
        except ValueError:
            continue
        items.append({
            "id": vid,
            "title": title,
            "published": dt,
            "channel": channel["name"],
            "maritime": channel.get("maritime", False),
            "desc": desc,
        })
    return items


def relevant(item):
    """Морской канал: хватает признака угрозы. Общий канал: море+угроза в заголовке."""
    title = item["title"]
    if item["maritime"]:
        return bool(THREAT.search(title) or THREAT.search(item["desc"]))
    return bool((MARINE.search(title) and THREAT.search(title)) or SOLO.search(title))


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def fmt_msk(dt):
    d = dt.astimezone(MSK)
    return f"{d.day} {MONTHS_RU[d.month - 1]}, {d:%H:%M} МСК"


def build_html(items, now_msk, archive_days):
    date_line = f"{now_msk.day} {MONTHS_RU[now_msk.month - 1]} {now_msk.year}"
    if items:
        top = " • ".join(i["title"][:80] for i in items[:3])
        summary = f"Сегодня в обзоре {len(items)} видео. Главное: {top}"
    else:
        summary = ("За последние сутки подходящих видео в отслеживаемых каналах "
                   "не нашлось. Обзор обновится завтра в 08:00 МСК.")

    cards = []
    for i in items:
        cards.append(f"""
    <article class="card">
      <div class="video" data-id="{i['id']}">
        <img src="https://i.ytimg.com/vi/{i['id']}/hqdefault.jpg" alt="" loading="lazy">
        <span class="play">▶</span>
      </div>
      <div class="meta">
        <h3>{esc(i['title'])}</h3>
        <p><span class="ch">{esc(i['channel'])}</span> · {fmt_msk(i['published'])}</p>
        <a href="https://www.youtube.com/watch?v={i['id']}" target="_blank" rel="noopener">Смотреть на YouTube ↗</a>
      </div>
    </article>""")

    arch_links = " · ".join(
        f'<a href="archive/{d}.html">{d[8:10]}.{d[5:7]}</a>' for d in archive_days
    ) or "пока пусто"

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Морской обзор — {date_line}</title>
<meta name="description" content="Ежедневный обзор происшествий на море: удары БПЛА, атаки на суда, шторма, действия ВМС. Обновляется каждый день в 08:00 МСК.">
<style>
  :root {{ --bg:#0b1220; --card:#141d31; --txt:#e8edf7; --dim:#93a1bd; --red:#ff4444; --cyan:#4fc3f7; }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--bg); color:var(--txt); font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; line-height:1.45; }}
  header {{ padding:28px 16px 18px; text-align:center; background:linear-gradient(180deg,#101a30,var(--bg)); border-bottom:1px solid #1e2a44; }}
  .badge {{ display:inline-block; background:var(--red); color:#fff; font-weight:800; letter-spacing:.08em; padding:4px 14px; border-radius:6px; font-size:14px; }}
  h1 {{ font-size:clamp(24px,5vw,40px); margin:10px 0 4px; }} h1 .a {{ color:var(--cyan); }}
  .date {{ color:var(--dim); font-size:15px; }}
  .summary {{ max-width:900px; margin:14px auto 0; color:var(--txt); font-size:15px; background:#101a30; border:1px solid #1e2a44; border-radius:10px; padding:10px 16px; }}
  main {{ max-width:1100px; margin:0 auto; padding:22px 14px 8px; display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:18px; }}
  .card {{ background:var(--card); border:1px solid #1e2a44; border-radius:12px; overflow:hidden; display:flex; flex-direction:column; }}
  .video {{ position:relative; aspect-ratio:16/9; background:#000; cursor:pointer; }}
  .video img {{ width:100%; height:100%; object-fit:cover; display:block; }}
  .video iframe {{ width:100%; height:100%; border:0; position:absolute; inset:0; }}
  .play {{ position:absolute; inset:0; display:flex; align-items:center; justify-content:center; font-size:26px; color:#fff; background:rgba(8,12,24,.35); }}
  .play::after {{ content:""; position:absolute; width:64px; height:44px; background:var(--red); border-radius:10px; z-index:-1; }}
  .meta {{ padding:12px 14px 14px; }}
  .meta h3 {{ font-size:15.5px; margin-bottom:6px; }}
  .meta p {{ color:var(--dim); font-size:13px; margin-bottom:8px; }}
  .ch {{ color:var(--cyan); }}
  .meta a {{ color:var(--dim); font-size:13px; text-decoration:none; border-bottom:1px dotted var(--dim); }}
  .empty {{ grid-column:1/-1; text-align:center; color:var(--dim); padding:40px 0; font-size:16px; }}
  footer {{ max-width:1100px; margin:0 auto; padding:10px 16px 34px; color:var(--dim); font-size:13px; text-align:center; }}
  footer a {{ color:var(--cyan); text-decoration:none; }}
</style>
</head>
<body>
<header>
  <span class="badge">⚡ СРОЧНО · ВАЖНО · НОВОЕ</span>
  <h1>МОРСКОЙ <span class="a">ОБЗОР</span></h1>
  <div class="date">за {date_line} · обновляется ежедневно в 08:00 по Москве</div>
  <div class="summary">{esc(summary)}</div>
</header>
<main>
{''.join(cards) if cards else '<div class="empty">Сегодня без происшествий в подборке. Это хорошие новости.</div>'}
</main>
<footer>
  <p>Архив: {arch_links}</p>
  <p style="margin-top:8px">Обзор собирается автоматически из открытых YouTube-каналов
  (BBC, DW, Настоящее Время, УНІАН, PortNews, Sky News, Al Jazeera, WION, WGOW Shipping).
  Все видео принадлежат их авторам и воспроизводятся штатным плеером YouTube.</p>
  <p style="margin-top:8px"><img src="https://abacus.jasoncameron.dev/hit/morskoy-obzor/site?bg=0b1220&text=93a1bd" alt="" height="18"></p>
</footer>
<script>
document.addEventListener('click', function (e) {{
  var v = e.target.closest('.video');
  if (!v || v.querySelector('iframe')) return;
  var f = document.createElement('iframe');
  f.src = 'https://www.youtube-nocookie.com/embed/' + v.dataset.id + '?autoplay=1';
  f.allow = 'autoplay; encrypted-media; picture-in-picture';
  f.allowFullscreen = true;
  v.innerHTML = '';
  v.appendChild(f);
}});
</script>
</body>
</html>
"""


def build_tg(items, now_msk):
    date_line = f"{now_msk.day} {MONTHS_RU[now_msk.month - 1]} {now_msk.year}"
    lines = [f"⚡ МОРСКОЙ ОБЗОР — {date_line}, 08:00 МСК", ""]
    if not items:
        lines.append("За последние сутки подходящих видео не нашлось. Следующий обзор — завтра в 08:00.")
    else:
        lines.append(f"Сегодня в обзоре: {len(items)} видео.")
        lines.append("")
        for n, i in enumerate(items[:TG_MAX_LINKS], 1):
            title = i["title"] if len(i["title"]) <= 90 else i["title"][:87] + "…"
            lines.append(f"{n}. {title} ({i['channel']})")
            lines.append(f"https://youtu.be/{i['id']}")
    lines.append("")
    lines.append(f"Полный обзор с видео: {SITE_URL}")
    text = "\n".join(lines)
    return text[:3900]


def main():
    maybe_enable_socks()
    channels = json.loads((ROOT / "channels.json").read_text(encoding="utf-8"))
    now_utc = datetime.now(timezone.utc)
    now_msk = now_utc.astimezone(MSK)

    all_items = []
    for ch in channels:
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={ch['id']}"
        try:
            all_items += parse_feed(fetch(url), ch)
        except Exception as e:
            print(f"[warn] {ch['name']}: {e}")

    fresh = [i for i in all_items if i["published"] >= now_utc - timedelta(hours=WINDOW_HOURS)]
    picked = [i for i in fresh if relevant(i)]
    if len(picked) < MIN_ITEMS:
        wide = [i for i in all_items if i["published"] >= now_utc - timedelta(hours=WIDE_WINDOW_HOURS)]
        picked = [i for i in wide if relevant(i)]

    seen, unique = set(), []
    for i in sorted(picked, key=lambda x: x["published"], reverse=True):
        if i["id"] not in seen:
            seen.add(i["id"])
            unique.append(i)
    unique = unique[:MAX_ITEMS]

    ARCHIVE.mkdir(parents=True, exist_ok=True)
    archive_days = sorted((p.stem for p in ARCHIVE.glob("*.html")), reverse=True)
    today = now_msk.strftime("%Y-%m-%d")
    if today not in archive_days:
        archive_days.insert(0, today)
    archive_days = archive_days[:14]

    html_page = build_html(unique, now_msk, archive_days)
    (DOCS / "index.html").write_text(html_page, encoding="utf-8")
    (ARCHIVE / f"{today}.html").write_text(html_page, encoding="utf-8")
    (DOCS / "tg.txt").write_text(build_tg(unique, now_msk), encoding="utf-8")

    print(f"[ok] отобрано {len(unique)} видео из {len(all_items)} свежих записей")
    for i in unique:
        print(f"  - {i['published']:%m-%d %H:%M} | {i['channel']}: {i['title'][:70]}")


if __name__ == "__main__":
    main()


"""Send an approved package through official APIs, with durable duplicate protection."""
import argparse
import base64
from hashlib import sha256
import http.client
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode, urlsplit

from pipeline import SITE, digest, validate

GOOGLE = "www.googleapis.com"
RETRYABLE = {429, 500, 502, 503, 504}


class DeliveryError(Exception):
    pass


def setting(name):
    value = os.environ.get(name)
    if not value:
        raise DeliveryError(f"Требуется настройка {name}")
    return value


def request(host, method, path, body=None, headers=None, retry=False):
    for attempt in range(4 if retry else 1):
        connection = http.client.HTTPSConnection(host, timeout=30)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            result = response.status, dict(response.getheaders()), response.read(1_000_000)
            if not retry or result[0] not in RETRYABLE or attempt == 3:
                return result
        except (OSError, http.client.HTTPException):
            if not retry or attempt == 3:
                raise DeliveryError("Сбой связи. Проверьте журнал перед повтором.") from None
        finally:
            connection.close()
        time.sleep(2 ** attempt)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def payload(result, service):
    code, _, body = result
    if code not in (200, 201):
        hint = {401: "проверьте срок действия OAuth или токена",
                403: "проверьте права, роль, App Review или квоту",
                429: "достигнут лимит запросов"}.get(code, "проверьте конфигурацию и журнал")
        raise DeliveryError(f"{service} HTTP {code} — {hint}")
    return json.loads(body)


class Journal:
    """GitHub Contents API on an owner-configured branch of a private repository."""

    def __init__(self, channel, event):
        repo = os.getenv("SHORTS_JOURNAL_REPOSITORY") or setting("GITHUB_REPOSITORY")
        self.branch = setting("SHORTS_JOURNAL_BRANCH")
        if (not re.fullmatch(r"[\w.-]+/[\w.-]+", repo)
                or not re.fullmatch(r"sw75-journal[-\w]*", self.branch)):
            raise DeliveryError("Настройте отдельную ветку sw75-journal")
        self.headers = {**bearer(setting("SHORTS_JOURNAL_TOKEN")), "User-Agent": "sw75-shorts"}
        info = payload(request("api.github.com", "GET", f"/repos/{repo}", headers=self.headers, retry=True), "GitHub")
        if info.get("private") is not True:
            raise DeliveryError("Для журнала требуется приватный репозиторий")
        self.path = f"/repos/{repo}/contents/sw75-state/{channel}/{event}.json"
        self.blob = None

    def write(self, value):
        content = base64.b64encode(json.dumps(value).encode()).decode()
        data = {"message": "Record delivery state", "branch": self.branch, "content": content}
        if self.blob:
            data["sha"] = self.blob
        result = payload(request("api.github.com", "PUT", self.path, json.dumps(data).encode(),
                                 {**self.headers, "Content-Type": "application/json"}), "GitHub журнал")
        self.blob = result["content"]["sha"]

    def read(self):
        result = request("api.github.com", "GET", self.path + "?" + urlencode({"ref": self.branch}),
                         headers=self.headers, retry=True)
        if result[0] == 200:
            return json.loads(base64.b64decode(payload(result, "GitHub")["content"]))
        if result[0] != 404:
            payload(result, "GitHub журнал")
        return None

    def reserve(self):
        record = self.read()
        if record is not None:
            if record.get("id"):
                print(f"Уже выполнено. ID {record['id']}")
                return False
            raise DeliveryError("Есть незавершённая запись. Сверьте состояние на платформе перед повтором.")
        self.write({"state": "pending"})
        return True


def refresh():
    body = urlencode({"grant_type": "refresh_token", "client_id": setting("YT_CLIENT_ID"),
                      "client_secret": setting("YT_CLIENT_SECRET"), "refresh_token": setting("YT_REFRESH_TOKEN")})
    result = request("oauth2.googleapis.com", "POST", "/token", body.encode(),
                     {"Content-Type": "application/x-www-form-urlencoded"}, retry=True)
    return payload(result, "Google OAuth")["access_token"]


def youtube_metadata(folder):
    read = lambda name: (folder / name).read_text(encoding="utf-8").strip()
    return {"snippet": {"title": read("youtube_title.txt"), "description": read("youtube_description.txt"),
                        "tags": json.loads(read("youtube_tags.json")), "categoryId": "25",
                        "defaultLanguage": "ru"},
            "status": {"privacyStatus": "private"}}


def youtube_upload(folder, approval, journal):
    video = folder / "shorts.mp4"
    if not video.is_file() or digest(video) != approval or not 250_000 <= video.stat().st_size <= 60_000_000:
        raise DeliveryError("SHA-256 или размер MP4 не совпадает с подтверждённым файлом")
    token = refresh()
    headers = bearer(token)
    channel_id = setting("YT_CHANNEL_ID")
    channels = payload(request(GOOGLE, "GET", "/youtube/v3/channels?part=id&mine=true",
                               headers=headers, retry=True), "YouTube канал")
    if not re.fullmatch(r"UC[\w-]{22}", channel_id) or [c["id"] for c in channels["items"]] != [channel_id]:
        raise DeliveryError("OAuth не соответствует подтверждённому ID канала")
    if not journal.reserve():
        return
    data = video.read_bytes()
    size = len(data)
    response = request(GOOGLE, "POST", "/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                       json.dumps(youtube_metadata(folder)).encode(),
                       {**headers, "Content-Type": "application/json", "X-Upload-Content-Length": str(size),
                        "X-Upload-Content-Type": "video/mp4"})
    if response[0] not in (200, 201):
        payload(response, "YouTube сессия")
    location = urlsplit(next((v for k, v in response[1].items() if k.lower() == "location"), ""))
    if (location.scheme != "https" or location.netloc != GOOGLE
            or location.path != "/upload/youtube/v3/videos"):
        raise DeliveryError("Google вернул неожиданный адрес сессии")
    path = location.path + "?" + location.query
    offset = 0
    for attempt in range(5):
        try:
            result = request(GOOGLE, "PUT", path, data[offset:],
                             {**headers, "Content-Type": "video/mp4",
                              "Content-Range": f"bytes {offset}-{size - 1}/{size}"})
        except DeliveryError:
            result = (503, {}, b"")
        if result[0] == 401:
            headers = bearer(refresh())
        if result[0] in RETRYABLE | {401}:
            time.sleep(2 ** attempt)
            result = request(GOOGLE, "PUT", path, b"", {**headers, "Content-Range": f"bytes */{size}"}, retry=True)
        if result[0] == 308:
            received = next((v for k, v in result[1].items() if k.lower() == "range"), "")
            match = re.fullmatch(r"bytes=0-(\d+)", received)
            offset = int(match[1]) + 1 if match else 0
            if offset >= size:
                raise DeliveryError("Некорректный диапазон загрузки")
            continue
        uploaded = payload(result, "YouTube загрузка")
        video_id = uploaded["id"]
        journal.write({"state": "uploaded", "id": video_id, "sha256": approval})
        print(f"YouTube private — {video_id} https://youtu.be/{video_id}")
        return
    raise DeliveryError("YouTube не завершил загрузку в текущей сессии")


def facebook_post(folder, approval, news, journal):
    message = folder / "facebook_post.txt"
    if digest(message) != approval:
        raise DeliveryError("SHA-256 текста Facebook не совпадает")
    page_id, version = setting("FB_PAGE_ID"), setting("FB_GRAPH_API_VERSION")
    if not page_id.isdecimal() or not re.fullmatch(r"v\d+\.\d+", version):
        raise DeliveryError("Некорректный FB_PAGE_ID или FB_GRAPH_API_VERSION")
    headers = bearer(setting("FB_PAGE_ACCESS_TOKEN"))
    identity = payload(request("graph.facebook.com", "GET", f"/{version}/me?fields=id,username",
                               headers=headers, retry=True), "Facebook /me")
    if identity.get("id") != page_id or identity.get("username", "").lower() != "romagumwork":
        raise DeliveryError("Токен Facebook не подтверждает Page romagumwork")
    if not journal.reserve():
        return
    data = urlencode({"message": message.read_text(encoding="utf-8"), "link": news["archive"]}).encode()
    result = request("graph.facebook.com", "POST", f"/{version}/{page_id}/feed", data,
                     {**headers, "Content-Type": "application/x-www-form-urlencoded"})
    post_id = payload(result, "Facebook feed")["id"]
    journal.write({"state": "posted", "id": post_id})
    print(f"Facebook Page — {post_id}")


def telegram(news, folder):
    token = setting("EBASHIT_TOKEN")
    if not re.fullmatch(r"\d+:[\w-]+", token):
        raise DeliveryError("Некорректный формат EBASHIT_TOKEN")
    chat = setting("OWNER_CHAT_ID")
    mp4 = "MP4 готов" if (folder / "qa.json").is_file() else "готовы тексты"
    updates = []
    for key, label in (("youtube", "YouTube private"), ("facebook", "Facebook Page")):
        record = Journal(key, news["sha256"]).read() or {}
        state = "выполнено" if record.get("id") else record.get("error", "ожидает сверки" if record else "ожидает запуска")
        updates.append(f"{label} — {state}")
    text = "\n".join([f"SW75 · {news['date']} · {mp4}", news["archive"], *updates])
    phase = sha256(text.encode()).hexdigest()[:24]
    journal = Journal(f"telegram-{phase}", news["sha256"])
    if not journal.reserve():
        return
    body = urlencode({"chat_id": chat, "text": text}).encode()
    result = payload(request("api.telegram.org", "POST", f"/bot{token}/sendMessage", body,
                             {"Content-Type": "application/x-www-form-urlencoded"}), "Telegram")
    journal.write({"state": "sent", "id": str(result["result"]["message_id"])})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("channel", choices=("youtube", "facebook", "telegram"))
    parser.add_argument("folder", type=Path)
    parser.add_argument("--approved-sha")
    args = parser.parse_args()
    flags = {"youtube": "YOUTUBE_UPLOAD_ENABLED", "facebook": "FB_POST_ENABLED",
             "telegram": "TELEGRAM_NOTIFY_ENABLED"}
    if os.getenv(flags[args.channel]) != "true":
        print(f"Dry-run: {flags[args.channel]} выключен")
        return
    if args.channel == "youtube" and os.getenv("YOUTUBE_PUBLISH_ENABLED", "false") != "false":
        raise DeliveryError("Этот загрузчик поддерживает режим YouTube private")
    if args.channel != "telegram" and not re.fullmatch(r"[0-9a-f]{64}", args.approved_sha or ""):
        raise DeliveryError("Требуется явное подтверждение SHA-256")
    news = json.loads((args.folder / "news.json").read_text(encoding="utf-8"))
    event = validate(news)
    if event != news["sha256"]:
        raise DeliveryError("Редакционный хэш не совпал")
    if news["archive"] != f"{SITE}archive/{news['date']}.html":
        raise DeliveryError("Адрес архива не совпал")
    if args.channel == "telegram":
        telegram(news, args.folder)
        return
    journal = Journal(args.channel, event)
    try:
        if args.channel == "facebook":
            facebook_post(args.folder, args.approved_sha, news, journal)
        else:
            youtube_upload(args.folder, args.approved_sha, journal)
    except DeliveryError as error:
        if journal.blob:
            try:
                journal.write({"state": "pending", "error": str(error)})
            except DeliveryError:
                pass
        raise


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except DeliveryError as error:
        raise SystemExit(str(error)) from None
    except (OSError, ValueError, KeyError):
        raise SystemExit("Не удалось прочитать пакет или ответ сервиса. Проверьте журнал.") from None

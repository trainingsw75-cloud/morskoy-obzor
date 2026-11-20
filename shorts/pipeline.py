
"""Prepare a reviewed Moscow edition and optionally render a mobile preview."""
import argparse
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
from urllib.parse import urlsplit
import wave

ROOT = Path(__file__).resolve().parents[1]
MOSCOW = "Europe/Moscow"
SITE = "https://trainingsw75-cloud.github.io/morskoy-obzor/"
INDUSTRIES = {"shipping", "tankers", "LNG", "offshore/FPSO", "shipbuilding", "crew safety", "incidents"}
MODEL = "ru_RU-dmitri-medium.onnx"
MODEL_HASHES = {
    MODEL: "9ed9941bac7373f2ec1be36627e0e71a349a59ca5522d4363fd50701db05193a",
    MODEL + ".json": "c3741c9c1558fc38e902fd80d39155c32a7d019d7d17e3a43eac66b7bd3fbe09",
}
VOICE_CARD = ("https://huggingface.co/rhasspy/piper-voices/blob/"
              "2f358b6a560e3a913ebf509b2f3344f20b367f94/ru/ru_RU/dmitri/medium/MODEL_CARD")
VOICE_CREDIT = ("Piper Dmitri (rhasspy / Open Home Foundation). "
                "Hi-Fi TTS — Bakhturina, Lavrukhin, Ginsburg, Jia (CC BY 4.0). "
                "Русская адаптация и синтез. https://www.openslr.org/109/")


def digest(path):
    with path.open("rb") as file:
        from hashlib import file_digest
        return file_digest(file, "sha256").hexdigest()


def validate(edition):
    day = date.fromisoformat(edition["date"])
    checked = datetime.fromisoformat(edition["verified_at"])
    if (edition["timezone"] != MOSCOW or checked.utcoffset() != timedelta(hours=3)
            or checked.date() != day or edition["confidence"] != "high"
            or edition["industry"] not in INDUSTRIES or edition["media_permission"] != "original-graphics-only"):
        raise ValueError("Редакционный пакет требует подтверждения")
    if not re.fullmatch(r"[a-z0-9-]{5,100}", edition["event"]):
        raise ValueError("Некорректный ID события")
    sources = {}
    for source in edition["sources"]:
        url = urlsplit(source["url"])
        host = url.hostname or ""
        if (url.scheme != "https" or not host or url.username or url.password
                or any(host == domain or host.endswith("." + domain)
                       for domain in ("youtube.com", "youtu.be", "facebook.com", "t.me"))
                or source["kind"] not in {"primary", "independent"}
                or date.fromisoformat(source["date"]) > checked.date()
                or source["id"] in sources):
            raise ValueError("Недопустимый источник")
        sources[source["id"]] = source
    if not any(s["kind"] == "primary" for s in sources.values()):
        raise ValueError("Требуется первичный источник")
    if not all(edition[key].strip() for key in ("headline", "summary", "commentary")):
        raise ValueError("Неполный пакет")
    scenes = edition["scenes"]
    if not 3 <= len(scenes) <= 12 or not any(scene[2] in sources for scene in scenes):
        raise ValueError("Требуются сцены с источниками")
    for title, speech, evidence in scenes:
        if (not 1 <= len(title.strip()) <= 42 or evidence not in {*sources, "editorial"}
                or any(not 1 <= len(phrase.strip()) <= 105 for phrase in speech.split(" | "))):
            raise ValueError("Реплика без подтверждения")
    return sha256(edition["event"].encode()).hexdigest()


def write(path, value):
    path.write_text(value.rstrip() + "\n", encoding="utf-8")


def metadata(edition, key, folder):
    archive = f"{SITE}archive/{edition['date']}.html"
    refs = "\n".join(f"{s['id']} ({s['date']}) — {s['url']}" for s in edition["sources"])
    post = f"{edition['headline']}\n\n{edition['summary']}\n{edition['commentary']}\n\n{refs}\n\n{archive}"
    write(folder / "news.json", json.dumps({**edition, "sha256": key, "archive": archive}, ensure_ascii=False, indent=2))
    write(folder / "facebook_post.txt", post)
    write(folder / "telegram_post.txt", post)
    write(folder / "youtube_title.txt", edition["headline"] + " #Shorts")
    write(folder / "youtube_description.txt", post + "\n\nГрафика SW75. Синтетическая озвучка. " + VOICE_CREDIT)
    write(folder / "youtube_tags.json", json.dumps(["морские новости", "SW75", edition["industry"]], ensure_ascii=False))
    write(folder / "attribution.md", f"# Источники\n\n{refs}\n\nГрафика SW75. {VOICE_CREDIT}\n\n"
          f"[Карточка модели]({VOICE_CARD}) · [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)\n")
    write(folder / "shorts_script.md", f"# {edition['headline']}\n\n" + "\n\n".join(
        f"### {title} · {evidence}\n\n{speech.replace(' | ', ' ')}" for title, speech, evidence in edition["scenes"]))


def command(*args, cwd=None):
    result = subprocess.run(args, cwd=cwd, check=True, capture_output=True, timeout=240)
    return result.stdout.decode("utf-8"), result.stderr.decode("utf-8", errors="replace")


def srt_time(frames):
    seconds, ms = divmod(round(frames * 1000 / 30), 1000)
    minutes, sec = divmod(seconds, 60)
    return f"00:{minutes:02}:{sec:02},{ms:03}"


def synthesize(edition, model, folder):
    clips = []
    for index, (title, speech, _) in enumerate(edition["scenes"]):
        # Phrase-sized captions use each phrase's measured PCM length.
        phrases = speech.split(" | ")
        for number, phrase in enumerate(phrases):
            source = folder / "speech.txt"
            wav = folder / "clip.wav"
            write(source, phrase)
            command(sys.executable, "-m", "piper", "-m", str(model), "-i", str(source),
                    "-f", str(wav), "--length-scale", "1.15")
            with wave.open(str(wav), "rb") as clip:
                if (clip.getframerate(), clip.getsampwidth(), clip.getnchannels()) != (22050, 2, 1):
                    raise ValueError("Неожиданный PCM Piper")
                data = clip.readframes(clip.getnframes())
            frames = (len(data) // 2 + 734) // 735 + (9 if number == len(phrases) - 1 else 2)
            clips.append((index, title, phrase, frames, data))
    return clips


def draw_card(path, font_path, index, total, title, caption):
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1080, 1920), "#081d2a")
    draw = ImageDraw.Draw(image)
    accent, white, muted = "#68f1ce", "#eff8fa", "#a8c6d1"
    small = ImageFont.truetype(str(font_path), 29)
    heading = ImageFont.truetype(str(font_path), 52)
    subtitle = ImageFont.truetype(str(font_path), 46)
    for x in range(0, 1080, 90):
        draw.line((x, 0, x, 1920), fill="#102938")
    for y in range(0, 1920, 90):
        draw.line((0, y, 1080, y), fill="#102938")
    draw.rectangle((72, 150, 128, 157), fill=accent)
    draw.text((72, 180), "SW75 MARITIME DIGITAL LAB", font=small, fill=accent)
    while draw.textlength(title, font=heading) > 820 and heading.size > 28:
        heading = ImageFont.truetype(str(font_path), heading.size - 2)
    draw.text((72, 328), title, font=heading, fill=white)
    draw.rounded_rectangle((72, 486, 906, 1050), radius=28, fill="#102f40")
    draw.line((121, 702, 850, 702), width=16, fill=muted)
    for x in (245, 725):
        draw.line((x, 708, x, 899), width=16, fill=muted)
    for y in (905, 925, 945):
        draw.line((107, y, 871, y), fill="#407582", width=2)
    x = 132 + index * 46
    draw.polygon([(x, 830), (x + 300, 830), (x + 250, 880), (x + 30, 880)], fill=accent)
    draw.rectangle((x + 39, 792, x + 130, 831), fill=accent)
    draw.text((108, 982), "УСЛОВНАЯ СХЕМА", font=small, fill=muted)
    draw.rounded_rectangle((72, 1127, 906, 1504), radius=28, fill="#102f40")
    while max(draw.textlength(line, font=subtitle) for line in caption.splitlines()) > 750:
        subtitle = ImageFont.truetype(str(font_path), subtitle.size - 2)
    draw.multiline_text((110, 1195), caption, font=subtitle, fill=white, spacing=18)
    draw.rectangle((72, 1588, 906, 1593), fill="#244353")
    draw.rectangle((72, 1588, 72 + 834 * (index + 1) // total, 1593), fill=accent)
    image.save(path)


def qa(video):
    out, _ = command("ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video))
    result = json.loads(out)
    visual, audio = result["streams"]
    expected = {"codec_name": "h264", "width": 1080, "height": 1920, "pix_fmt": "yuv420p", "r_frame_rate": "30/1"}
    video_duration, audio_duration = float(visual["duration"]), float(audio["duration"])
    if (any(visual[k] != v for k, v in expected.items()) or audio["codec_name"] != "aac"
            or not 45 <= video_duration <= 60 or abs(video_duration - audio_duration) > 0.10
            or not 250_000 <= video.stat().st_size <= 60_000_000):
        raise ValueError("MP4 не прошёл проверку формата и длительности")
    _, log = command("ffmpeg", "-hide_banner", "-nostdin", "-xerror", "-i", str(video),
                     "-af", "volumedetect,silencedetect=n=-40dB:d=1.8", "-f", "null", "-")
    peak = re.search(r"max_volume: ([-\d.]+) dB", log)
    mean = re.search(r"mean_volume: ([-\d.]+) dB", log)
    if not peak or not mean or float(peak[1]) > -0.1 or float(mean[1]) < -33 or "silence_start:" in log:
        raise ValueError("В MP4 обнаружены тишина или недопустимый уровень речи")
    return {"video_seconds": video_duration, "audio_seconds": audio_duration, "bytes": video.stat().st_size,
            "peak_db": float(peak[1]), "mean_db": float(mean[1]), "sha256": digest(video)}


def render(edition, folder, model_dir, font):
    model = (model_dir / MODEL).resolve()
    for name, expected in MODEL_HASHES.items():
        if digest(model.parent / name) != expected:
            raise ValueError(f"SHA-256 не совпал для {name}")
    with tempfile.TemporaryDirectory(dir=folder) as temporary:
        work = Path(temporary).resolve()
        clips = synthesize(edition, model, work)
        total_frames = sum(clip[3] for clip in clips) + 30
        if not 45 * 30 <= total_frames <= 60 * 30:
            raise ValueError(f"Сценарий занимает {total_frames / 30:.2f} с. Исправьте длину реплик.")
        subtitles, manifest = [], []
        start = 0
        with wave.open(str(folder / "narration.wav"), "wb") as audio:
            audio.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
            for number, (index, title, phrase, frames, data) in enumerate(clips):
                end = start + frames
                wrapped = "\n".join(textwrap.wrap(phrase, 29))
                subtitles.append(f"{number + 1}\n{srt_time(start)} --> {srt_time(end)}\n{wrapped}")
                start = end
                if number == len(clips) - 1:
                    frames += 30
                audio.writeframes(data + bytes(frames * 1470 - len(data)))
                draw_card(work / "card.png", font, index, len(edition["scenes"]), title, wrapped)
                name = f"clip{number:02}.mp4"
                command("ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-loop", "1",
                        "-framerate", "30", "-i", "card.png", "-vf", "fade=in:s=0:n=4",
                        "-frames:v", str(frames), "-c:v", "libx264", "-threads", "3",
                        "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", name, cwd=work)
                manifest.append(f"file '{name}'")
        write(work / "concat.txt", "\n".join(manifest))
        write(folder / "subtitles.srt", "\n\n".join(subtitles))
        video = folder / "shorts.mp4"
        command("ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "concat",
                "-i", "concat.txt", "-i", str((folder / "narration.wav").resolve()),
                "-c:v", "copy", "-af", "volume=-3dB", "-c:a", "aac", "-ar", "48000", "-b:a", "128k",
                "-movflags", "+faststart", str(video.resolve()), cwd=work)
        report = qa(video)
        write(folder / "qa.json", json.dumps(report, indent=2))
        write(folder / "SHA256SUMS", f"{report['sha256']}  shorts.mp4")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("day", type=date.fromisoformat)
    parser.add_argument("--output", type=Path, default=ROOT / "shorts-output")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--model-dir", type=Path, default=ROOT / "youtube-news-drafts")
    parser.add_argument("--font", type=Path, default=Path("C:/Windows/Fonts/arialbd.ttf" if sys.platform == "win32" else
                                                       "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
    args = parser.parse_args()
    source = ROOT / "shorts" / "editions" / f"{args.day}.json"
    if not source.is_file():
        print(f"{args.day}: подтверждённого пакета нет")
        return
    edition = json.loads(source.read_text(encoding="utf-8"))
    if edition["date"] != args.day.isoformat() or not (ROOT / "docs" / "archive" / f"{args.day}.html").is_file():
        raise ValueError("Нет соответствующей сборки архива")
    key = validate(edition)
    for other in source.parent.glob("*.json"):
        if other != source and json.loads(other.read_text(encoding="utf-8"))["event"] == edition["event"]:
            raise ValueError("Событие уже закреплено за другой датой")
    args.output.mkdir(parents=True, exist_ok=True)
    folder = args.output / (f"{args.day}" if args.render else f"{args.day}-metadata")
    if folder.exists():
        raise SystemExit(f"Пакет уже подготовлен: {folder}")
    with tempfile.TemporaryDirectory(dir=args.output) as staging:
        package = Path(staging) / "package"
        package.mkdir()
        metadata(edition, key, package)
        if args.render:
            render(edition, package, args.model_dir, args.font)
        package.rename(folder)
    print(folder)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.stderr.decode("utf-8", errors="replace")[-1500:])

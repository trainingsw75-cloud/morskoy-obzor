
# SW75 Maritime Digital Lab

Ветка `youtube-news-drafts` добавляет редакционный пакет, пайплайн Shorts и ручную доставку через официальные API. Действующий утренний обзор работает по прежнему расписанию 07:40 МСК. Ссылки из его ленты служат сигналами для редактора.

## Первый выпуск и локальный запуск

Для выпуска о контакте балкера Strategic Alliance с мостом использованы [релиз MPA от 13 ноября](https://www.mpa.gov.sg/media-centre/details/singapore-registered-vessel-strategic-alliance-came-into-contact-with-bridge-in-new-jersey-no-injuries-reported) и [отчёт NTSB с примером употребления термина allision](https://www.ntsb.gov/investigations/AccidentReports/Reports/MAR1302.pdf). Силуэт судна и моста создан программно.

На этом ПК, в обычном PowerShell из корня репозитория, при уже установленных Python, FFmpeg, Pillow, Piper и локальной модели:

```powershell
python shorts/pipeline.py 2026-11-20 --render --output shorts-output/rebuild
```

Готовый предпросмотр находится в `shorts-output/2026-11-20/shorts.mp4`. По USB скопируйте его в Downloads на Galaxy S25 и откройте в Галерее. Если Telegram «Избранное» уже используется для приватной передачи, отправьте MP4 с ПК как файл. Рядом лежат `narration.wav`, `subtitles.srt`, `qa.json`, `SHA256SUMS`, сценарий и описания. Оцените произношение названий, паузы, читаемость и финальную реплику.

Команды для проверки без аккаунтов:

```powershell
python -m unittest discover -s tests -v
python shorts/pipeline.py 2026-11-20 --output shorts-output/check
python shorts/deliver.py youtube shorts-output/2026-11-20
python shorts/deliver.py facebook shorts-output/2026-11-20
python shorts/deliver.py telegram shorts-output/2026-11-20
```

На чистой машине установите FFmpeg с libx264, Python 3.12 или 3.13, зависимости командой `python -m pip install -r shorts/requirements-render.txt` и модель командой `python shorts/fetch_model.py .shorts-models`. Для рендера добавьте `--model-dir .shorts-models`. Windows использует Arial Bold, Linux использует DejaVu Sans Bold. Linux-рендер выдаёт тот же формат, его SHA-256 может отличаться от Windows.

Для следующего выпуска редактор добавляет `shorts/editions/YYYY-MM-DD.json` по образцу. Укажите стабильный ID события, московское время проверки, источники MPA/другого уполномоченного органа, допустимые визуалы и отдельно обозначьте авторский комментарий. Символы ` | ` отделяют естественные паузы синтеза. Для спорных фактов требуется независимое подтверждение или официальная первичная документация, а исход каждого расследования берётся из конкретного источника на указанную дату. Код проверяет структуру и ссылки, а оценку достоверности выполняет редактор. При отсутствии пакета на дату обработка завершается штатно. Все внешние действия по умолчанию выключены.

## Панель на телефоне

- [Утренний обзор и архив](https://trainingsw75-cloud.github.io/morskoy-obzor/)
- [Запуски GitHub Actions](https://github.com/trainingsw75-cloud/morskoy-obzor/actions)
- YouTube Studio для Android, канал @SW75MaritimeDigitalLab
- [Facebook Page romagumwork](https://www.facebook.com/romagumwork/)

Настройка GitHub, OAuth и восстановление описаны в [OPERATIONS_RU.md](OPERATIONS_RU.md).

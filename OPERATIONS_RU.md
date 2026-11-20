
# Эксплуатация SW75 Shorts

## GitHub Actions

`shorts-preview.yml` срабатывает после успешного «Утреннего морского обзора» из main своего репозитория и вычисляет дату по МСК на момент запуска исходного workflow. Ручной запуск принимает дату готового архива. Расписание остаётся в `build.yml`, GitHub может запускать запланированную задачу с задержкой. После попадания workflow в ветку по умолчанию подготовка выполняется на GitHub-hosted runner при выключенном ПК. Проверяйте Summary и дату архива. Для каждого нового выпуска нужен проверенный редакционный JSON.

Публичный репозиторий позволяет читателям скачивать Actions artifacts. Здесь предпросмотр публикует в Summary только сводку QA. Полные файлы и доставку поддерживает приватный репозиторий с этим кодом и архивом. Для запуска там вручную обновите main из проверенной ветки и перенесите нужный архив. Автоматическая синхронизация публичного обзора с приватным репозиторием потребует отдельной настройки. В приватном preview поставьте `VIDEO_RENDER_ENABLED=true`, скачайте ZIP из Artifacts в течение семи дней и распакуйте его в «Мои файлы» на телефоне.

## Авторизация и публикации

В приватном репозитории заведите environment `shorts-delivery` с ограничением на main и, если тариф поддерживает, подтверждением владельца. Используйте отдельную ветку `sw75-journal`, переменную `SHORTS_JOURNAL_BRANCH` и секрет `SHORTS_JOURNAL_TOKEN` с правом Contents Read/Write только для этого приватного репозитория. Журнал хранит статусы и ID, а также закрывает повторную отправку при неопределённом исходе. При локальном запуске задайте `SHORTS_JOURNAL_REPOSITORY` в формате owner/repo. Токены вводятся только в GitHub Secrets или локально в безопасной среде.

| Действие | Variables | Secrets |
|---|---|---|
| YouTube private | `YOUTUBE_UPLOAD_ENABLED=true`, `YOUTUBE_PUBLISH_ENABLED=false`, `YT_CHANNEL_ID` | `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` |
| Facebook Page | `FB_POST_ENABLED=true`, `FB_PAGE_ID`, `FB_GRAPH_API_VERSION` | `FB_PAGE_ACCESS_TOKEN` |
| Telegram владельцу | `TELEGRAM_NOTIFY_ENABLED=true` | `EBASHIT_TOKEN`, `OWNER_CHAT_ID` |

**YouTube.** В Google Cloud включите YouTube Data API v3, настройте OAuth consent screen и создайте OAuth-клиент. Войдите в аккаунт владельца и выберите брендовый канал @SW75MaritimeDigitalLab. Через официальный [OAuth 2.0 Playground](https://developers.google.com/oauthplayground/) с собственным OAuth-клиентом типа Web application и redirect URI `https://developers.google.com/oauthplayground` запросите offline-доступ для `https://www.googleapis.com/auth/youtube.upload`, обменяйте код на refresh token и сохраните три строки OAuth в Secrets. Проверьте ID канала в Studio и задайте `YT_CHANNEL_ID`. Для External в статусе Testing refresh token может истекать через семь дней. Если `channels.list?mine=true` отклонит upload-only grant из-за scope, после проверки текста ошибки перевыпустите тот же grant с дополнительным `youtube.readonly`. Код сравнивает OAuth-канал с ID до начала загрузки. После отдельного разрешения на первую private-загрузку запустите Shorts delivery для youtube, укажите ID успешного private preview и SHA-256 просмотренного MP4. Поддерживается только режим private. Публичный выпуск требует отдельного согласования прав, канала и конкретного видео. [Документация videos.insert](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert).

**Facebook.** В Meta назначьте владельцу задачу `CREATE_CONTENT` для romagumwork и получите Page Access Token через официальный flow с `pages_manage_posts` и `pages_read_engagement`. При выборе страницы через `/me/accounts` понадобятся `pages_show_list` и соответствующие роли приложения, режим доступа и App Review. В существующем Facebook preview значение `check_access=false` показывает текст. При `check_access=true` он сопоставляет ID. Новый клиент дополнительно сверяет username romagumwork через /me. Согласуйте точный SHA-256 `facebook_post.txt` и запустите Shorts delivery для facebook. Публикуется текст и ссылка на архив. [Meta Page Feed](https://developers.facebook.com/docs/graph-api/reference/page/feed/).

**Telegram.** Сохраните действующие секреты бота и личного чата владельца в приватном environment. Вариант telegram отправляет статус готовности пакета, ссылку на архив и состояния YouTube и Facebook из приватного журнала. Запуск для того же набора состояний пропускается. Старое оповещение об ошибках обзора остаётся в build.yml. Ошибки до записи в журнал видны в Actions.

## Ошибки, остановка и расходы

При 401 проверьте срок токена или grant, при 403 — настройки прав и квоту. При 429/5xx безопасные чтения повторяются. YouTube проверяет принятые байты той же сессии. Для Facebook и Telegram запрос с неопределённым исходом остаётся в состоянии pending. Сначала сверьте страницу, Studio или личный чат. Если результат есть, запишите ID в журнал. Если оператор подтвердил отсутствие операции, он может очистить конкретную pending-запись отдельным коммитом и снова явно запустить доставку. После сбоя загрузки YouTube не переносите URL сессии в документы и логи.

Для остановки выставьте флаги в false. Локальный рендер использует свободные инструменты. Платные API не используются. Минуты и хранилище приватного Actions учитываются в лимитах плана GitHub. Проверьте лимиты и настройки расходов перед запуском приватной автоматики.

Голос закреплён по SHA-256 модели и конфигурации. В карточке Dmitri указан датасет CC0, база Hi-Fi TTS предоставлена по CC BY 4.0, атрибуция включена в пакет. Перед публичным выпуском владелец подтверждает достаточность условий для коммерческого использования конкретного синтеза и принимает озвучку после прослушивания. [Карточка Dmitri](https://huggingface.co/rhasspy/piper-voices/blob/2f358b6a560e3a913ebf509b2f3344f20b367f94/ru/ru_RU/dmitri/medium/MODEL_CARD) · [Hi-Fi TTS](https://www.openslr.org/109/).

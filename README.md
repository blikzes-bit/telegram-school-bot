# 🎓 Telegram School Bot

![Python](https://img.shields.io/badge/Python-3.12+-blue?logo=python&logoColor=white)
![Aiogram](https://img.shields.io/badge/Aiogram-3.x-2CA5E0?logo=telegram&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.x-D71F00?logo=sqlite&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-multi--arch-2496ED?logo=docker&logoColor=white)
![CI](https://github.com/blikzes-bit/telegram-school-bot/actions/workflows/docker-publish.yml/badge.svg)
![License](https://img.shields.io/badge/License-MIT-green)

Телеграм-бот для школьного класса на **Python + aiogram 3**. Класс один раз настраивает расписание уроков и время звонков, а дальше бот сам напоминает о домашних заданиях и уроках на завтра — прямо в чат.

---

## ✨ Возможности

| | |
|---|---|
| 🚀 **Онбординг** | Пошаговая настройка: количество уроков, время звонков, расписание на каждый день недели (с опциональной субботой) |
| 📚 **Сегодня** | Сводка на день: уроки сегодня, домашка к сдаче, просроченные задания |
| 📅 **Расписание** | Просмотр расписания по дням недели с указанием времени уроков |
| 📝 **Домашние задания** | Добавление, редактирование и отметка о выполнении домашки по предметам и датам, архив выполненного |
| ⏰ **Напоминания** | Ежедневная рассылка по расписанию: домашка на завтра + просроченные задания + уроки на завтра |
| ⚙️ **Настройки** | Индивидуальное время напоминаний для чата, включение/отключение каждого типа напоминаний, полный сброс настроек |
| 💬 **Личка и группы** | Работает как в личном чате, так и в общем чате класса — настройки хранятся отдельно на каждый `chat_id` |

Под капотом — конечный автомат (FSM) для диалогов, автоматическое разбиение длинных сообщений на части (лимит Telegram в 4096 символов), безопасное экранирование Markdown и планировщик задач на APScheduler с учётом часового пояса.

---

## 🏗️ Архитектура

```
telegram-school-bot/
├── bot.py             # Точка входа: инициализация бота, роутеров и планировщика
├── config.py          # Конфигурация из переменных окружения
├── utils.py           # Разбиение сообщений, экранирование Markdown и др.
├── handlers/          # Обработчики диалогов и команд
│   ├── onboarding.py  #   первичная настройка расписания
│   ├── today.py       #   сводка на сегодня
│   ├── schedule.py    #   просмотр расписания
│   ├── homework.py    #   CRUD домашних заданий
│   ├── settings.py    #   настройки напоминаний
│   └── common.py      #   /help, /cancel
├── keyboards/         # Reply/inline клавиатуры
├── database/          # SQLAlchemy-модели и async-доступ к БД
├── services/
│   └── scheduler.py   #   ежедневные напоминания через APScheduler
└── tests/             # pytest + pytest-asyncio: БД, хендлеры, планировщик
```

**Стек:** aiogram 3 (async), SQLAlchemy 2 + aiosqlite (async ORM над SQLite), APScheduler, pytz, python-dotenv.

---

## ⚙️ Установка и запуск

### Локально

```bash
git clone https://github.com/blikzes-bit/telegram-school-bot.git
cd telegram-school-bot

python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env          # укажи свой BOT_TOKEN

python bot.py
```

### Docker

Готовый мультиархитектурный образ (`amd64` + `arm64`) публикуется в GHCR при каждом пуше в `main`:

```bash
docker run -d \
  --name school-bot \
  -e BOT_TOKEN=your_token_here \
  -e TIMEZONE=Europe/Kiev \
  -v school_bot_data:/data \
  ghcr.io/blikzes-bit/telegram-school-bot:latest
```

База данных хранится в `/data/school_bot.db` — том нужен для сохранения данных между перезапусками контейнера.

### Переменные окружения

| Переменная | Обязательна | По умолчанию | Описание |
|---|---|---|---|
| `BOT_TOKEN` | ✅ | — | Токен бота от [@BotFather](https://t.me/BotFather) |
| `TIMEZONE` | ❌ | `Europe/Kiev` | Часовой пояс для расчёта времени напоминаний |
| `DATABASE_URL` | ❌ | `sqlite+aiosqlite:///school_bot.db` | Строка подключения к БД |

---

## 🧪 Тесты

```bash
pytest
```

Тесты покрывают работу с БД, редактирование домашних заданий, валидацию времени, разбиение сообщений и логику планировщика напоминаний. GitHub Actions прогоняет их автоматически перед сборкой и публикацией Docker-образа.

---

## 🤝 Вклад

PR и issue приветствуются — если нашёл баг или есть идея новой функции, заводи issue или открывай pull request.

## 📄 Лицензия

Проект распространяется по лицензии MIT.

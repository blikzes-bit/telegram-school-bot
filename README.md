# 🤖 Telegram School Bot

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python)
![Aiogram](https://img.shields.io/badge/Aiogram-3.x-2CA5E0)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-Active-success)

# 🤖 Telegram School Bot

Telegram-бот для школы, написанный на **Python** с использованием **aiogram**.  
Позволяет автоматизировать взаимодействие учеников и администрации, отправлять напоминания, работать с базой данных и управлять пользователями.

---

## ✨ Возможности

- 📚 Система регистрации пользователей
- 🔔 Напоминания
- 🗂️ Работа с базой данных
- 👤 Личный кабинет пользователя
- ⚙️ FSM (Finite State Machine)
- 📝 Поддержка Markdown
- 🚀 Простая настройка через `.env`

---

## 📂 Структура проекта

```
telegram-school-bot/
├── database/        # Работа с БД
├── handlers/        # Обработчики команд и сообщений
├── keyboards/       # Клавиатуры Telegram
├── services/        # Бизнес-логика
├── bot.py           # Точка входа
├── config.py        # Конфигурация
├── utils.py         # Вспомогательные функции
├── requirements.txt # Зависимости
└── .env.example     # Пример конфигурации
```

---

## ⚙️ Установка

### 1. Клонировать репозиторий

```bash
git clone https://github.com/USERNAME/telegram-school-bot.git
cd telegram-school-bot
```

### 2. Создать виртуальное окружение

Windows

```bash
python -m venv venv
venv\Scripts\activate
```

Linux/macOS

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Установить зависимости

```bash
pip install -r requirements.txt
```

### 4. Создать `.env`

```bash
cp .env.example .env
```

или вручную заполнить необходимые значения.

---

## ▶️ Запуск

```bash
python bot.py
```

---

## 🛠 Используемые технологии

- Python 3.11+
- aiogram
- SQLite
- asyncio

---

## 📋 TODO

- [ ] Панель администратора
- [ ] Логи действий
- [ ] Docker
- [ ] Unit-тесты
- [ ] CI/CD

---

## 🤝 Contribution

Pull Requests и предложения приветствуются.

---

## 📄 License

Проект распространяется под лицензией MIT.

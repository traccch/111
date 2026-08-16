#!/usr/bin/env bash
# Запуск бота одной командой: ./run.sh
# Скрипт сам создаст окружение, поставит зависимости, спросит токен и включит бота.

set -euo pipefail
cd "$(dirname "$0")"

BOLD=$'\033[1m'; GREEN=$'\033[32m'; RED=$'\033[31m'; DIM=$'\033[2m'; OFF=$'\033[0m'

say()  { printf "%s\n" "${BOLD}$1${OFF}"; }
ok()   { printf "%s\n" "${GREEN}✓${OFF} $1"; }
fail() { printf "%s\n" "${RED}✗ $1${OFF}" >&2; exit 1; }

# ------------------------------------------------------------------ 1. Python

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    fail "Нужен Python 3.10 или новее.
   macOS:  brew install python
   Ubuntu: sudo apt install python3 python3-venv
   Или скачай с https://www.python.org/downloads/ и запусти скрипт заново."
fi
ok "Python: $($PYTHON --version)"

# ------------------------------------------------------------ 2. Окружение

if [ ! -d .venv ]; then
    say "Создаю виртуальное окружение…"
    "$PYTHON" -m venv .venv || fail "Не удалось создать окружение.
   На Ubuntu/Debian обычно помогает: sudo apt install python3-venv"
fi

VENV_PY=".venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY=".venv/Scripts/python.exe"   # Git Bash под Windows
[ -x "$VENV_PY" ] || fail "Окружение .venv повреждено — удали папку .venv и запусти скрипт заново."

say "Проверяю зависимости…"
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r requirements.txt || fail "Не удалось установить зависимости (проверь интернет)."
ok "Зависимости на месте"

# ---------------------------------------------------------------- 3. Токен

token_from_env() {
    [ -f .env ] || return 1
    grep -E '^BOT_TOKEN=' .env | tail -1 | cut -d= -f2- | tr -d ' "\r'
}

valid_token() {
    printf "%s" "$1" | grep -Eq '^[0-9]{5,}:[A-Za-z0-9_-]{30,}$'
}

TOKEN="$(token_from_env || true)"

if ! valid_token "${TOKEN:-}"; then
    echo
    say "Нужен токен бота. Где его взять (2 минуты):"
    cat <<'HOWTO'
   1. Открой в Telegram чат с @BotFather
   2. Отправь /newbot
   3. Придумай имя (любое) и username — должен заканчиваться на bot,
      например my_money_2026_bot
   4. BotFather пришлёт строку вида 8123456789:AAH-xxxxxxxxxxxxxxxxxxxxxxxxxxxx
   5. Скопируй её и вставь сюда
HOWTO
    echo
    while true; do
        printf "%s" "${BOLD}Вставь токен и нажми Enter: ${OFF}"
        if : 2>/dev/null < /dev/tty; then
            read -r TOKEN < /dev/tty || TOKEN=""
        else
            read -r TOKEN || TOKEN=""
        fi
        [ -n "$TOKEN" ] || fail "Ввод пустой.
   Впиши токен вручную: открой файл .env и замени строку BOT_TOKEN=… на свою."
        TOKEN="$(printf "%s" "$TOKEN" | tr -d ' "\r')"
        valid_token "$TOKEN" && break
        printf "%s\n" "${RED}Это не похоже на токен. Он выглядит так: 8123456789:AAH-xxxxxxxx${OFF}"
    done

    if [ -f .env ]; then
        "$VENV_PY" - "$TOKEN" <<'PY'
import pathlib, re, sys
token = sys.argv[1]
path = pathlib.Path(".env")
text = path.read_text(encoding="utf-8")
if re.search(r"(?m)^BOT_TOKEN=", text):
    text = re.sub(r"(?m)^BOT_TOKEN=.*$", f"BOT_TOKEN={token}", text)
else:
    text = text.rstrip("\n") + f"\nBOT_TOKEN={token}\n"
path.write_text(text, encoding="utf-8")
PY
    else
        cp .env.example .env
        "$VENV_PY" - "$TOKEN" <<'PY'
import pathlib, re, sys
path = pathlib.Path(".env")
path.write_text(
    re.sub(r"(?m)^BOT_TOKEN=.*$", f"BOT_TOKEN={sys.argv[1]}", path.read_text(encoding="utf-8")),
    encoding="utf-8",
)
PY
    fi
    ok "Токен сохранён в файл .env — больше спрашивать не буду"
fi

# ---------------------------------------------------------------- 4. Запуск

echo
say "Запускаю бота. Открой его в Telegram и напиши /start"
printf "%s\n\n" "${DIM}Остановить — Ctrl+C. Пока это окно открыто, бот работает.${OFF}"
exec "$VENV_PY" -m bot.main

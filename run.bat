@echo off
rem Switch console to UTF-8 before any non-ASCII text in this file,
rem otherwise cmd renders Russian messages as garbage.
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Бот учёта расходов

rem Запуск бота под Windows: двойной клик по этому файлу.
rem Скрипт сам создаст окружение, поставит зависимости, спросит токен и включит бота.

rem ------------------------------------------------------------- 1. Python
set "PYTHON="
for %%C in ("py -3" "python") do (
    %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYTHON=%%~C"
        goto :python_found
    )
)

echo.
echo   [X] Нужен Python 3.10 или новее.
echo       Скачай с https://www.python.org/downloads/
echo       ВАЖНО: при установке поставь галочку "Add Python to PATH",
echo       потом запусти этот файл заново.
echo.
pause
exit /b 1

:python_found
for /f "delims=" %%V in ('%PYTHON% --version') do echo   [OK] %%V

rem --------------------------------------------------------- 2. Окружение
if not exist ".venv" (
    echo   Создаю виртуальное окружение...
    %PYTHON% -m venv .venv
    if !errorlevel! neq 0 (
        echo   [X] Не удалось создать окружение.
        pause
        exit /b 1
    )
)

set "VENV_PY=.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo   [X] Окружение .venv повреждено — удали папку .venv и запусти файл заново.
    pause
    exit /b 1
)

echo   Проверяю зависимости...
"%VENV_PY%" -m pip install --quiet --upgrade pip
"%VENV_PY%" -m pip install --quiet -r requirements.txt
if %errorlevel% neq 0 (
    echo   [X] Не удалось установить зависимости ^(проверь интернет^).
    pause
    exit /b 1
)
echo   [OK] Зависимости на месте

rem ------------------------------------------------------------- 3. Токен
"%VENV_PY%" -c "import pathlib,re,sys; t=pathlib.Path('.env'); m=re.search(r'(?m)^BOT_TOKEN=(.+)$', t.read_text(encoding='utf-8')) if t.exists() else None; sys.exit(0 if m and re.fullmatch(r'[0-9]{5,}:[A-Za-z0-9_-]{30,}', m.group(1).strip().strip('\"')) else 1)"
if %errorlevel% equ 0 goto :run

echo.
echo   Нужен токен бота. Где его взять ^(2 минуты^):
echo     1. Открой в Telegram чат с @BotFather
echo     2. Отправь /newbot
echo     3. Придумай имя и username ^(должен заканчиваться на bot^)
echo     4. BotFather пришлёт строку вида 8123456789:AAH-xxxxxxxxxxxxxxxx
echo     5. Скопируй её и вставь сюда
echo.

:ask_token
set "TOKEN="
set /p "TOKEN=Вставь токен и нажми Enter: "
"%VENV_PY%" -c "import re,sys; sys.exit(0 if re.fullmatch(r'[0-9]{5,}:[A-Za-z0-9_-]{30,}', sys.argv[1].strip().strip('\"')) else 1)" "!TOKEN!"
if %errorlevel% neq 0 (
    echo   Это не похоже на токен. Он выглядит так: 8123456789:AAH-xxxxxxxx
    goto :ask_token
)

if not exist ".env" copy /y ".env.example" ".env" >nul
"%VENV_PY%" -c "import pathlib,re,sys; p=pathlib.Path('.env'); s=p.read_text(encoding='utf-8'); t=sys.argv[1].strip().strip('\"'); s=re.sub(r'(?m)^BOT_TOKEN=.*$', 'BOT_TOKEN='+t, s) if re.search(r'(?m)^BOT_TOKEN=', s) else s.rstrip()+'\nBOT_TOKEN='+t+'\n'; p.write_text(s, encoding='utf-8')" "!TOKEN!"
echo   [OK] Токен сохранён в файл .env — больше спрашивать не буду

:run
echo.
echo   Запускаю бота. Открой его в Telegram и напиши /start
echo   Остановить — Ctrl+C. Пока это окно открыто, бот работает.
echo.
"%VENV_PY%" -m bot.main
pause

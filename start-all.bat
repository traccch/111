@echo off
rem Switch console to UTF-8 before any non-ASCII text in this file.
chcp 65001 >nul
setlocal enabledelayedexpansion

rem Запускает всех ботов, лежащих в соседних папках: каждый — в своём окне.
rem Папкой считается та, внутри которой есть bot\main.py и run.bat.

set "ROOT=%~dp0.."
set /a COUNT=0
set /a NEEDS_TOKEN=0

echo.
echo   Ищу ботов в папке: %ROOT%
echo.

for /d %%D in ("%ROOT%\*") do (
    if exist "%%~fD\bot\main.py" if exist "%%~fD\run.bat" (
        set /a COUNT+=1
        if exist "%%~fD\.env" (
            echo   [запуск] %%~nxD
            start "Бот: %%~nxD" /D "%%~fD" cmd /k run.bat
        ) else (
            set /a NEEDS_TOKEN+=1
            echo   [пропуск] %%~nxD — нет файла .env с токеном
        )
    )
)

echo.
if %COUNT% equ 0 (
    echo   Ботов рядом не нашлось.
    echo   Положи папки с ботами в одну общую папку — например:
    echo       Рабочий стол\Боты\111
    echo       Рабочий стол\Боты\222
    echo   и запусти этот файл ещё раз.
) else (
    if %NEEDS_TOKEN% gtr 0 (
        echo   У пропущенных ботов ещё нет токена. Зайди в такую папку,
        echo   запусти run.bat один раз и вставь токен — дальше они будут
        echo   стартовать вместе со всеми.
        echo.
    )
    echo   Каждый бот работает в своём окне. Закрыть окно — остановить бота.
)

echo.
pause

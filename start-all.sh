#!/usr/bin/env bash
# Запускает всех ботов, лежащих в соседних папках, в одном окне.
# Папкой с ботом считается та, внутри которой есть bot/main.py и run.sh.
# Ctrl+C останавливает сразу всех.

set -uo pipefail
cd "$(dirname "$0")"

BOLD=$'\033[1m'; DIM=$'\033[2m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'

ROOT="$(cd .. && pwd)"
PIDS=()
NAMES=()
SKIPPED=()

printf "\n%s\n\n" "${BOLD}Ищу ботов в папке: ${ROOT}${OFF}"

for dir in "$ROOT"/*/; do
    [ -f "${dir}bot/main.py" ] && [ -f "${dir}run.sh" ] || continue
    name="$(basename "$dir")"

    # Без токена run.sh начнёт спрашивать его в общем потоке ввода — а там
    # несколько ботов сразу, разобрать ответы будет невозможно.
    if ! grep -Eq '^BOT_TOKEN=[0-9]{5,}:' "${dir}.env" 2>/dev/null; then
        SKIPPED+=("$name")
        continue
    fi

    printf "  %s\n" "${BOLD}[запуск]${OFF} $name"
    # awk с fflush, а не sed: строки должны появляться сразу, а не пачками.
    ( cd "$dir" && ./run.sh 2>&1 | awk -v p="${DIM}[$name]${OFF} " '{print p $0; fflush()}' ) &
    PIDS+=("$!")
    NAMES+=("$name")
done

if [ "${#SKIPPED[@]}" -gt 0 ]; then
    printf "\n%s\n" "${YELLOW}Пропустил (нет токена в .env): ${SKIPPED[*]}${OFF}"
    printf "%s\n" "Зайди в такую папку, запусти ./run.sh один раз и вставь токен."
fi

if [ "${#PIDS[@]}" -eq 0 ]; then
    printf "\n%s\n" "${BOLD}Запускать нечего.${OFF}"
    printf "%s\n" "Положи папки с ботами в одну общую папку и запусти файл снова."
    exit 1
fi

# Между нами и питоном есть промежуточные процессы (подоболочка, run.sh),
# поэтому гасим всё дерево, иначе бот переживёт Ctrl+C.
kill_tree() {
    local pid="$1" child
    for child in $(pgrep -P "$pid" 2>/dev/null); do
        kill_tree "$child"
    done
    kill "$pid" 2>/dev/null || true
}

stop_all() {
    trap - INT TERM
    printf "\n%s\n" "${BOLD}Останавливаю ботов…${OFF}"
    for pid in "${PIDS[@]}"; do
        kill_tree "$pid"
    done
    wait 2>/dev/null || true
    exit 0
}
trap stop_all INT TERM

printf "\n%s\n" "${BOLD}Работают: ${NAMES[*]}${OFF}"
printf "%s\n\n" "${DIM}Остановить всех — Ctrl+C.${OFF}"
wait

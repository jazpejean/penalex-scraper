#!/bin/bash
# lanzar_todo_qwen2.sh — canal Workers AI cuenta NUEVA (mariolizcanoabg,
# db30f9..., Global Key cfk_): fuerza GEMINI_API_KEY vacía y apunta las
# variables CF_AI_* a la cuenta nueva; excluye la cuenta vieja para que este
# proceso use SOLO la nueva. Corre en paralelo con lanzar_todo.sh (Gemini) y
# lanzar_todo_qwen.sh (cuenta vieja): tres canales simultáneos.
set -u
cd /home/jeanjazpe/penalex-scraper
mkdir -p logs_analizar
LOG=logs_analizar/lote_qwen2.log

echo "=== lanzar_todo_qwen2.sh (cuenta NUEVA) arrancó $(date) ===" >> "$LOG"

while true; do
    for sala in scp scon; do
        echo "--- ciclo Qwen-nueva $sala 2000-2026 $(date) ---" >> "$LOG"
        GEMINI_API_KEY= \
        CLOUDFLARE_WORKERS_AI_API_KEY="${CF_AI_API_KEY_2:-$(grep -E '^CF_AI_API_KEY_2=' /home/jeanjazpe/penalex-scraper/.env | cut -d= -f2-)}" \
        CF_AI_ACCOUNT_ID=db30f9aaab24226ac49937167e0bd440 \
        CF_AI_EMAIL=mariolizcanoabg@gmail.com \
        CF_AI_ACCOUNT_ID_2= \
        CF_AI_API_KEY_2= \
        CF_AI_EMAIL_2= \
            /home/jeanjazpe/penalex-scraper/.venv/bin/python \
            /home/jeanjazpe/penalex-scraper/analizar_batch.py \
            "$sala" 2000 2026 1000 2>&1 | tee -a "$LOG"
    done
    echo "--- ciclo Qwen-nueva completo; durmiendo 30 min ($(date)) ---" >> "$LOG"
    sleep 1800
done
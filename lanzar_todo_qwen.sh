#!/bin/bash
# lanzar_todo_qwen.sh — canal Workers AI cuenta VIEJA (a1dc6833, token cfut_):
# fuerza GEMINI_API_KEY vacía (=> proveedor workers) y EXCLUYE la cuenta nueva
# (CF_AI_ACCOUNT_ID_2="") para que este proceso use SOLO la cuenta vieja.
# Corre en paralelo con lanzar_todo.sh (Gemini) y lanzar_todo_qwen2.sh (cuenta
# nueva): tres canales simultáneos sobre el mismo pool de pendientes.
set -u
cd /home/jeanjazpe/penalex-scraper
mkdir -p logs_analizar
LOG=logs_analizar/lote_qwen.log

echo "=== lanzar_todo_qwen.sh (cuenta VIEJA) arrancó $(date) ===" >> "$LOG"

while true; do
    for sala in scp scon; do
        echo "--- ciclo Qwen-vieja $sala 2000-2026 $(date) ---" >> "$LOG"
        GEMINI_API_KEY= \
        CF_AI_ACCOUNT_ID_2= \
        CF_AI_API_KEY_2= \
        CF_AI_EMAIL_2= \
            /home/jeanjazpe/penalex-scraper/.venv/bin/python \
            /home/jeanjazpe/penalex-scraper/analizar_batch.py \
            "$sala" 2000 2026 1000 2>&1 | tee -a "$LOG"
    done
    echo "--- ciclo Qwen-vieja completo; durmiendo 30 min ($(date)) ---" >> "$LOG"
    sleep 1800
done
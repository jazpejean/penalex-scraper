#!/bin/bash
# lanzar_todo_qwen.sh — canal paralelo de Workers AI (Qwen3-30B-A3B):
# corre el MISMO lote que Gemini pero con GEMINI_API_KEY vacía, lo que fuerza
# al AnalizadorIA a usar Workers AI (cf_api_key). Ambos procesos comparten el
# pool de pendientes; el filtro analyzed_at is.null reparte el trabajo y el
# guardado es idempotente (no se duplica).
set -u
cd /home/jeanjazpe/penalex-scraper
mkdir -p logs_analizar
LOG=logs_analizar/lote_qwen.log

echo "=== lanzar_todo_qwen.sh arrancó $(date) ===" >> "$LOG"

while true; do
    for sala in scp scon; do
        echo "--- ciclo Qwen $sala 2000-2026 $(date) ---" >> "$LOG"
        GEMINI_API_KEY= \
            /home/jeanjazpe/penalex-scraper/.venv/bin/python \
            /home/jeanjazpe/penalex-scraper/analizar_batch.py \
            "$sala" 2000 2026 1000 2>&1 | tee -a "$LOG"
    done
    echo "--- ciclo Qwen completo; durmiendo 30 min ($(date)) ---" >> "$LOG"
    sleep 1800
done
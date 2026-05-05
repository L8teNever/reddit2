#!/bin/bash
set -e

OLLAMA_HOST="${OLLAMA_HOST:-http://ollama:11436}"
DEFAULT_MODEL="${OLLAMA_MODEL:-llama3.2}"

echo "==> Warte auf Ollama ($OLLAMA_HOST)..."
until curl -sf "$OLLAMA_HOST/api/tags" > /dev/null 2>&1; do
    echo "    ...noch nicht bereit, warte 3s"
    sleep 3
done
echo "==> Ollama bereit."

# Pull default model if none available
MODEL_COUNT=$(curl -s "$OLLAMA_HOST/api/tags" \
    | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('models',[])))")

if [ "$MODEL_COUNT" = "0" ]; then
    echo "==> Kein Modell gefunden. Lade '$DEFAULT_MODEL' (kann einige Minuten dauern)..."
    curl -s -X POST "$OLLAMA_HOST/api/pull" \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"$DEFAULT_MODEL\"}" \
    | python3 -u -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
        if d.get('total'):
            pct = int(d.get('completed', 0) / d['total'] * 100)
            print(f'\r    {d.get(\"status\",\"\")}: {pct}%  ', end='', flush=True)
        elif d.get('status'):
            print(f'    {d[\"status\"]}', flush=True)
    except Exception:
        pass
print()
"
    echo "==> Modell '$DEFAULT_MODEL' bereit."
else
    echo "==> Modelle bereits vorhanden ($MODEL_COUNT verfügbar)."
fi

echo "==> Starte App..."
exec python main.py serve --host 0.0.0.0

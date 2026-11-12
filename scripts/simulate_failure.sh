set -u
LB=${LB:-http://localhost:8080}

section() { echo; echo "=== $* ==="; }
dist()    { python3 scripts/load_test.py -u "$LB" -n "${1:-12}" -q; }
status()  { curl -s "$LB/lb/status" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print('algoritmo:', d['algorithm']); [print(f\"  {b['name']}: {'UP  ' if b['healthy'] else 'DOWN'} total={b['total_requests']}\") for b in d['backends']]"; }

section "1) Estado inicial";               status
section "2) Tráfego normal (3 instâncias)"; dist 12

section "3) DERRUBANDO app2 (docker stop)"; docker stop shopnow-app2 >/dev/null
echo "Disparando tráfego imediatamente (failover passivo deve evitar erros)..."
dist 12
echo "Aguardando health check ativo (5s)..."; sleep 5
status

section "4) Tráfego com app2 fora"; dist 12

section "5) RECUPERANDO app2 (docker start)"; docker start shopnow-app2 >/dev/null
echo "Aguardando health check (8s)..."; sleep 8
status
section "6) Tráfego com as 3 instâncias novamente"; dist 12

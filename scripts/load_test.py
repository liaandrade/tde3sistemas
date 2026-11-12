#!/usr/bin/env python3
"""
Gerador de carga simples (somente biblioteca padrão).
Mostra qual instância respondeu cada requisição e um resumo no final.

Exemplos:
  python scripts/load_test.py -n 30                       # 30 reqs, sequencial
  python scripts/load_test.py -n 200 -c 1 -d 0.1          # ~10 req/s (p/ ver failover ao vivo)
  python scripts/load_test.py -n 40 -c 20 -p "/slow?delay=2"   # concorrente (least_connections)
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

p = argparse.ArgumentParser()
p.add_argument("-u", "--url", default="http://localhost:8080")
p.add_argument("-p", "--path", default="/")
p.add_argument("-n", "--requests", type=int, default=30)
p.add_argument("-c", "--concurrency", type=int, default=1)
p.add_argument("-d", "--delay", type=float, default=0.0, help="pausa entre reqs (por worker)")
p.add_argument("-q", "--quiet", action="store_true")
args = p.parse_args()


def hit(i: int) -> str:
    time.sleep(args.delay)
    try:
        with urllib.request.urlopen(args.url + args.path, timeout=40) as r:
            who = json.loads(r.read()).get("instance", "?")
            code = r.status
    except urllib.error.HTTPError as e:
        who, code = f"ERRO-{e.code}", e.code
    except Exception as e:  # conexão recusada, timeout...
        who, code = f"ERRO-{type(e).__name__}", 0
    if not args.quiet:
        print(f"#{i:04d} -> {who} ({code})", flush=True)
    return who


start = time.time()
with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
    results = list(ex.map(hit, range(1, args.requests + 1)))

print("\n===== RESUMO =====")
for who, n in sorted(Counter(results).items()):
    print(f"{who:>20}: {n:4d} ({n / len(results):.0%})")
print(f"Tempo total: {time.time() - start:.2f}s")

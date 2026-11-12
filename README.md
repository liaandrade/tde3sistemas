# ShopNow — Load Balancer com FastAPI

Load balancer reverso que distribui requisições entre 3 instâncias idênticas de uma aplicação Flask, com health check ativo/passivo, retry seguro e 3 algoritmos de balanceamento.

> **Observação:** este repositório foi criado e publicado **após o projeto já estar concluído e testado localmente**. Os commits refletem a organização final do código e da documentação.

---

## Arquitetura

```
                 ┌──────────────────────────┐
  Clientes ────► │  Load Balancer (:8080)   │
                 │  FastAPI + httpx         │
                 │  • round_robin           │
                 │  • least_connections     │
                 │  • random                │
                 │  • health check + retry  │
                 └───┬──────────┬──────────┬┘
                     ▼          ▼          ▼
                  app1:5000  app2:5000  app3:5000
                  (Flask, mesmo código, INSTANCE_ID diferente)
```

- **LB**: FastAPI + httpx, algoritmos `round_robin`, `least_connections`, `random`
- **Apps**: Flask/Gunicorn, mesma imagem, `INSTANCE_ID` distingue
- **Health check ativo**: a cada 2s, `FALL_THRESHOLD=2`, `RISE_THRESHOLD=2`
- **Health check passivo**: falha de conexão remove do pool na hora
- **Retry**: apenas métodos seguros (GET/HEAD/OPTIONS)

---

## Como rodar

```bash
docker compose up --build
```

Testar com outro algoritmo:

```bash
# Linux/macOS
ALGORITHM=least_connections docker compose up --build

# Windows PowerShell
$env:ALGORITHM="least_connections"; docker compose up --build
```

### Endpoints

| Endpoint | Descrição |
|---|---|
| `GET /lb/status` | Status do pool (algoritmo, saúde, conexões, requisições) |
| `POST /lb/algorithm/{nome}` | Trocar algoritmo em runtime |
| `GET /{path}` | Proxy reverso para as instâncias |

---

## Funcionamento interno do `lb.py`

### Algoritmos

- **round_robin**: percorre `candidates` em ordem circular com `self._rr`.
- **least_connections**: escolhe `min(b.active)`; empates resolvidos por `self._rr`.
- **random**: `random.choice(candidates)`.

`pick()` incrementa `b.active` **antes** de qualquer `await` (linha 87), garantindo atomicidade dentro do event loop.

### Health check ativo

- Roda a cada `HEALTH_INTERVAL` (2s).
- `FALL_THRESHOLD=2` falhas consecutivas → `healthy=False`.
- `RISE_THRESHOLD=2` sucessos consecutivos → `healthy=True`.

### Health check passivo

Em `proxy()`, exceções `httpx.ConnectError`/`ConnectTimeout` chamam `mark_down()` imediatamente, independente do próximo check ativo.

### Retry

- Erro de conexão (requisição não chegou): reenvia para outro backend, **qualquer método**.
- Erro no meio da requisição: só reenvia se `safe_method` (GET/HEAD/OPTIONS); POST retorna 502.

### Decremento de conexão

`finally: b.active -= 1` (linha 212), garantindo que o contador não fique preso mesmo em exceção.

---

## Relatório de Testes

Ambiente: Windows 11, Docker Desktop (WSL2), PowerShell 5.1.

### Teste 1 — Round Robin

**Comando:** `python scripts/load_test.py -n 9`

**Resultado:**
```
app1: 3 (33%), app2: 3 (33%), app3: 3 (33%)
```

Sequência observada: app1 → app2 → app3 → app1 → app2 → app3 → ...

**Status:**  Aprovado

### Teste 2 — Least Connections

**Objetivo:** ocupar app1 com requisições lentas e verificar que o LB a evita.

**Tentativas no Windows:**

1. `Start-Job { curl.exe ... }` — jobs reportaram "Running" mas `active_connections` ficou 0 no `/lb/status`.
2. `Start-Job { Invoke-WebRequest ... }` — mesmo comportamento; job "Running" mas requisição não chegou ao LB.
3. Requisição lenta individual via `Measure-Command` — **10,14s** (endpoint funciona).

**Conclusão:** a lógica do `least_connections` está correta no `lb.py` (linhas 78–87 e 212), mas o disparo paralelo via PowerShell no Windows não reproduziu o cenário do guia (provável proxy WinHTTP ou comportamento do `Start-Job`). O teste fiel requer WSL ou Git Bash (bash com `&`).

**Status:**  Parcial — lógica validada por inspeção; teste de concorrência pendente de ambiente bash.

### Teste 3 — Random

**Comando:** `python scripts/load_test.py -n 300 -q`

**Resultado:**
```
app1: 100 (33%), app2: 106 (35%), app3: 94 (31%)
```

**Status:**  Aprovado

### Teste 4 — `simulate_failure.sh`

Não executado no Windows (script bash). Equivalente ao Teste 5.

**Status:**  Não executado

### Teste 5 — Failover manual

**Procedimento:** tráfego contínuo + `docker stop shopnow-app2` + `Start-Sleep 8` + `docker start shopnow-app2`.

**Evidência nos logs do LB:** app2 ausente do tráfego real entre 15:18:26 e 15:18:28, e presente novamente após 15:19:58. Nenhuma linha de erro no cliente durante a janela.

**Status:**  Comportamento observado (evidência parcial — logs truncados)

### Teste 6 — Falha suave

**Comandos:**
```powershell
curl.exe -X POST localhost:5003/admin/fail
Start-Sleep 5
curl.exe localhost:8080/lb/status   # app3: "healthy": false
python scripts/load_test.py -n 20 -q # app3 ausente do resumo (0 requisições)
curl.exe -X POST localhost:5003/admin/recover
Start-Sleep 5
curl.exe localhost:8080/lb/status   # app3: "healthy": true
```

**Resultado:** health check ativo removeu app3 do pool e a readmitiu após ~4s. Tráfego real evitou app3 enquanto doente.

**Status:**  Aprovado

### Teste 7 — Queda total

**Comandos:**
```powershell
docker stop shopnow-app1 shopnow-app2 shopnow-app3
curl.exe -i localhost:8080/
```

**Resultado:**
```
HTTP/1.1 503 Service Unavailable
{"error":"nenhuma instância saudável disponível"}
```

Recuperação: `docker start` nas três + 6s → todas `healthy: true`.

**Status:**  Aprovado

---

## Diagnóstico — Problemas encontrados no Windows

### 1. Estrutura de pastas aninhada

**Problema:** cópia do ZIP resultou em `C:\shopnow\shopnow\shopnow\` (dois níveis extras).

**Solução:** copiar o nível correto (`tde3sistemas\shopnow\shopnow\*`) para `C:\shopnow\`.

### 2. OneDrive + Docker

**Problema:** a pasta original estava no OneDrive, que trava arquivos durante o build.

**Solução:** copiar para fora do OneDrive (`C:\shopnow`).

### 3. `curl` vs `curl.exe` no PowerShell

**Problema:** `curl` é alias de `Invoke-WebRequest` no PowerShell, com sintaxe diferente.

**Solução:** usar sempre `curl.exe`.

### 4. `Start-Job` + requisições lentas (Teste 2)

**Problema:** `Start-Job` com `curl.exe` ou `Invoke-WebRequest` não manteve conexões lentas ativas no LB. O job reportava "Running" mas `active_connections` ficava 0.

**Diagnóstico:** requisição lenta individual via `Measure-Command` demorou 10,14s (endpoint OK). O problema é específico do `Start-Job` no PowerShell (provável proxy WinHTTP ou comportamento do job em background).

**Solução pendente:** executar o Teste 2 no WSL ou Git Bash, onde `curl ... &` funciona como no guia.

### 5. Docker Desktop abrindo `docker-compose.yml` sozinho

**Problema:** ao rodar comandos `docker` na pasta, o Docker Desktop abre o compose no editor padrão.

**Solução:** comportamento cosmético do Docker Desktop; fechar a janela sem salvar. Não afeta a execução.

### 6. `ALGORITHM` no PowerShell

**Problema:** `ALGORITHM=least_connections docker compose up` (sintaxe bash) não funciona.

**Solução:** `$env:ALGORITHM="least_connections"; docker compose up --build`.

---

## Limitações e próximos passos

- O LB é um ponto único de falha. Em produção, rode 2+ LBs com IP virtual (keepalived) ou use um LB gerenciado.
- O estado do LB (contadores de conexão) é local e em memória. Para escalar horizontalmente, é preciso mudar essa parte.
- Para produção, o mais indicado é HAProxy/Nginx/Envoy. Este LB serve para demonstrar os conceitos.
- Faltam TLS, rate limiting e sessões persistentes (sticky sessions, ex.: hash por IP).
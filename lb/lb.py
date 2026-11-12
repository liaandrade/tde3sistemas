"""
ShopNow - Load Balancer (camada 7 / HTTP reverse proxy).

Recursos:
  * Algoritmos: round_robin, least_connections, random (troca em tempo de execução)
  * Health check ATIVO: consulta GET /health de cada backend periodicamente
  * Health check PASSIVO: se uma requisição falha por erro de conexão, o backend
    é removido imediatamente e a requisição é reenviada a outro backend (retry)
  * Histerese: FALL_THRESHOLD falhas seguidas para sair, RISE_THRESHOLD sucessos
    seguidos para voltar (evita "flapping")
  * API de administração em /lb/status e /lb/algorithm/{nome}
"""
import asyncio
import logging
import os
import random
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import FastAPI, HTTPException, Request, Response

# ----------------------------- Configuração ---------------------------------
BACKENDS = os.getenv("BACKENDS", "app1:5000,app2:5000,app3:5000").split(",")
ALGORITHM = os.getenv("ALGORITHM", "round_robin")
HEALTH_INTERVAL = float(os.getenv("HEALTH_INTERVAL", 2))   # segundos entre checks
HEALTH_TIMEOUT = float(os.getenv("HEALTH_TIMEOUT", 1))     # timeout de cada check
FALL_THRESHOLD = int(os.getenv("FALL_THRESHOLD", 2))       # falhas p/ marcar DOWN
RISE_THRESHOLD = int(os.getenv("RISE_THRESHOLD", 2))       # sucessos p/ marcar UP
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", 35))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [LB] %(message)s")
log = logging.getLogger("lb")

# Headers "hop-by-hop" que não devem ser repassados pelo proxy
HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}
# Headers da resposta que o httpx já tratou (descompressão) e devem ser recalculados
RESP_STRIP = HOP_HEADERS | {"content-encoding"}


@dataclass
class Backend:
    name: str
    url: str
    healthy: bool = False        # começa DOWN até o 1º health check passar
    active: int = 0              # conexões em andamento (para least_connections)
    total: int = 0               # total de requisições atendidas com sucesso
    errors: int = 0              # falhas de comunicação
    consec_fail: int = 0
    consec_ok: int = 0


class Balancer:
    ALGORITHMS = ("round_robin", "least_connections", "random")

    def __init__(self, hosts: list[str], algorithm: str):
        self.backends = [Backend(name=h.split(":")[0], url=f"http://{h}") for h in hosts]
        self.algorithm = algorithm
        self._rr = 0  # ponteiro do round robin (e desempate do least_connections)

    # ------------------------- Seleção de backend ---------------------------
    def pick(self, exclude: set[str]) -> Backend | None:
        """
        Escolhe um backend saudável (ignorando os já tentados nesta requisição).
        Não há 'await' aqui, então é atômico dentro do event loop (sem locks).
        """
        candidates = [b for b in self.backends if b.healthy and b.name not in exclude]
        if not candidates:
            return None

        if self.algorithm == "round_robin":
            # Percorre os saudáveis em ordem circular
            chosen = candidates[self._rr % len(candidates)]
            self._rr += 1
        elif self.algorithm == "least_connections":
            # Menor nº de requisições em andamento; empate resolvido em rodízio
            least = min(b.active for b in candidates)
            tied = [b for b in candidates if b.active == least]
            chosen = tied[self._rr % len(tied)]
            self._rr += 1
        else:  # random
            chosen = random.choice(candidates)

        chosen.active += 1  # reserva já aqui, antes de qualquer await
        return chosen

    # ---------------------------- Health check ------------------------------
    def mark_down(self, b: Backend, reason: str):
        """Health check passivo: remove imediatamente do pool."""
        if b.healthy:
            log.warning("❌ %s REMOVIDO do pool (%s)", b.name, reason)
        b.healthy = False
        b.consec_ok = 0
        b.consec_fail = FALL_THRESHOLD

    async def check(self, client: httpx.AsyncClient, b: Backend, initial: bool = False):
        """Health check ativo de um backend, com histerese."""
        try:
            r = await client.get(f"{b.url}/health", timeout=HEALTH_TIMEOUT)
            ok = r.status_code == 200
        except httpx.HTTPError:
            ok = False

        if ok:
            b.consec_fail = 0
            b.consec_ok += 1
            if not b.healthy and (initial or b.consec_ok >= RISE_THRESHOLD):
                b.healthy = True
                log.info("✅ %s VOLTOU ao pool", b.name)
        else:
            b.consec_ok = 0
            b.consec_fail += 1
            if b.healthy and b.consec_fail >= FALL_THRESHOLD:
                b.healthy = False
                log.warning("❌ %s REMOVIDO do pool (health check falhou %dx)", b.name, b.consec_fail)

    async def check_all(self, client: httpx.AsyncClient, initial: bool = False):
        await asyncio.gather(*(self.check(client, b, initial) for b in self.backends))

    async def health_loop(self, client: httpx.AsyncClient):
        while True:
            await asyncio.sleep(HEALTH_INTERVAL)
            await self.check_all(client)


balancer = Balancer(BACKENDS, ALGORITHM)
client: httpx.AsyncClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    client = httpx.AsyncClient(limits=httpx.Limits(max_connections=1000))
    await balancer.check_all(client, initial=True)  # estado inicial antes de receber tráfego
    log.info("Iniciado | algoritmo=%s | backends=%s", balancer.algorithm,
             {b.name: b.healthy for b in balancer.backends})
    task = asyncio.create_task(balancer.health_loop(client))
    yield
    task.cancel()
    await client.aclose()


app = FastAPI(title="ShopNow Load Balancer", lifespan=lifespan)


# ----------------------------- API administrativa ---------------------------
@app.get("/lb/status")
async def status():
    return {
        "algorithm": balancer.algorithm,
        "available_algorithms": Balancer.ALGORITHMS,
        "backends": [
            {"name": b.name, "healthy": b.healthy, "active_connections": b.active,
             "total_requests": b.total, "errors": b.errors}
            for b in balancer.backends
        ],
    }


@app.post("/lb/algorithm/{name}")
async def set_algorithm(name: str):
    if name not in Balancer.ALGORITHMS:
        raise HTTPException(400, f"Algoritmo inválido. Use: {Balancer.ALGORITHMS}")
    balancer.algorithm = name
    log.info("🔀 Algoritmo alterado para %s", name)
    return {"algorithm": name}


# ------------------------------- Proxy reverso ------------------------------
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def proxy(path: str, request: Request):
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    headers["X-Forwarded-For"] = request.client.host if request.client else "unknown"
    query = request.url.query
    safe_method = request.method in ("GET", "HEAD", "OPTIONS")

    tried: set[str] = set()
    # No máximo uma tentativa por backend (failover transparente)
    for _ in range(len(balancer.backends)):
        b = balancer.pick(tried)
        if b is None:
            break
        tried.add(b.name)
        try:
            upstream = await client.request(
                request.method,
                f"{b.url}/{path}" + (f"?{query}" if query else ""),
                headers=headers, content=body, timeout=REQUEST_TIMEOUT,
            )
            b.total += 1
            out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in RESP_STRIP}
            out_headers["X-Served-By"] = b.name
            out_headers["X-LB-Algorithm"] = balancer.algorithm
            return Response(content=upstream.content, status_code=upstream.status_code,
                            headers=out_headers)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            # A requisição nem chegou ao backend -> seguro tentar outro (qualquer método)
            b.errors += 1
            balancer.mark_down(b, f"erro de conexão: {type(e).__name__}")
        except httpx.HTTPError as e:
            # Falha no meio da requisição: só reenvia se for idempotente
            b.errors += 1
            balancer.mark_down(b, f"erro durante requisição: {type(e).__name__}")
            if not safe_method:
                return Response('{"error":"bad gateway"}', status_code=502,
                                media_type="application/json")
        finally:
            b.active -= 1

    return Response('{"error":"nenhuma instância saudável disponível"}', status_code=503,
                    media_type="application/json")

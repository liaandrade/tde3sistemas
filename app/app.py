"""
ShopNow - Instância da aplicação de e-commerce (simulada).

Todas as instâncias rodam ESTE MESMO código; a única diferença é a variável
de ambiente INSTANCE_ID, que permite saber quem respondeu cada requisição.
"""
import os
import random
import socket
import threading
import time

from flask import Flask, jsonify, request

app = Flask(__name__)

INSTANCE_ID = os.getenv("INSTANCE_ID", socket.gethostname())

# Estado local da instância (protegido por lock porque o gunicorn usa threads)
_lock = threading.Lock()
_state = {"healthy": True, "requests_served": 0}

# Catálogo fictício
PRODUCTS = [
    {"id": 1, "name": "Notebook Gamer", "price": 5999.90},
    {"id": 2, "name": "Smartphone Pro", "price": 3499.00},
    {"id": 3, "name": "Fone Bluetooth", "price": 299.90},
    {"id": 4, "name": "Monitor 4K", "price": 1899.00},
]


def _count():
    """Incrementa o contador de requisições atendidas por esta instância."""
    with _lock:
        _state["requests_served"] += 1
        return _state["requests_served"]


def _reply(payload: dict, status: int = 200):
    """Toda resposta inclui o ID da instância e o header X-Instance-Id."""
    payload = {"instance": INSTANCE_ID, "hostname": socket.gethostname(), **payload}
    resp = jsonify(payload)
    resp.status_code = status
    resp.headers["X-Instance-Id"] = INSTANCE_ID
    return resp


@app.get("/")
def index():
    n = _count()
    return _reply({"message": "Bem-vindo à ShopNow!", "requests_served": n})


@app.get("/products")
def products():
    _count()
    return _reply({"products": PRODUCTS})


@app.post("/checkout")
def checkout():
    """Simula uma operação de escrita com latência variável."""
    _count()
    time.sleep(random.uniform(0.05, 0.2))
    return _reply({"status": "pedido_criado", "order_id": random.randint(10000, 99999)}, 201)


@app.get("/slow")
def slow():
    """
    Requisição lenta (ex.: ?delay=3). Mantém a conexão aberta por N segundos;
    útil para demonstrar o algoritmo Least Connections.
    """
    _count()
    delay = min(float(request.args.get("delay", 2)), 30)
    time.sleep(delay)
    return _reply({"message": f"resposta lenta ({delay}s)"})


@app.get("/health")
def health():
    """Endpoint consultado pelo Load Balancer (health check)."""
    if _state["healthy"]:
        return _reply({"status": "ok"})
    return _reply({"status": "unhealthy"}, 503)


# ---- Endpoints de simulação de falha "suave" (o processo continua vivo) ----
@app.post("/admin/fail")
def admin_fail():
    _state["healthy"] = False
    return _reply({"message": "instância marcada como DOENTE (/health -> 503)"})


@app.post("/admin/recover")
def admin_recover():
    _state["healthy"] = True
    return _reply({"message": "instância recuperada (/health -> 200)"})

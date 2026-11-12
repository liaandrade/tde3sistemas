# ShopNow – Load Balancer + 3 instâncias

```bash
docker compose up --build            # sobe tudo
curl localhost:8080/                 # cliente -> LB -> instância
./scripts/simulate_failure.sh        # demonstra o failover
```
Portas: LB `8080` | apps (acesso direto p/ testes) `5001`, `5002`, `5003`.
Status do LB: `curl localhost:8080/lb/status`
Trocar algoritmo: `curl -X POST localhost:8080/lb/algorithm/least_connections`

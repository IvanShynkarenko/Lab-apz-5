# Лабораторна робота №5
## Мікросервіси з використанням Service Discovery та Config Server на базі Consul

**Мета:** Замінити власний Config Server з Lab 4 на HashiCorp Consul, який виконує роль Service Registry, Service Discovery, Config Server (KV Store) та Health Monitor.

---

## 1. Архітектура системи

```
┌─────────────────────────────────────────────────────────────┐
│                  Consul :8500  (UI: /ui)                     │
│                                                              │
│  Service Registry              KV Store                      │
│  ├─ logging-service (×3)       ├─ config/hazelcast/          │
│  ├─ counter-service            │    cluster-members          │
│  └─ facade-service             └─ config/queue/name          │
└─────────────────────────────────────────────────────────────┘
         ▲ register + health check
         │
Client ──► facade-service :50050  (FastAPI / HTTP)
              │
              ├── health.service("logging-service", passing=True)
              │
              ├──gRPC──► logging-service-1 :50051 ──► Hazelcast IMap
              ├──gRPC──► logging-service-2 :50051 ──► Hazelcast IMap
              └──gRPC──► logging-service-3 :50051 ──► Hazelcast IMap
              │
              └──put()──► Hazelcast IQueue "counter-queue"
                                  │ take()
                           counter-service :50060  (FastAPI / HTTP)
                                  │
                              PostgreSQL :5435
```

### Ролі Consul у системі

| Роль | Опис |
|------|------|
| **Service Registry** | Сервіси реєструються при старті через Consul Agent API |
| **Service Discovery** | facade-service отримує список здорових екземплярів через `health.service(..., passing=True)` |
| **Config Server (KV)** | Адреси Hazelcast та назва черги зберігаються в Consul KV Store |
| **Health Monitoring** | TCP/HTTP health checks кожні 10 с, авто-видалення після 30 с недоступності |

---

## 2. Структура проєкту

```
lab5/
├── docker-compose.yml
├── consul-bootstrap/
│   └── bootstrap.sh          # Заповнення Consul KV при старті
├── facade-service/
│   ├── Dockerfile
│   ├── main.py               # FastAPI + Consul SD + Hazelcast queue
│   └── requirements.txt
├── logging-service/
│   ├── Dockerfile
│   ├── main.py               # gRPC сервер + Consul registration + Hazelcast IMap
│   └── requirements.txt
├── messages-service/         # counter-service
│   ├── Dockerfile
│   ├── main.py               # FastAPI + queue consumer + PostgreSQL
│   └── requirements.txt
├── hazelcast/
│   └── hazelcast.yaml
└── proto/
    ├── logging.proto
    ├── logging_pb2.py
    └── logging_pb2_grpc.py
```

---

## 3. Consul KV — конфігурація

### 3.1 Bootstrap скрипт (`consul-bootstrap/bootstrap.sh`)

```bash
#!/bin/sh
CONSUL_URL="http://consul:8500"

echo "[bootstrap] Waiting for Consul..."
until curl -sf "${CONSUL_URL}/v1/status/leader" > /dev/null; do
  sleep 1
done
echo "[bootstrap] Consul is ready"

# Адреси вузлів Hazelcast
curl -sf -X PUT "${CONSUL_URL}/v1/kv/config/hazelcast/cluster-members" \
  -d "hazelcast-1:5701,hazelcast-2:5701,hazelcast-3:5701"
echo "[bootstrap] Set config/hazelcast/cluster-members"

# Назва черги
curl -sf -X PUT "${CONSUL_URL}/v1/kv/config/queue/name" \
  -d "counter-queue"
echo "[bootstrap] Set config/queue/name"

echo "[bootstrap] Done seeding Consul KV"
```

Скрипт запускається одноразово контейнером `consul-bootstrap` (образ `curlimages/curl`) з `restart: on-failure`. Він чекає на готовність Consul і записує два ключі в KV Store.

### 3.2 Перевірка KV після запуску

```bash
curl http://localhost:8500/v1/kv/config/hazelcast/cluster-members?raw
# hazelcast-1:5701,hazelcast-2:5701,hazelcast-3:5701

curl http://localhost:8500/v1/kv/config/queue/name?raw
# counter-queue
```

> 📸 **СКРІН 1:** Термінал з виводом двох `curl` запитів до Consul KV — показати значення ключів `cluster-members` та `queue/name`

---

## 4. Реалізація сервісів

### 4.1 Реєстрація в Consul та читання KV — `logging-service`

Кожен з трьох екземплярів реєструється з унікальним `service_id` (logging-1, logging-2, logging-3) та TCP health check:

```python
# logging-service/main.py

def register_with_consul():
    c = get_consul()
    for attempt in range(15):
        try:
            c.agent.service.register(
                name="logging-service",
                service_id=SERVICE_ID,          # logging-1 / logging-2 / logging-3
                address=MY_HOST,
                port=LOG_PORT,
                check=consul.Check.tcp(
                    MY_HOST, LOG_PORT,
                    interval="10s",
                    timeout="5s",
                    deregister="30s"            # авто-видалення після 30 с недоступності
                ),
            )
            print(f"[{SERVICE_ID}] Registered with Consul (id={SERVICE_ID}, {MY_HOST}:{LOG_PORT})")
            return c
        except Exception as e:
            print(f"[{SERVICE_ID}] Consul not ready ({e}), retrying in 2s...")
            time.sleep(2)
```

Адреси Hazelcast читаються з Consul KV (не з env vars):

```python
def get_hazelcast_client(c):
    cluster_str = get_kv(c, "config/hazelcast/cluster-members")
    addresses = [a.strip() for a in cluster_str.split(",")]
    print(f"[{SERVICE_ID}] Hazelcast members from Consul: {addresses}")
    return hazelcast.HazelcastClient(cluster_members=addresses, cluster_name="dev")
```

### 4.2 Service Discovery у `facade-service`

facade-service не має хардкодованих адрес logging-service. При кожному запиті він запитує Consul про здорові екземпляри:

```python
# facade-service/main.py

def get_healthy_logging_addresses() -> list[str]:
    c = get_consul()
    _, services = c.health.service("logging-service", passing=True)
    return [
        f"{s['Service']['Address']}:{s['Service']['Port']}"
        for s in services
    ]

def call_logging(rpc_fn):
    targets = get_healthy_logging_addresses()
    if not targets:
        raise HTTPException(status_code=503, detail="No healthy logging-service instances in Consul")
    random.shuffle(targets)          # випадковий порядок = балансування навантаження
    last_error = None
    for target in targets:
        stub = get_stub(target)
        try:
            result = rpc_fn(stub)
            print(f"[facade] Used logging-service at {target}")
            return result
        except grpc.RpcError as e:
            last_error = e
            if e.code() in RETRYABLE_CODES:
                print(f"[facade] {target} unavailable ({e.code()}), trying next")
                continue
            raise
    raise HTTPException(status_code=503,
                        detail=f"All logging-service instances unavailable: {last_error}")
```

Аналогічно для counter-service:

```python
def get_healthy_counter_address() -> str:
    c = get_consul()
    _, services = c.health.service("counter-service", passing=True)
    if not services:
        raise HTTPException(status_code=503, detail="No healthy counter-service instances")
    svc = random.choice(services)
    return f"{svc['Service']['Address']}:{svc['Service']['Port']}"
```

### 4.3 Реєстрація `counter-service` та `facade-service`

Обидва сервіси реєструються з HTTP health check (endpoint `/health`):

```python
c.agent.service.register(
    name="counter-service",
    service_id="counter-service",
    address=MY_HOST,
    port=COUNTER_PORT,
    check=consul.Check.http(
        f"http://{MY_HOST}:{COUNTER_PORT}/health",
        interval="10s", timeout="5s", deregister="30s"
    ),
)
```

### 4.4 Типи health checks

| Сервіс | Тип перевірки | Endpoint / адреса |
|--------|--------------|-------------------|
| logging-service (×3) | TCP | `host:50051` |
| counter-service | HTTP GET | `http://counter-service:50060/health` |
| facade-service | HTTP GET | `http://facade-service:50050/health` |

---

## 5. Docker Compose конфігурація

Ключові сервіси з `docker-compose.yml`:

```yaml
consul:
  image: hashicorp/consul:1.18
  command: agent -server -bootstrap-expect=1 -ui -client=0.0.0.0 -bind=0.0.0.0
  ports:
    - "8500:8500"

consul-bootstrap:
  image: curlimages/curl:8.7.1
  volumes:
    - ./consul-bootstrap/bootstrap.sh:/bootstrap.sh
  command: sh /bootstrap.sh
  depends_on:
    - consul
  restart: on-failure

logging-service-1:
  build:
    context: .
    dockerfile: logging-service/Dockerfile
  environment:
    SERVICE_ID: logging-1
    LOG_PORT: "50051"
    MY_HOST: "logging-service-1"
    CONSUL_HOST: "consul"
    CONSUL_PORT: "8500"
  ports:
    - "50051:50051"

# logging-service-2 (порт 50054) та logging-service-3 (порт 50055) — аналогічно

counter-service:
  build:
    context: .
    dockerfile: messages-service/Dockerfile
  environment:
    COUNTER_PORT: "50060"
    MY_HOST: "counter-service"
    DB_HOST: postgres
    CONSUL_HOST: "consul"
    CONSUL_PORT: "8500"
  ports:
    - "50060:50060"

facade-service:
  environment:
    FACADE_PORT: "50050"
    MY_HOST: "facade-service"
    CONSUL_HOST: "consul"
    CONSUL_PORT: "8500"
  ports:
    - "50050:50050"
```

---

## 6. Запуск системи

```bash
cd lab5
docker compose up -d
```

> 📸 **СКРІН 2:** `docker compose ps` — всі контейнери зі статусом `Up` (consul, consul-bootstrap, hazelcast-1/2/3, postgres, logging-service-1/2/3, counter-service, facade-service)

### 6.1 Consul UI

Відкрити в браузері: **http://localhost:8500/ui**

> 📸 **СКРІН 3:** Consul UI — вкладка **Services**, показати всі зареєстровані сервіси (logging-service ×3, counter-service, facade-service) зі зеленим статусом (All checks passing)

### 6.2 Зареєстровані сервіси через API

```bash
curl http://localhost:8500/v1/agent/services | python3 -m json.tool
```

Очікуваний результат (скорочено):

```json
{
  "facade-service": {
    "ID": "facade-service",
    "Service": "facade-service",
    "Address": "facade-service",
    "Port": 50050
  },
  "counter-service": {
    "ID": "counter-service",
    "Service": "counter-service",
    "Address": "counter-service",
    "Port": 50060
  },
  "logging-1": {
    "ID": "logging-1",
    "Service": "logging-service",
    "Address": "logging-service-1",
    "Port": 50051
  },
  "logging-2": {
    "ID": "logging-2",
    "Service": "logging-service",
    "Address": "logging-service-2",
    "Port": 50051
  },
  "logging-3": {
    "ID": "logging-3",
    "Service": "logging-service",
    "Address": "logging-service-3",
    "Port": 50051
  }
}
```

> 📸 **СКРІН 4:** Термінал з виводом `curl http://localhost:8500/v1/agent/services | python3 -m json.tool`

### 6.3 Здорові екземпляри logging-service

```bash
curl 'http://localhost:8500/v1/health/service/logging-service?passing=true' \
  | python3 -m json.tool | grep -E '"ID"|"Address"|"Port"'
```

Очікуваний результат:

```
"ID": "logging-1",
"Address": "logging-service-1",
"Port": 50051,
"ID": "logging-2",
"Address": "logging-service-2",
"Port": 50051,
"ID": "logging-3",
"Address": "logging-service-3",
"Port": 50051,
```

> 📸 **СКРІН 5:** Термінал з виводом healthy logging-service instances

---

## 7. Тестування

### 7.1 POST-запити — 10 транзакцій

```bash
for i in $(seq 1 10); do
  curl -s -X POST http://localhost:50050/ \
    -H "Content-Type: application/json" \
    -d "{\"msg\": \"msg$i\"}"
  echo
done
```

Очікуваний результат:

```json
{"uuid":"b4afcac7-6692-49d4-aafc-b69a4b458678","msg":"msg1"}
{"uuid":"c819b5a6-b434-49ac-b243-87d3b6176a81","msg":"msg2"}
{"uuid":"31a30ba4-c593-4f55-b9da-ba633074a4ac","msg":"msg3"}
{"uuid":"2edad12b-46a6-4988-a850-63b8b6a6b814","msg":"msg4"}
{"uuid":"9a74e7c5-9e2b-4367-a09d-f46bdfa8debc","msg":"msg5"}
{"uuid":"8d3d5f0b-56c8-465b-ae67-1f82ab1007b8","msg":"msg6"}
{"uuid":"965155ae-bb4a-42b8-8f0b-629feff547fa","msg":"msg7"}
{"uuid":"bfcbdd15-4909-4e9a-8b2f-e70ebe6fbd3e","msg":"msg8"}
{"uuid":"6bef4903-0abb-4ade-8a70-ce0ac499f927","msg":"msg9"}
{"uuid":"08c51a06-dde4-4584-a650-d1ebe0f3df56","msg":"msg10"}
```

> 📸 **СКРІН 6:** Термінал з 10 POST-запитами та відповідями (uuid + msg для кожного)

### 7.2 GET-запит — отримання всіх повідомлень

```bash
curl http://localhost:50050/
```

Очікуваний результат:

```
msg1 msg2 msg3 msg4 msg5 msg6 msg7 msg8 msg9 msg10
```

> 📸 **СКРІН 7:** Термінал з GET-запитом та відповіддю — всі 10 повідомлень в одному рядку

### 7.3 Розподіл між екземплярами logging-service

facade-service використовує `random.shuffle(targets)` для балансування між здоровими екземплярами. Повідомлення розподіляються випадково:

| Екземпляр | Отримані повідомлення |
|-----------|----------------------|
| logging-service-1 | msg8, msg9 |
| logging-service-2 | msg4, msg6, msg7 |
| logging-service-3 | msg1, msg2, msg3, msg5, msg10 |

Логи контейнерів підтверджують розподіл:

```
[logging-1] Stored  uuid='bfcbdd15-...' msg='msg8'
[logging-1] Stored  uuid='6bef4903-...' msg='msg9'

[logging-2] Stored  uuid='2edad12b-...' msg='msg4'
[logging-2] Stored  uuid='8d3d5f0b-...' msg='msg6'
[logging-2] Stored  uuid='965155ae-...' msg='msg7'

[logging-3] Stored  uuid='b4afcac7-...' msg='msg1'
[logging-3] Stored  uuid='c819b5a6-...' msg='msg2'
[logging-3] Stored  uuid='31a30ba4-...' msg='msg3'
[logging-3] Stored  uuid='9a74e7c5-...' msg='msg5'
[logging-3] Stored  uuid='08c51a06-...' msg='msg10'
```

> 📸 **СКРІН 8:** Логи трьох контейнерів — `docker logs lab5-logging-service-1-1`, `docker logs lab5-logging-service-2-1`, `docker logs lab5-logging-service-3-1` — показати рядки `Stored uuid=...`

### 7.4 Споживання черги counter-service

```bash
docker logs lab5-counter-service-1
```

Очікуваний вивід:

```
[counter-service] DB initialized
[counter-service] Registered with Consul (counter-service:50060)
[counter-service] Queue consumer started, waiting for messages...
[counter-service] Consumed from queue: 'msg1'
[counter-service] Consumed from queue: 'msg2'
[counter-service] Consumed from queue: 'msg3'
...
[counter-service] Consumed from queue: 'msg10'
```

> 📸 **СКРІН 9:** `docker logs lab5-counter-service-1` — рядки `Consumed from queue` для всіх 10 повідомлень

---

## 8. Перевірка відмовостійкості

### 8.1 Вимкнення logging-service-1

```bash
docker stop lab5-logging-service-1-1
```

Через ~15 секунд (health check interval + deregister timeout) Consul автоматично прибирає екземпляр зі списку healthy:

```bash
curl 'http://localhost:8500/v1/health/service/logging-service?passing=true' \
  | python3 -m json.tool | grep '"Address"'
# "Address": "logging-service-2",
# "Address": "logging-service-3",
```

Нові запити продовжують працювати через два здорових екземпляри:

```bash
curl -s -X POST http://localhost:50050/ \
  -H "Content-Type: application/json" \
  -d '{"msg": "msg11"}'
# {"uuid":"2e93a31b-...","msg":"msg11"}
```

> 📸 **СКРІН 10:** Термінал — зупинка logging-service-1, Consul показує 2 healthy instances, успішний POST з `msg11`

> 📸 **СКРІН 11:** Consul UI — logging-service-1 змінив статус на **critical** (червоний індикатор), logging-service-2 та logging-service-3 залишаються зеленими

### 8.2 Відновлення logging-service-1

```bash
docker start lab5-logging-service-1-1
```

Після старту сервіс повторно реєструється в Consul. Через ~15 секунд (перший успішний health check) екземпляр повертається у список healthy:

```bash
curl 'http://localhost:8500/v1/health/service/logging-service?passing=true' \
  | python3 -m json.tool | grep '"Address"'
# "Address": "logging-service-1",
# "Address": "logging-service-2",
# "Address": "logging-service-3",
```

> 📸 **СКРІН 12:** Consul UI — всі 3 екземпляри logging-service знову зелені (All checks passing)

### 8.3 Вимкнення counter-service (`docker pause`)

```bash
docker pause lab5-counter-service-1
```

POST-запити продовжують працювати — повідомлення потрапляють у Hazelcast IQueue і чекають на споживача:

```bash
curl -s -X POST http://localhost:50050/ \
  -H "Content-Type: application/json" \
  -d '{"msg": "msg12"}'
# {"uuid":"e69e021b-...","msg":"msg12"}

curl -s -X POST http://localhost:50050/ \
  -H "Content-Type: application/json" \
  -d '{"msg": "msg13"}'
# {"uuid":"edfd9d4d-...","msg":"msg13"}
```

GET-запит повертає `null` для частини counter (facade-service перехоплює помилку і підставляє `"null"`):

```bash
curl http://localhost:50050/
# msg1 msg2 ... msg10 null
```

> 📸 **СКРІН 13:** Термінал — `docker pause`, два успішних POST, GET з `null` в кінці рядка

### 8.4 Відновлення counter-service

```bash
docker unpause lab5-counter-service-1
```

counter-service вичитує накопичені повідомлення з черги:

```
[counter-service] Consumed from queue: 'msg12'
[counter-service] Consumed from queue: 'msg13'
```

GET-запит повертає повний список:

```bash
curl http://localhost:50050/
# msg1 msg2 msg3 msg4 msg5 msg6 msg7 msg8 msg9 msg10 msg11 msg12 msg13
```

> 📸 **СКРІН 14:** Термінал — `docker unpause`, логи споживання `msg12`/`msg13`, GET з повним списком повідомлень

---

## 9. Порівняння з Lab 4 (власний Config Server)

| Аспект | Lab 4 (власний Config Server) | Lab 5 (Consul) |
|--------|-------------------------------|----------------|
| Конфігурація | Окремий Python-сервіс | Consul KV Store (вбудований) |
| Service Registry | Відсутній / хардкод адрес | Consul Agent API |
| Service Discovery | Статичні адреси в env vars | `health.service(..., passing=True)` |
| Health Monitoring | Відсутній | TCP/HTTP checks, авто-видалення |
| UI моніторингу | Відсутній | Consul UI (http://localhost:8500/ui) |
| Відмовостійкість | Ручне оновлення конфігурації | Автоматичне виключення з балансування |
| Кількість додаткових сервісів | 1 (config-server) | 1 (consul) + 1 (consul-bootstrap) |

---

## 10. Висновки

1. **Consul** замінює власний Config Server і надає повноцінний Service Registry з автоматичним health monitoring без написання додаткового коду.

2. **Service Discovery** через `health.service(..., passing=True)` гарантує, що facade-service завжди отримує лише здорові екземпляри — без хардкодованих адрес у коді чи конфігурації.

3. **Consul KV** зберігає конфігурацію Hazelcast та Message Queue централізовано. Зміна конфігурації (наприклад, додавання вузла Hazelcast) не потребує перебудови Docker-образів.

4. **Health checks** автоматично виявляють недоступні екземпляри та виключають їх з балансування протягом 10–15 секунд. При відновленні сервіс автоматично повертається у пул без ручного втручання.

5. **Consul UI** (http://localhost:8500/ui) надає зручний веб-інтерфейс для моніторингу стану всіх сервісів у реальному часі — корисно як для розробки, так і для діагностики проблем.

6. **Відмовостійкість черги** — при недоступності counter-service повідомлення накопичуються в Hazelcast IQueue і обробляються після відновлення сервісу, без втрати даних.

---

*Посилання на GitHub: [вставити посилання на репозиторій, гілка `micro_consul`]*

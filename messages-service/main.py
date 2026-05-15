import os
import time
import threading
import psycopg2
from psycopg2.extras import RealDictCursor
import hazelcast
import consul
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

app = FastAPI()

COUNTER_PORT = int(os.environ.get("COUNTER_PORT", 50060))
MY_HOST = os.environ.get("MY_HOST", "counter-service")
DB_HOST = os.environ.get("DB_HOST", "postgres")
DB_PORT = int(os.environ.get("DB_PORT", 5432))
DB_NAME = os.environ.get("DB_NAME", "counterdb")
DB_USER = os.environ.get("DB_USER", "counter")
DB_PASS = os.environ.get("DB_PASS", "counter")
CONSUL_HOST = os.environ.get("CONSUL_HOST", "consul")
CONSUL_PORT = int(os.environ.get("CONSUL_PORT", 8500))


def get_consul():
    return consul.Consul(host=CONSUL_HOST, port=CONSUL_PORT)


def get_kv(c, key):
    for attempt in range(10):
        try:
            _, data = c.kv.get(key)
            if data:
                return data["Value"].decode()
        except Exception as e:
            print(f"[counter-service] Consul KV get {key!r} failed ({e}), retrying...")
        time.sleep(2)
    raise RuntimeError(f"Could not read Consul KV key {key!r}")


def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASS,
    )


def init_db():
    for attempt in range(10):
        try:
            conn = get_conn()
            with conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS messages (
                            id SERIAL PRIMARY KEY,
                            content TEXT NOT NULL,
                            created_at TIMESTAMPTZ DEFAULT NOW()
                        )
                    """)
            conn.close()
            print("[counter-service] DB initialized")
            return
        except Exception as e:
            print(f"[counter-service] DB not ready ({e}), retrying in 2s...")
            time.sleep(2)
    raise RuntimeError("Could not connect to PostgreSQL after 10 attempts")


def register_with_consul(c):
    for attempt in range(15):
        try:
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
            print(f"[counter-service] Registered with Consul ({MY_HOST}:{COUNTER_PORT})")
            return
        except Exception as e:
            print(f"[counter-service] Consul not ready ({e}), retrying in 2s...")
            time.sleep(2)
    raise RuntimeError("Could not register with Consul")


def queue_consumer(c):
    cluster_str = get_kv(c, "config/hazelcast/cluster-members")
    queue_name = get_kv(c, "config/queue/name")
    addresses = [a.strip() for a in cluster_str.split(",")]
    print(f"[counter-service] Queue consumer: hz={addresses}, queue={queue_name!r}")

    hz = hazelcast.HazelcastClient(cluster_members=addresses, cluster_name="dev")
    queue = hz.get_queue(queue_name).blocking()
    print("[counter-service] Queue consumer started, waiting for messages...")
    while True:
        try:
            msg = queue.take()
            print(f"[counter-service] Consumed from queue: {msg!r}")
            conn = get_conn()
            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute("INSERT INTO messages (content) VALUES (%s)", (msg,))
            finally:
                conn.close()
        except Exception as e:
            print(f"[counter-service] Consumer error: {e}, retrying in 1s...")
            time.sleep(1)


@app.on_event("startup")
def startup():
    init_db()
    c = get_consul()
    register_with_consul(c)
    t = threading.Thread(target=queue_consumer, args=(c,), daemon=True)
    t.start()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=PlainTextResponse)
def get_messages():
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT content FROM messages ORDER BY id")
            rows = cur.fetchall()
        return " ".join(r["content"] for r in rows) if rows else ""
    finally:
        conn.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=COUNTER_PORT)

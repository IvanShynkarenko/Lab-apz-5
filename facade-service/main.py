import os
import sys
import time
import uuid
import random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import grpc
import httpx
import hazelcast
import consul
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from proto import logging_pb2
from proto import logging_pb2_grpc

FACADE_PORT = int(os.environ.get("FACADE_PORT", 50050))
MY_HOST = os.environ.get("MY_HOST", "facade-service")
CONSUL_HOST = os.environ.get("CONSUL_HOST", "consul")
CONSUL_PORT = int(os.environ.get("CONSUL_PORT", 8500))
RETRY_TIMEOUT = 5

RETRYABLE_CODES = (
    grpc.StatusCode.UNAVAILABLE,
    grpc.StatusCode.DEADLINE_EXCEEDED,
    grpc.StatusCode.RESOURCE_EXHAUSTED,
    grpc.StatusCode.ABORTED,
)

app = FastAPI()
_hz_client = None
_counter_queue = None
_consul_client = None


def get_consul():
    global _consul_client
    if _consul_client is None:
        _consul_client = consul.Consul(host=CONSUL_HOST, port=CONSUL_PORT)
    return _consul_client


def get_kv(key):
    c = get_consul()
    for attempt in range(10):
        try:
            _, data = c.kv.get(key)
            if data:
                return data["Value"].decode()
        except Exception as e:
            print(f"[facade] Consul KV get {key!r} failed ({e}), retrying...")
        time.sleep(2)
    raise RuntimeError(f"Could not read Consul KV key {key!r}")


def register_with_consul():
    c = get_consul()
    for attempt in range(15):
        try:
            c.agent.service.register(
                name="facade-service",
                service_id="facade-service",
                address=MY_HOST,
                port=FACADE_PORT,
                check=consul.Check.http(
                    f"http://{MY_HOST}:{FACADE_PORT}/health",
                    interval="10s", timeout="5s", deregister="30s"
                ),
            )
            print(f"[facade] Registered with Consul ({MY_HOST}:{FACADE_PORT})")
            return
        except Exception as e:
            print(f"[facade] Consul not ready ({e}), retrying in 2s...")
            time.sleep(2)
    raise RuntimeError("Could not register with Consul")


def init_hz():
    global _hz_client, _counter_queue
    cluster_str = get_kv("config/hazelcast/cluster-members")
    queue_name = get_kv("config/queue/name")
    addresses = [a.strip() for a in cluster_str.split(",")]
    print(f"[facade] Hazelcast members from Consul: {addresses}, queue: {queue_name!r}")
    _hz_client = hazelcast.HazelcastClient(cluster_members=addresses, cluster_name="dev")
    _counter_queue = _hz_client.get_queue(queue_name).blocking()
    print("[facade] Connected to Hazelcast queue")


def get_healthy_logging_addresses() -> list[str]:
    c = get_consul()
    _, services = c.health.service("logging-service", passing=True)
    addresses = []
    for svc in services:
        host = svc["Service"]["Address"]
        port = svc["Service"]["Port"]
        addresses.append(f"{host}:{port}")
    return addresses


def get_healthy_counter_address() -> str:
    c = get_consul()
    _, services = c.health.service("counter-service", passing=True)
    if not services:
        raise HTTPException(status_code=503, detail="No healthy counter-service instances")
    svc = random.choice(services)
    host = svc["Service"]["Address"]
    port = svc["Service"]["Port"]
    return f"{host}:{port}"


def get_stub(target: str) -> logging_pb2_grpc.LoggingServiceStub:
    channel = grpc.insecure_channel(target)
    return logging_pb2_grpc.LoggingServiceStub(channel)


def call_logging(rpc_fn):
    targets = get_healthy_logging_addresses()
    if not targets:
        raise HTTPException(status_code=503, detail="No healthy logging-service instances in Consul")
    random.shuffle(targets)
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
    raise HTTPException(status_code=503, detail=f"All logging-service instances unavailable: {last_error}")


@app.on_event("startup")
def startup():
    register_with_consul()
    init_hz()


@app.get("/health")
def health():
    return {"status": "ok"}


class PostBody(BaseModel):
    msg: str


@app.post("/")
def post_message(body: PostBody):
    message_id = str(uuid.uuid4())
    request = logging_pb2.LogRequest(uuid=message_id, msg=body.msg)

    def do_log(stub):
        stub.LogMessage(request, timeout=RETRY_TIMEOUT)

    call_logging(do_log)

    _counter_queue.put(body.msg)
    print(f"[facade] Queued message for counter: {body.msg!r}")

    return {"uuid": message_id, "msg": body.msg}


@app.get("/", response_class=PlainTextResponse)
def get_messages():
    def do_get(stub):
        return stub.GetMessages(logging_pb2.GetMessagesRequest(), timeout=RETRY_TIMEOUT)

    try:
        log_response = call_logging(do_get)
    except HTTPException:
        raise
    except grpc.RpcError as e:
        raise HTTPException(status_code=503, detail=f"Logging service error: {e.code()}")

    try:
        counter_addr = get_healthy_counter_address()
        with httpx.Client() as client:
            resp = client.get(f"http://{counter_addr}", timeout=5.0)
            resp.raise_for_status()
            counter_text = resp.text
    except Exception as e:
        counter_text = "null"
        print(f"[facade] Counter service unavailable: {e}")

    log_str = " ".join(log_response.messages) if log_response.messages else ""
    combined = f"{log_str} {counter_text}".strip() if log_str else counter_text
    return combined


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=FACADE_PORT)

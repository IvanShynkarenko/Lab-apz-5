import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import grpc
import consul
from concurrent import futures

import hazelcast

from proto import logging_pb2
from proto import logging_pb2_grpc

LOG_PORT = int(os.environ.get("LOG_PORT", 50051))
SERVICE_ID = os.environ.get("SERVICE_ID", "logging-1")
MY_HOST = os.environ.get("MY_HOST", "logging-service-1")
CONSUL_HOST = os.environ.get("CONSUL_HOST", "consul")
CONSUL_PORT = int(os.environ.get("CONSUL_PORT", 8500))
MAP_NAME = "messages-map"


def get_consul():
    return consul.Consul(host=CONSUL_HOST, port=CONSUL_PORT)


def get_kv(c, key):
    for attempt in range(10):
        try:
            _, data = c.kv.get(key)
            if data:
                return data["Value"].decode()
        except Exception as e:
            print(f"[{SERVICE_ID}] Consul KV get {key!r} failed ({e}), retrying...")
        time.sleep(2)
    raise RuntimeError(f"Could not read Consul KV key {key!r}")


def register_with_consul():
    c = get_consul()
    for attempt in range(15):
        try:
            c.agent.service.register(
                name="logging-service",
                service_id=SERVICE_ID,
                address=MY_HOST,
                port=LOG_PORT,
                check=consul.Check.tcp(MY_HOST, LOG_PORT, interval="10s", timeout="5s", deregister="30s"),
            )
            print(f"[{SERVICE_ID}] Registered with Consul (id={SERVICE_ID}, {MY_HOST}:{LOG_PORT})")
            return c
        except Exception as e:
            print(f"[{SERVICE_ID}] Consul not ready ({e}), retrying in 2s...")
            time.sleep(2)
    raise RuntimeError("Could not register with Consul")


def get_hazelcast_client(c):
    cluster_str = get_kv(c, "config/hazelcast/cluster-members")
    addresses = [a.strip() for a in cluster_str.split(",")]
    print(f"[{SERVICE_ID}] Hazelcast members from Consul: {addresses}")
    return hazelcast.HazelcastClient(cluster_members=addresses, cluster_name="dev")


class LoggingServicer(logging_pb2_grpc.LoggingServiceServicer):
    def __init__(self, hz):
        self._map = hz.get_map(MAP_NAME).blocking()
        print(f"[{SERVICE_ID}] Connected to Hazelcast, using map '{MAP_NAME}'")

    def LogMessage(self, request, context):
        uuid_val = request.uuid
        msg = request.msg
        existing = self._map.put_if_absent(uuid_val, msg)
        if existing is None:
            print(f"[{SERVICE_ID}] Stored  uuid={uuid_val!r} msg={msg!r}")
        else:
            print(f"[{SERVICE_ID}] Duplicate uuid={uuid_val!r}, skipped")
        return logging_pb2.LogResponse(ok=True)

    def GetMessages(self, request, context):
        messages = list(self._map.values())
        print(f"[{SERVICE_ID}] GetMessages -> {len(messages)} messages")
        return logging_pb2.GetMessagesResponse(messages=messages)


def serve():
    c = register_with_consul()
    hz = get_hazelcast_client(c)

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    logging_pb2_grpc.add_LoggingServiceServicer_to_server(LoggingServicer(hz), server)
    server.add_insecure_port(f"[::]:{LOG_PORT}")
    server.start()
    print(f"[{SERVICE_ID}] gRPC server listening on port {LOG_PORT}")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()

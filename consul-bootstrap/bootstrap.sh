#!/bin/sh
# Wait for Consul to be ready, then seed KV config values

CONSUL_URL="http://consul:8500"

echo "[bootstrap] Waiting for Consul..."
until curl -sf "${CONSUL_URL}/v1/status/leader" > /dev/null; do
  sleep 1
done
echo "[bootstrap] Consul is ready"

# Hazelcast cluster members
curl -sf -X PUT "${CONSUL_URL}/v1/kv/config/hazelcast/cluster-members" \
  -d "hazelcast-1:5701,hazelcast-2:5701,hazelcast-3:5701"
echo "[bootstrap] Set config/hazelcast/cluster-members"

# Queue name
curl -sf -X PUT "${CONSUL_URL}/v1/kv/config/queue/name" \
  -d "counter-queue"
echo "[bootstrap] Set config/queue/name"

echo "[bootstrap] Done seeding Consul KV"

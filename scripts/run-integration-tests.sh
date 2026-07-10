#!/usr/bin/env bash
# Spin up real TS3 + TS6 servers in docker and run the integration suite
# against both. Safe on a shared runner: ephemeral host ports only, a fixed
# unique compose project name, and full teardown (with -v, so the TS3
# serveradmin password is regenerated - and re-parsed - on every run).
set -euo pipefail

cd "$(dirname "$0")/.."
compose="docker compose -f docker/docker-compose.test.yml"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "=== integration run failed; dumping server logs ===" >&2
    $compose logs --no-color >&2 2>&1 || true
  fi
  $compose down -v --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT

echo "Starting ts3 + ts6..."
$compose up -d

echo "Waiting for ts3 to become healthy..."
ts3_ok=""
for _ in $(seq 1 40); do
  state="$($compose ps ts3 --format '{{.Health}}' 2>/dev/null || true)"
  if [ "$state" = "healthy" ]; then ts3_ok=1; break; fi
  sleep 3
done
[ -n "$ts3_ok" ] || { echo "ts3 did not become healthy in time" >&2; exit 1; }

echo "Waiting for ts6 ssh query listener..."
ts6_ok=""
for _ in $(seq 1 40); do
  if $compose logs ts6 2>&1 | grep -q "listening for ssh query"; then ts6_ok=1; break; fi
  sleep 3
done
[ -n "$ts6_ok" ] || { echo "ts6 ssh query did not come up in time" >&2; exit 1; }

echo "Discovering ephemeral ports and credentials..."
ts3_port="$($compose port ts3 10022 | awk -F: '{print $NF}')"
ts6_port="$($compose port ts6 10022 | awk -F: '{print $NF}')"
# The ts3 image prints the generated serveradmin password once on first boot:
#   loginname= "serveradmin", password= "XXXX"
ts3_password="$($compose logs ts3 2>&1 \
  | grep -oE 'loginname= "serveradmin", password= "[^"]+"' \
  | grep -oE '"[^"]+"$' | tr -d '"' | head -1)"
if [ -z "$ts3_password" ]; then
  echo "Could not parse the ts3 serveradmin password from the logs" >&2
  exit 1
fi

export TSQ_TS3_HOST=127.0.0.1 TSQ_TS3_PORT="$ts3_port" TSQ_TS3_PASSWORD="$ts3_password"
export TSQ_TS6_HOST=127.0.0.1 TSQ_TS6_PORT="$ts6_port" TSQ_TS6_PASSWORD=tsq-ci-password

echo "Running integration suite against ts3 (:$ts3_port) and ts6 (:$ts6_port)..."
uv run --frozen pytest -m integration -q

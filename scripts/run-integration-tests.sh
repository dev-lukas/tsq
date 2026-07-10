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

echo "Waiting for ts3 + ts3strict to become healthy..."
for service in ts3 ts3strict; do
  ok=""
  for _ in $(seq 1 40); do
    state="$($compose ps "$service" --format '{{.Health}}' 2>/dev/null || true)"
    if [ "$state" = "healthy" ]; then ok=1; break; fi
    sleep 3
  done
  [ -n "$ok" ] || { echo "$service did not become healthy in time" >&2; exit 1; }
done

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
ts3_ft_port="$($compose port ts3 30033 | awk -F: '{print $NF}')"
ts6_ft_port="$($compose port ts6 30033 | awk -F: '{print $NF}')"
ts3strict_port="$($compose port ts3strict 10022 | awk -F: '{print $NF}')"
# The ts3 image prints the generated serveradmin password once on first boot:
#   loginname= "serveradmin", password= "XXXX"
parse_password() {
  $compose logs "$1" 2>&1 \
    | grep -oE 'loginname= "serveradmin", password= "[^"]+"' \
    | grep -oE '"[^"]+"$' | tr -d '"' | head -1
}
ts3_password="$(parse_password ts3)"
ts3strict_password="$(parse_password ts3strict)"
if [ -z "$ts3_password" ] || [ -z "$ts3strict_password" ]; then
  echo "Could not parse a serveradmin password from the logs" >&2
  exit 1
fi

export TSQ_TS3_HOST=127.0.0.1 TSQ_TS3_PORT="$ts3_port" TSQ_TS3_PASSWORD="$ts3_password"
export TSQ_TS6_HOST=127.0.0.1 TSQ_TS6_PORT="$ts6_port" TSQ_TS6_PASSWORD=tsq-ci-password
export TSQ_TS3_FT_PORT="$ts3_ft_port" TSQ_TS6_FT_PORT="$ts6_ft_port"
export TSQ_TS3STRICT_HOST=127.0.0.1 TSQ_TS3STRICT_PORT="$ts3strict_port"
export TSQ_TS3STRICT_PASSWORD="$ts3strict_password"

# Run the ENTIRE suite (unit + fake + integration) under one coverage gate:
# with the live tests included, every module - transport included - must be
# covered. This is what enforces "each function has a unit AND a live test".
echo "Running full suite (unit + fake + integration) with coverage gate..."
uv run --frozen pytest -m "not slow" -q --cov=tsq --cov-report=term-missing --cov-fail-under=99

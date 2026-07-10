# tsq

Asyncio TeamSpeak ServerQuery client for **TeamSpeak 3** and **TeamSpeak 6**, over SSH.

TeamSpeak 6 removed the classic raw/telnet ServerQuery — SSH query (port 10022) is the
only line-protocol interface left. `tsq` speaks that protocol against both server
generations with one async API, runs on modern Python (3.12–3.14+), and its whole test
suite executes against real `teamspeak:3.13` and `teamspeaksystems/teamspeak6-server`
containers.

## Why

- [`py-ts3`](https://github.com/benediktschmitt/py-ts3) is unmaintained and imports
  `telnetlib` at module load — removed from the standard library in Python 3.13.
- TeamSpeak 6 servers only offer SSH query (and HTTP WebQuery).
- Bots want asyncio-native ergonomics: awaitable commands, `@client.on` event handlers,
  automatic keepalive and reconnect — the `discord.py` feel.

## Install

```
uv add tsq          # or: pip install tsq
```

Requires Python ≥ 3.12. The only runtime dependency is
[asyncssh](https://asyncssh.readthedocs.io/).

## Usage

One-shot administrative session:

```python
import tsq

async with await tsq.connect("ts.example.com", 10022,
                             password="...", server_id=1) as ts:
    for row in await ts.client_list("uid"):
        print(row["clid"], row["client_nickname"])
    cid = await ts.channel_create("Lounge", channel_flag_permanent=1)
```

Long-running bot with events and automatic reconnect:

```python
client = tsq.Client("ts.example.com", 10022, password="...",
                    server_id=1, register_events="server")

@client.on("cliententerview")
async def on_join(event: tsq.Event) -> None:
    if event.get("reasonid") == "0" and event.get("client_type") == "0":
        print("joined:", event["client_unique_identifier"])

@client.on("clientleftview")
async def on_leave(event: tsq.Event) -> None:
    print("left:", event.get("clid"))

await client.run_forever()   # reconnects with backoff; keepalive automatic
```

Pull-style event consumption (instead of handlers):

```python
async for event in client.events():
    handle(event)
# or: event = await client.wait_for_event(timeout=240)
```

Anything without a typed wrapper goes through the generic escape-safe `exec()`,
including pipelined bulk commands (many parameter blocks, one round trip):

```python
rows = await ts.exec("servergrouplist")
await ts.exec("clientmove", clid=5, cid=42)
await ts.exec("channeladdperm", cid=60, blocks=[
    {"permsid": "i_channel_needed_join_power", "permvalue": 75},
    {"permsid": "i_channel_needed_subscribe_power", "permvalue": 60},
])
```

Wire constants are available as `StrEnum`s that compare directly against
event/row values:

```python
from tsq import ReasonId, TargetMode, ClientType, LEAVE_REASONS

if event["reasonid"] == ReasonId.CONNECT and event["client_type"] == ClientType.VOICE:
    ...
if event.get("reasonid") in LEAVE_REASONS:
    ...
```

File transfer (icons, avatars, channel files) — same API against TS3 and TS6:

```python
ft = tsq.FileTransfer(client)
await ft.upload(icon_bytes, "/icon_3735928559")      # cid=0 = server icons
data = await ft.download("/tsq.bin", cid=42)
rows = await ft.file_list(cid=42, path="/")          # [] for empty dirs
await ft.delete_file("/tsq.bin", cid=42)
```

### Errors

```python
try:
    await ts.use(99)
except tsq.QueryError as e:        # error id != 0; str(e) carries the server msg
    print(e.error_id, e.msg)
except tsq.QueryTimeoutError:      # no response in time (connection is closed)
    ...
except tsq.ConnectionClosedError:  # connection gone
    ...
```

`tsq.FloodError` (a `QueryError`, id 524) signals server flood protection — add your
client's IP to the server's `query_ip_allowlist.txt` to be exempt.

### Defaults worth knowing

- **Keepalive**: automatic `whoami` after 240 s idle (servers kick at ~300 s).
  Configure via `keepalive_interval`; `0` disables.
- **Reconnect** (`run_forever`): exponential backoff 5 s → 300 s; a server message
  containing "banned" waits 300 s. `use`/`servernotifyregister` and `on_ready` re-run
  after every reconnect.
- **Host keys**: verification is off by default (TeamSpeak servers generate ephemeral
  query host keys). Pin one in production: `tsq.connect(..., known_hosts=...)`
  (forwarded to asyncssh).
- **close() sends `quit`**: on TS6 a query client that silently drops the SSH
  connection never produces a `notifyclientleftview`; a clean `quit` does (on both
  generations). See [docs/dialects.md](docs/dialects.md).

## TS3 vs TS6

Probed against real servers — the wire dialects are near-identical, and `tsq`
auto-detects the generation from the greeting (`client.dialect`). All recorded
differences and server-config notes live in [docs/dialects.md](docs/dialects.md).

Enable SSH query on a TS3 server with `TS3SERVER_QUERY_PROTOCOLS=raw,ssh`; on TS6 with
`TSSERVER_QUERY_SSH_ENABLED=1` (password via `TSSERVER_QUERY_ADMIN_PASSWORD`).

## Migrating from py-ts3

| py-ts3 | tsq |
|---|---|
| `TS3ServerConnection("telnet://user:pass@host:10011")` | `await tsq.connect(host, 10022, username=..., password=...)` (SSH) |
| `conn.exec_("clientlist", "uid")` | `await ts.exec("clientlist", "uid")` or `await ts.client_list("uid")` |
| response `resp[0]["cldbid"]` | same shape: `rows[0]["cldbid"]` (`list[dict[str, str]]`) |
| `conn.wait_for_event(timeout=240)` | `await client.wait_for_event(timeout=240)` or `@client.on(...)` |
| `event[0]["reasonid"]` | `event["reasonid"]` (Event is a `Mapping[str, str]`) |
| `conn.send_keepalive()` | automatic (or `await ts.send_keepalive()`) |
| `ts3.query.TS3QueryError` | `tsq.QueryError` (str() still contains the server msg) |
| `ts3.query.TS3TimeoutError` | `tsq.QueryTimeoutError` |
| query builder `.pipe()` | `exec(cmd, blocks=[{...}, {...}], **shared)` |
| `ts3.definitions` constants | `tsq.ReasonId` / `TargetMode` / `ClientType` / `LEAVE_REASONS` |
| `ts3.filetransfer.TS3FileTransfer` | `tsq.FileTransfer` (asyncio, TS3+TS6) |
| manual reconnect loop | `await client.run_forever()` |

## Development

```
uv sync
uv run pytest                        # unit + fake-transport tests (no docker)
./scripts/run-integration-tests.sh   # full suite vs real TS3 + TS6 in docker
uv run ruff check src tests scripts && uv run mypy
```

`scripts/probe_dialect.py` records raw protocol transcripts from a live server —
rerun it when a new TS6 build lands and diff against `tests/unit/fixtures/`.

## License

MIT

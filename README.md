# atsq — **A**synchronous **T**eam**S**peak **Q**uery

[![CI](https://github.com/dev-lukas/atsq/actions/workflows/ci.yml/badge.svg)](https://github.com/dev-lukas/atsq/actions/workflows/ci.yml)
[![Release](https://github.com/dev-lukas/atsq/actions/workflows/release.yml/badge.svg)](https://github.com/dev-lukas/atsq/actions/workflows/release.yml)
[![PyPI](https://img.shields.io/pypi/v/atsq)](https://pypi.org/project/atsq/)
[![Python](https://img.shields.io/pypi/pyversions/atsq)](https://pypi.org/project/atsq/)
[![License](https://img.shields.io/pypi/l/atsq)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

The successor to [py-ts3](https://github.com/benediktschmitt/py-ts3): an
asyncio-native ServerQuery client for **TeamSpeak 3 and TeamSpeak 6** over SSH —
automatic keepalive, reconnect and event dispatch, on modern Python
(3.12–3.14+). Every release is tested live against real TS3 and TS6 servers.

## Install

```
uv add atsq          # or: pip install atsq
```

## Usage

```python
import atsq

client = atsq.Client("ts.example.com", 10022, password="...",
                     server_id=1,                    # or server_port=9987
                     nickname="My Bot",              # re-applied on reconnect
                     register_events=atsq.ALL_EVENTS)

@client.on("cliententerview")
async def on_join(event: atsq.Event) -> None:
    if event["client_type"] == atsq.ClientType.VOICE:
        print("joined:", event["client_unique_identifier"])

@client.on("clientleftview")
async def on_leave(event: atsq.Event) -> None:
    print("left:", event.get("clid"))

await client.run_forever()   # reconnects with backoff; keepalive automatic
```

<details>
<summary><b>One-shot administrative session</b></summary>

```python
async with await atsq.connect("ts.example.com", 10022,
                              password="...", server_id=1) as ts:
    for row in await ts.client_list("uid"):
        print(row["clid"], row["client_nickname"])
    cid = await ts.channel_create("Lounge", channel_flag_permanent=1)
```

Pull-style event consumption instead of handlers:

```python
async for event in client.events():
    handle(event)
# or: event = await client.wait_for_event(timeout=240)
```
</details>

<details>
<summary><b>Generic commands, pipelining and wire constants</b></summary>

Anything without a typed wrapper goes through the escape-safe `exec()`,
including pipelined bulk commands (many parameter blocks, one round trip):

```python
rows = await ts.exec("servergrouplist")
await ts.exec("clientmove", clid=5, cid=42)
await ts.exec("channeladdperm", cid=60, blocks=[
    {"permsid": "i_channel_needed_join_power", "permvalue": 75},
    {"permsid": "i_channel_needed_subscribe_power", "permvalue": 60},
])
```

Wire constants are `StrEnum`s that compare directly against event/row values:

```python
from atsq import ReasonId, TargetMode, ClientType, LEAVE_REASONS

if event["reasonid"] == ReasonId.CONNECT and event["client_type"] == ClientType.VOICE:
    ...
if event.get("reasonid") in LEAVE_REASONS:
    ...
```
</details>

<details>
<summary><b>File transfer (icons, avatars, channel files)</b></summary>

Same API against TS3 and TS6:

```python
ft = atsq.FileTransfer(client)
icon_id = await ft.upload_icon(png_bytes)            # crc32-named, returns the id
data = await ft.download("/file.bin", cid=42)
rows = await ft.file_list(cid=42, path="/")          # [] for empty dirs
await ft.delete_file("/file.bin", cid=42)
```
</details>

<details>
<summary><b>Error handling</b></summary>

```python
try:
    await ts.use(99)
except atsq.QueryError as e:        # error id != 0; str(e) carries the server msg
    print(e.error_id, e.msg)
except atsq.QueryTimeoutError:      # no response in time (connection is closed)
    ...
except atsq.ConnectionClosedError:  # connection gone
    ...
```

`atsq.FloodError` (id 524) is retried automatically (`flood_retries`, default 2).
</details>

<details>
<summary><b>Defaults worth knowing</b></summary>

- **Keepalive**: automatic `whoami` after 240 s idle (servers kick at ~300 s).
  Configure via `keepalive_interval`; `0` disables.
- **Flood protection**: an `error 524` is retried automatically after the wait
  the server asks for (`flood_retries`, default 2; `0` disables). Allowlisted
  IPs (`query_ip_allowlist.txt`) never hit it in the first place.
- **Reconnect** (`run_forever`): exponential backoff 5 s → 300 s; a server
  message containing "banned" waits 300 s. `use`/`servernotifyregister` and
  `on_ready` re-run after every reconnect.
- **Host keys**: verification is off by default (TeamSpeak servers generate
  ephemeral query host keys). Pin one in production:
  `atsq.connect(..., known_hosts=...)` (forwarded to asyncssh).
- **close() sends `quit`**: on TS6 a query client that silently drops the SSH
  connection never produces a `notifyclientleftview`; a clean `quit` does (on
  both generations).
- **Snapshots** work via plain `exec("serversnapshotcreate")` /
  `exec("serversnapshotdeploy", version=..., data=...)` — deploy deselects the
  session; call `use` again afterwards.
</details>

<details>
<summary><b>Migrating from py-ts3</b></summary>

| py-ts3 | atsq |
|---|---|
| `TS3ServerConnection("telnet://user:pass@host:10011")` | `await atsq.connect(host, 10022, username=..., password=...)` (SSH) |
| `conn.exec_("clientlist", "uid")` | `await ts.exec("clientlist", "uid")` or `await ts.client_list("uid")` |
| response `resp[0]["cldbid"]` | same shape: `rows[0]["cldbid"]` (`list[dict[str, str]]`) |
| `conn.wait_for_event(timeout=240)` | `await client.wait_for_event(timeout=240)` or `@client.on(...)` |
| `event[0]["reasonid"]` | `event["reasonid"]` (Event is a `Mapping[str, str]`) |
| `conn.send_keepalive()` | automatic (or `await ts.send_keepalive()`) |
| `ts3.query.TS3QueryError` | `atsq.QueryError` (str() still contains the server msg) |
| `ts3.query.TS3TimeoutError` | `atsq.QueryTimeoutError` |
| query builder `.pipe()` | `exec(cmd, blocks=[{...}, {...}], **shared)` |
| `ts3.definitions` constants | `atsq.ReasonId` / `TargetMode` / `ClientType` / `LEAVE_REASONS` |
| `ts3.filetransfer.TS3FileTransfer` | `atsq.FileTransfer` (asyncio, TS3+TS6) |
| manual reconnect loop | `await client.run_forever()` |

Enable SSH query on a TS3 server with `TS3SERVER_QUERY_PROTOCOLS=raw,ssh`; on
TS6 it is the only line protocol (`TSSERVER_QUERY_SSH_ENABLED=1`, password via
`TSSERVER_QUERY_ADMIN_PASSWORD`).
</details>

## Docs

- [TS3 vs TS6 dialect findings](docs/dialects.md) — probe-verified differences
  between the generations and how atsq handles them.
- [Testing policy](docs/testing.md) — every function is covered by a unit test
  **and** a live test against real servers; CI enforces it with a coverage gate.

## Development

```
uv sync
uv run pytest                        # unit + fake-transport tests (no docker)
./scripts/run-integration-tests.sh   # full suite vs real TS3 + TS6 in docker
```

## License

[MIT](LICENSE)

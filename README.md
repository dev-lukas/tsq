# tsq

Asyncio TeamSpeak ServerQuery client for **TeamSpeak 3** and **TeamSpeak 6**, over SSH.

TeamSpeak 6 removed the classic raw/telnet ServerQuery entirely — SSH query (port 10022)
is the only line-protocol interface left. `tsq` speaks that protocol against both server
generations with a single async API, and runs on modern Python (3.12–3.14+) without any
dependency on the removed stdlib `telnetlib`.

> Status: pre-alpha. The API freezes after the TS3-vs-TS6 dialect probe milestone.

## Why

- [`py-ts3`](https://github.com/benediktschmitt/py-ts3) is unmaintained and imports
  `telnetlib` at module load, which was removed in Python 3.13.
- TeamSpeak 6 servers only offer SSH query and HTTP WebQuery.
- Bots want asyncio-native ergonomics (background event dispatch, awaitable commands,
  automatic keepalive and reconnect) — the same feel as `discord.py`.

## Planned usage

```python
import tsq

async with await tsq.connect("ts.example.com", 10022,
                             username="serveradmin", password="...",
                             server_id=1) as ts:
    for row in await ts.client_list():
        print(row["clid"], row["client_nickname"])
```

## Development

```
uv sync
uv run pytest              # unit + fake-transport tests
./scripts/run-integration-tests.sh   # spins up real TS3 + TS6 docker servers
```

## License

MIT

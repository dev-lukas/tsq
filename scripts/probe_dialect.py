"""Dialect probe: record raw ServerQuery transcripts from a real server.

Connects over SSH (like tsq does) but reads raw bytes with its own loop, so
the recorded transcript shows the true wire framing (``\\n\\r`` vs ``\\n``),
greeting shape, response formats and event emission of the probed server.

Usage:
    uv run python scripts/probe_dialect.py \\
        --host 127.0.0.1 --port 32768 --username serveradmin \\
        --password secret --label ts3 --outdir tests/unit/fixtures

Writes ``probe_<label>.log`` (annotated raw transcript) into --outdir and a
human summary to stdout. Intended for throwaway docker servers only: it
creates channels and sends messages.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

import asyncssh

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tsq.protocol import parse_data_line

ERROR_LINE_RE = re.compile(rb"(?:^|\n\r?)error id=\d+ msg=[^\n\r]*(?:\n\r?|$)")
READ_CHUNK = 4096

#: asyncssh extra-info keys worth recording for crypto compatibility notes.
SSH_INFO_KEYS = (
    "server_version",
    "kex_alg",
    "server_host_key_alg",
    "send_cipher",
    "recv_cipher",
    "send_mac",
    "recv_mac",
    "send_compression",
    "recv_compression",
)


class Probe:
    def __init__(self, host: str, port: int, username: str, password: str) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.log: list[str] = []
        self._buffer = b""

    async def connect(self) -> None:
        self.conn = await asyncssh.connect(
            self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            known_hosts=None,
            connect_timeout=15,
        )
        self.stdin, self.stdout, _ = await self.conn.open_session(
            term_type=None, encoding=None
        )
        info = {}
        for key in SSH_INFO_KEYS:
            try:
                value = self.conn.get_extra_info(key)
            except Exception:
                value = None
            if value is not None:
                info[key] = value
        self.note(f"ssh extra_info: {info}")

    def note(self, text: str) -> None:
        self.log.append(f"# {text}")
        print(f"  {text}")

    async def _read_some(self, timeout: float) -> bytes:
        try:
            async with asyncio.timeout(timeout):
                return await self.stdout.read(READ_CHUNK)
        except TimeoutError:
            return b""

    async def drain(self, quiet: float = 1.5, label: str = "drain") -> bytes:
        """Read until the server has been quiet for *quiet* seconds."""
        collected = b""
        while True:
            chunk = await self._read_some(quiet)
            if not chunk:
                break
            collected += chunk
        if collected:
            self.log.append(f"< [{label}] {collected!r}")
        return collected

    async def command(self, line: bytes, timeout: float = 8.0) -> bytes:
        """Send one command, return raw bytes up to (and incl.) its error line."""
        self.log.append(f"> {line!r}")
        self.stdin.write(line + b"\n\r")
        await self.stdin.drain()
        raw = self._buffer
        self._buffer = b""
        deadline = asyncio.get_running_loop().time() + timeout
        while not ERROR_LINE_RE.search(raw):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                self.log.append(f"< TIMEOUT after {timeout}s, partial: {raw!r}")
                return raw
            chunk = await self._read_some(remaining)
            if chunk:
                raw += chunk
        match = ERROR_LINE_RE.search(raw)
        assert match is not None
        end = match.end()
        raw, self._buffer = raw[:end], raw[end:]
        self.log.append(f"< {raw!r}")
        return raw

    @staticmethod
    def rows(raw: bytes) -> list[dict[str, str]]:
        """Best-effort parse of the data lines of a recorded response."""
        rows: list[dict[str, str]] = []
        for line in re.split(rb"\n\r?", raw):
            line = line.strip(b"\r")
            if not line or line.startswith(b"error ") or line.startswith(b"notify"):
                continue
            rows.extend(parse_data_line(line))
        return rows

    async def close(self) -> None:
        self.conn.close()


async def probe_server(host: str, port: int, username: str, password: str) -> list[str]:
    a = Probe(host, port, username, password)
    print("connecting (connection A)...")
    await a.connect()

    a.note("--- greeting ---")
    await a.drain(quiet=2.0, label="greeting")

    a.note("--- basics ---")
    await a.command(b"use sid=1")
    whoami_raw = await a.command(b"whoami")
    me = a.rows(whoami_raw)
    my_clid = me[0].get("client_id", "") if me else ""
    my_cluid = me[0].get("client_unique_identifier", "") if me else ""
    await a.command(b"version")

    a.note("--- client info chain ---")
    await a.command(b"clientlist -uid")
    if my_clid:
        await a.command(f"clientinfo clid={my_clid}".encode())
    my_dbid = ""
    if my_cluid:
        dbid_raw = await a.command(
            b"clientgetdbidfromuid cluid=" + my_cluid.replace("/", r"\/").encode()
        )
        dbid_rows = a.rows(dbid_raw)
        my_dbid = dbid_rows[0].get("cldbid", "") if dbid_rows else ""
    if my_dbid:
        await a.command(f"servergroupsbyclientid cldbid={my_dbid}".encode())

    a.note("--- group / channel landscape ---")
    sgl_raw = await a.command(b"servergrouplist")
    await a.command(b"channelgrouplist")
    await a.command(b"channellist")

    a.note("--- escaping round-trip via channelcreate ---")
    # raw name: tsq probe |pipe| a/b\c   end   (with a tab)
    escaped_name = rb"tsq\sprobe\s\p pipe\p\sa\/b\\c\tend"
    create_raw = await a.command(
        b"channelcreate channel_name=" + escaped_name + b" channel_flag_permanent=1"
    )
    create_rows = a.rows(create_raw)
    cid = create_rows[0].get("cid", "") if create_rows else ""
    await a.command(b"channellist")

    if cid:
        a.note("--- channel perms / move ---")
        await a.command(
            f"channeladdperm cid={cid} permsid=i_channel_needed_join_power permvalue=75".encode()
        )
        if my_dbid:
            await a.command(
                f"channelclientaddperm cid={cid} cldbid={my_dbid} "
                f"permsid=i_channel_join_power permvalue=100".encode()
            )
        second_raw = await a.command(
            b"channelcreate channel_name=tsq\\sprobe\\ssecond channel_flag_permanent=1"
        )
        second_rows = a.rows(second_raw)
        cid2 = second_rows[0].get("cid", "") if second_rows else ""
        if cid2:
            await a.command(f"channelmove cid={cid2} cpid={cid}".encode())
        if my_dbid:
            channel_admin_gid = ""
            # setclientchannelgroup needs a channel group id; take the first
            # from channelgrouplist output recorded above? Re-run and parse.
            cgl_rows = a.rows(await a.command(b"channelgrouplist"))
            for row in cgl_rows:
                if row.get("type") == "1":  # regular (non-template) groups
                    channel_admin_gid = row.get("cgid", "")
                    break
            if not channel_admin_gid and cgl_rows:
                channel_admin_gid = cgl_rows[0].get("cgid", "")
            if channel_admin_gid:
                await a.command(
                    f"setclientchannelgroup cgid={channel_admin_gid} "
                    f"cid={cid} cldbid={my_dbid}".encode()
                )

    if my_dbid:
        a.note("--- server group add/del ---")
        sg_rows = a.rows(sgl_raw)
        target_sgid = ""
        for row in sg_rows:
            # type=1 regular groups; avoid the query group we're in.
            if row.get("type") == "1" and "admin" not in row.get("name", "").lower():
                target_sgid = row.get("sgid", "")
                break
        if target_sgid:
            await a.command(
                f"servergroupaddclient sgid={target_sgid} cldbid={my_dbid}".encode()
            )
            await a.command(
                f"servergroupdelclient sgid={target_sgid} cldbid={my_dbid}".encode()
            )

    a.note("--- messaging / kick / errors ---")
    if my_clid:
        await a.command(
            b"sendtextmessage targetmode=1 target=" + my_clid.encode()
            + rb" msg=tsq\sprobe\s\p a\/b\\c\tend"
        )
        await a.command(
            f"clientkick clid={my_clid} reasonid=5 reasonmsg=tsq\\sprobe".encode()
        )
    await a.command(b"thisisnotacommand")

    a.note("--- flood behaviour (30 rapid whoami) ---")
    flood_errors = 0
    for _ in range(30):
        raw = await a.command(b"whoami", timeout=5.0)
        if b"error id=0" not in raw:
            flood_errors += 1
    a.note(f"flood result: {flood_errors}/30 non-ok responses")

    a.note("--- event registration ---")
    await a.command(b"servernotifyregister event=server")
    await a.command(b"servernotifyregister event=textserver")

    a.note("--- events from a second query client (connection B) ---")
    b = Probe(host, port, username, password)
    await b.connect()
    await b.drain(quiet=2.0, label="greeting-b")
    await b.command(b"use sid=1")
    await b.command(b"sendtextmessage targetmode=3 msg=tsq\\sprobe\\shello")
    await b.close()
    a.note("connection B used sid=1, sent a server text message, disconnected")
    events_raw = await a.drain(quiet=4.0, label="events-after-b")
    enter = b"notifycliententerview" in events_raw
    left = b"notifyclientleftview" in events_raw
    text = b"notifytextmessage" in events_raw
    a.note(f"events seen: enterview={enter} leftview={left} textmessage={text}")

    await a.command(b"quit", timeout=3.0)
    await a.close()
    return a.log


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--username", default="serveradmin")
    parser.add_argument("--password", required=True)
    parser.add_argument("--label", required=True, help="e.g. ts3 / ts6")
    parser.add_argument("--outdir", default="tests/unit/fixtures")
    args = parser.parse_args()

    log = asyncio.run(probe_server(args.host, args.port, args.username, args.password))

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    outfile = outdir / f"probe_{args.label}.log"
    outfile.write_text("\n".join(log) + "\n", encoding="utf-8")
    print(f"\ntranscript written to {outfile} ({len(log)} entries)")


if __name__ == "__main__":
    main()

# TS3 vs TS6 ServerQuery dialects

Findings of `scripts/probe_dialect.py`, run 2026-07-09 against real servers:

| | TS3 | TS6 |
|---|---|---|
| Image | `teamspeak:3.13` | `teamspeaksystems/teamspeak6-server:latest` |
| Server version (`version`) | `3.13.7 build=1655727713` | `6.0.0-beta11 build=1781522651` |
| SSH server | libssh 0.8.4 | libssh 0.11.3 |

Raw transcripts: `tests/unit/fixtures/probe_ts3.log` / `probe_ts6.log`.

## Verdict

**The wire protocol is effectively identical.** Every difference found is
additive or cosmetic; no command, error code, event, framing or escaping
difference affects tsq's core. All divergence handling lives in
`src/tsq/dialect.py`.

## Identical on both generations (probe-verified)

- **SSH transport**: password auth as the query login at the SSH layer; both
  negotiate `chacha20-poly1305@openssh.com` with stock asyncssh — **no legacy
  kex/cipher configuration needed** on either generation.
- **Greeting**: two lines, and the first line is literally `TS3` **on both**
  (TS6 keeps it, presumably for tool compatibility).
- **Framing**: `\n\r` line terminator everywhere (responses and events).
- **Escaping**: identical table; `channelcreate` round-trip of
  `tsq probe | pipe| a/b\c<TAB>end` returns byte-identical escapes on both.
  Caveat (both generations, server-side): **channel names sanitize control
  characters away** (a `\t` in `channel_name` is silently dropped), while
  **text messages preserve them** — escaping itself is lossless; the
  sanitization is per-field server policy.
- **Command set**: the full firephenix parity set (`use`,
  `servernotifyregister`, `clientlist -uid`, `clientinfo`,
  `clientgetdbidfromuid`, `servergroupsbyclientid`,
  `servergroupadd/delclient`, `setclientchannelgroup`, `channelcreate`,
  `channeladdperm`, `channelclientaddperm`, `channelmove`,
  `sendtextmessage`, `clientkick`, plus `whoami`/`version`/lists) — identical
  request/response shapes and identical error ids (`0`, `256` command not
  found, `512` invalid clientID, `516` invalid client type, `2563` empty
  result set).
- **Events**: query clients DO trigger real events on both —
  `notifycliententerview` (reasonid=0), `notifyclientleftview`,
  `notifytextmessage` (targetmode=3) with identical field layouts.
  Integration tests can exercise the event path with a second query
  connection; no fallback needed. (But see the leftview caveat below.)
- **Flood**: with the client's subnet in the query IP allowlist, 30
  back-to-back commands produce zero `error id=524` on both.
- `client_myteamspeak_id` is present in `clientinfo` on both (needed by the
  firephenix identity bridge).

## Differences (all additive/cosmetic)

| Aspect | TS3 | TS6 | tsq handling |
|---|---|---|---|
| Greeting line 2 | `Welcome to the TeamSpeak 3 ServerQuery interface, ...` | `Welcome to the TeamSpeak ServerQuery interface, ...` (no "3") | `sniff_dialect()` keys on the `TeamSpeak 3 ` prefix |
| `whoami` | — | adds `virtualserver_uuid=<uuid>` | none needed (schema-free rows) |
| `clientinfo` / `notifycliententerview` | — | adds `client_is_streaming=0` | none needed |
| `client_unique_identifier` length | SHA-1 base64 (28 chars) | SHA-256 base64 (44 chars) for voice clients | none needed (opaque string) |
| Server config | `TS3SERVER_*` env, password only in first-boot log, allowlist at `/var/ts3server/query_ip_allowlist.txt`, query protocols opt-in `raw,ssh` | `TSSERVER_*` env, deterministic `TSSERVER_QUERY_ADMIN_PASSWORD`, allowlist at `/var/tsserver/query_ip_allowlist.txt`, `TSSERVER_QUERY_SSH_ENABLED=1`, no raw protocol at all | `docker/docker-compose.test.yml` |
| **leftview on disconnect** (query clients) | emitted immediately for `quit` (reasonid=8), TCP abort/RST (reasonid=3) **and graceful SSH close** (reasonid=3) | emitted for `quit` (8) and abort/RST (3); **NOT emitted at all for a graceful SSH close** (verified: nothing within 65s) | `RawConnection.close()` sends a best-effort `quit` before closing, so tsq disconnects are observable on both generations |

## Consequences for the API (freeze decisions)

1. `Dialect` stays, but the quirks table currently carries no behavioural
   differences — it exists as the containment point for future beta drift.
2. AUTO detection sniffs the welcome line; anything unrecognised is treated
   as TS6 (the moving side). `version` remains available for explicit checks.
3. Rows stay `dict[str, str]` — the additive TS6 fields arrive for free.
4. The integration suite runs the *same* assertions against both servers,
   including real event round-trips via a second query connection.

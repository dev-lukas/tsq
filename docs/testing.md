# Testing policy

**Every command and function tsq ships must be covered twice: by a unit/fake
test (no network) and by a live integration test against real servers.**
CI enforces this with a coverage gate: the integration job runs the *entire*
suite (unit + fake + integration) with `--cov-fail-under=99`. Only explicitly
marked network-race branches (`pragma: no cover`) are exempt. Current level:
~99.9%, with every module at 100% except three unreachable race branches in
`transport.py`.

## Tiers

| Tier | Location | Needs | Covers |
|---|---|---|---|
| Unit | `tests/unit/` | nothing | escape/protocol/errors/definitions/events as pure functions, incl. **fixture replay**: every byte both real servers sent during the recorded probe sessions must parse (`test_fixtures_replay.py`) |
| Fake | `tests/fake/` | nothing | `RawConnection`/`Client`/`FileTransfer` logic driven through `FakeTransport` (scriptable responder) and `FakeFtServer` (real asyncio TCP data channel): serialization, events, timeouts, flood retry, reconnect/backoff/banned, keepalive, wire bytes of every typed wrapper |
| Integration | `tests/integration/` | docker | the same assertions against **both** real servers (`teamspeak:3.13` and `teamspeaksystems/teamspeak6-server`, parametrized fixture), plus `ts3strict` — a non-allowlisted TS3 whose real flood limits exercise the 524 auto-retry live |

## Per-API live coverage map

Every public callable and its integration test (all parametrized over TS3+TS6
unless noted):

| API | Live test |
|---|---|
| `connect` / `Client.start/close` | used by every test; failure paths in `TestErrors` |
| `use` / `server_id` | `TestSession`, `TestErrors.test_invalid_server_id` |
| `server_port` | `TestClientOptions.test_select_server_by_voice_port` |
| `nickname` | `TestClientOptions.test_nickname_and_multi_event_registration` |
| `register_events` (multi, `ALL_EVENTS`) | same |
| `whoami` / `version` / `dialect` | `TestSession.test_dialect_detection` |
| `client_list` | `TestSession.test_clientlist_contains_self` |
| `client_info` / `client_dbid_from_uid` / `server_groups_by_client` | `TestSession.test_client_info_chain` |
| `server_group_add/del_client`, `set_client_channel_group`, `channel_client_add_perm`, `client_kick` | `TestQueryClientContracts` (pins the live error ids for query clients: 512/2563/516) |
| `channel_create` | escaping round-trip + several others |
| `channel_add_perm` / `channel_move` | `TestChannelWrappers` |
| `exec` + `blocks` pipelining | `TestPipelining` (verified via `channelpermlist`) |
| `send_text_message` | `TestEvents.test_text_message_event` (control chars preserved) |
| `send_keepalive` | `TestSession.test_send_keepalive` |
| `wait_for_event` / `events()` / `@on` / `run_forever` | `TestEvents` incl. `test_run_forever_dispatch_and_live_reconnect` (server-side disconnect → auto-reconnect) |
| `QueryError` / error surfacing | `TestErrors`, `TestEscaping.test_error_msg_unescaped` |
| `FloodError` + auto-retry | `test_flood_live.py` against `ts3strict` |
| `ReasonId` / `LEAVE_REASONS` / `ClientType` | asserted inside the event/clientlist tests |
| `FileTransfer.upload/download/file_list/file_info/delete_file/create_directory/rename_file` | `TestFileTransfer` round-trips |
| `upload_icon/download_icon/delete_icon` | `TestFileTransfer.test_icon_round_trip` |
| `SshTransport` lifecycle | `TestTransportLifecycle` + implicitly everything |
| snapshots via `exec` | `TestSnapshots` (incl. the deploy-deselects-session caveat) |

## Running

```
uv run pytest                        # unit + fake only (~2 s)
./scripts/run-integration-tests.sh   # full suite + live servers + coverage gate
```

When adding a feature: add its unit/fake test AND its live test in the same
change — the coverage gate will fail the integration job otherwise.

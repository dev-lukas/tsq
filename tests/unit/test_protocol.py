import pytest

from tsq.protocol import (
    ErrorLine,
    is_error_line,
    is_event_line,
    parse_data_block,
    parse_data_line,
    parse_error_line,
    render_command,
)


class TestRenderCommand:
    """Exact wire bytes for every command firephenix-backend uses (parity set)."""

    def test_use(self) -> None:
        assert render_command("use", sid=1) == b"use sid=1"

    def test_servernotifyregister(self) -> None:
        assert (
            render_command("servernotifyregister", event="server")
            == b"servernotifyregister event=server"
        )

    def test_clientlist(self) -> None:
        assert render_command("clientlist") == b"clientlist"
        assert render_command("clientlist", "uid") == b"clientlist -uid"
        assert render_command("clientlist", "uid", "away") == b"clientlist -uid -away"

    def test_clientinfo(self) -> None:
        assert render_command("clientinfo", clid=42) == b"clientinfo clid=42"

    def test_clientgetdbidfromuid(self) -> None:
        assert (
            render_command("clientgetdbidfromuid", cluid="gZ7K1zAlGXHphTRl0lGIikB6/aE=")
            == rb"clientgetdbidfromuid cluid=gZ7K1zAlGXHphTRl0lGIikB6\/aE="
        )

    def test_servergroupsbyclientid(self) -> None:
        assert (
            render_command("servergroupsbyclientid", cldbid=7)
            == b"servergroupsbyclientid cldbid=7"
        )

    def test_servergroupaddclient(self) -> None:
        assert (
            render_command("servergroupaddclient", sgid=13, cldbid=7)
            == b"servergroupaddclient sgid=13 cldbid=7"
        )

    def test_servergroupdelclient(self) -> None:
        assert (
            render_command("servergroupdelclient", sgid=13, cldbid=7)
            == b"servergroupdelclient sgid=13 cldbid=7"
        )

    def test_setclientchannelgroup(self) -> None:
        assert (
            render_command("setclientchannelgroup", cgid=5, cid=51, cldbid=7)
            == b"setclientchannelgroup cgid=5 cid=51 cldbid=7"
        )

    def test_channelcreate(self) -> None:
        assert (
            render_command(
                "channelcreate",
                channel_name="Max's Channel",
                cpid=51,
                channel_flag_permanent=1,
            )
            == rb"channelcreate channel_name=Max's\sChannel cpid=51 channel_flag_permanent=1"
        )

    def test_channeladdperm(self) -> None:
        assert (
            render_command(
                "channeladdperm", cid=60, permsid="i_channel_needed_join_power", permvalue=75
            )
            == b"channeladdperm cid=60 permsid=i_channel_needed_join_power permvalue=75"
        )

    def test_channelclientaddperm(self) -> None:
        assert (
            render_command(
                "channelclientaddperm",
                cid=60,
                cldbid=7,
                permsid="i_channel_join_power",
                permvalue=100,
            )
            == b"channelclientaddperm cid=60 cldbid=7 permsid=i_channel_join_power permvalue=100"
        )

    def test_channelmove(self) -> None:
        assert render_command("channelmove", cid=60, cpid=47) == b"channelmove cid=60 cpid=47"

    def test_sendtextmessage(self) -> None:
        assert (
            render_command("sendtextmessage", targetmode=1, target=42, msg="Dein Code: 123 456")
            == rb"sendtextmessage targetmode=1 target=42 msg=Dein\sCode:\s123\s456"
        )

    def test_clientkick(self) -> None:
        assert (
            render_command("clientkick", clid=42, reasonid=5, reasonmsg="VPN nicht erlaubt")
            == rb"clientkick clid=42 reasonid=5 reasonmsg=VPN\snicht\serlaubt"
        )

    def test_login_and_whoami(self) -> None:
        assert (
            render_command("login", client_login_name="serveradmin", client_login_password="p w")
            == rb"login client_login_name=serveradmin client_login_password=p\sw"
        )
        assert render_command("whoami") == b"whoami"

    def test_none_params_skipped(self) -> None:
        assert render_command("clientkick", clid=1, reasonmsg=None) == b"clientkick clid=1"

    def test_bool_renders_as_int(self) -> None:
        assert render_command("channeledit", channel_flag_permanent=True) == (
            b"channeledit channel_flag_permanent=1"
        )

    def test_list_param_renders_piped(self) -> None:
        assert (
            render_command("servergroupdelclient", sgid=[13, 14], cldbid=7)
            == b"servergroupdelclient sgid=13|sgid=14 cldbid=7"
        )

    def test_blocks_pipeline_with_shared_params(self) -> None:
        assert render_command(
            "channeladdperm",
            cid=60,
            blocks=[
                {"permsid": "i_channel_needed_join_power", "permvalue": 75},
                {"permsid": "i_channel_join_power", "permvalue": 100},
            ],
        ) == (
            b"channeladdperm cid=60 permsid=i_channel_needed_join_power permvalue=75"
            b"|permsid=i_channel_join_power permvalue=100"
        )

    def test_blocks_without_shared_params(self) -> None:
        assert render_command(
            "clientkick",
            blocks=[{"clid": 1}, {"clid": 2}],
            reasonid=None,
        ) == b"clientkick clid=1|clid=2"

    def test_blocks_values_escaped(self) -> None:
        assert render_command(
            "channeledit",
            blocks=[{"channel_name": "a b"}, {"channel_name": "c|d"}],
        ) == (rb"channeledit channel_name=a\sb|channel_name=c\pd")

    def test_blocks_with_options(self) -> None:
        assert render_command(
            "servergroupaddperm",
            "continueonerror",
            sgid=13,
            blocks=[{"permsid": "a", "permvalue": 1}, {"permsid": "b", "permvalue": 2}],
        ) == (
            b"servergroupaddperm sgid=13 permsid=a permvalue=1"
            b"|permsid=b permvalue=2 -continueonerror"
        )

    def test_empty_blocks_is_plain_command(self) -> None:
        assert render_command("whoami", blocks=[]) == b"whoami"
        assert render_command("use", sid=1, blocks=None) == b"use sid=1"

    def test_empty_block_rejected(self) -> None:
        with pytest.raises(ValueError):
            render_command("clientkick", blocks=[{"clid": 1}, {}])

    def test_invalid_command_name_rejected(self) -> None:
        with pytest.raises(ValueError):
            render_command("client info")
        with pytest.raises(ValueError):
            render_command("")


class TestLineClassification:
    def test_event_lines(self) -> None:
        assert is_event_line(b"notifycliententerview cfid=0 ctid=1 reasonid=0 clid=2")
        assert is_event_line(b"notifyclientleftview reasonid=8 clid=2")
        assert not is_event_line(b"clid=1 client_nickname=A")
        assert not is_event_line(b"error id=0 msg=ok")

    def test_error_lines(self) -> None:
        assert is_error_line(b"error id=0 msg=ok")
        assert is_error_line(b"error id=524 msg=client\\sis\\sflooding")
        assert not is_error_line(b"errortest=1")
        assert not is_error_line(b"notifycliententerview reasonid=0")


class TestParseErrorLine:
    def test_ok(self) -> None:
        parsed = parse_error_line(b"error id=0 msg=ok")
        assert parsed == ErrorLine(id=0, msg="ok", extra={})
        assert parsed.ok

    def test_flood(self) -> None:
        parsed = parse_error_line(b"error id=524 msg=client\\sis\\sflooding")
        assert parsed.id == 524
        assert parsed.msg == "client is flooding"
        assert not parsed.ok

    def test_permission_error_with_extra(self) -> None:
        parsed = parse_error_line(
            b"error id=2568 msg=insufficient\\sclient\\spermissions failed_permid=4"
        )
        assert parsed.id == 2568
        assert parsed.msg == "insufficient client permissions"
        assert parsed.extra == {"failed_permid": "4"}

    def test_rejects_non_error_line(self) -> None:
        with pytest.raises(ValueError):
            parse_error_line(b"clid=1")


class TestParseDataLine:
    def test_single_row(self) -> None:
        assert parse_data_line(b"clid=1 client_nickname=Alice") == [
            {"clid": "1", "client_nickname": "Alice"}
        ]

    def test_multi_row_pipe(self) -> None:
        assert parse_data_line(b"clid=1 client_nickname=A|clid=2 client_nickname=B\\sC") == [
            {"clid": "1", "client_nickname": "A"},
            {"clid": "2", "client_nickname": "B C"},
        ]

    def test_value_containing_equals(self) -> None:
        row = parse_data_line(b"client_unique_identifier=gZ7K1zAlGXHphTRl0lGIik=")[0]
        assert row["client_unique_identifier"] == "gZ7K1zAlGXHphTRl0lGIik="

    def test_empty_value_and_flag(self) -> None:
        assert parse_data_line(b"key= flag other=1") == [
            {"key": "", "flag": "", "other": "1"}
        ]

    def test_unescaping_applied_to_values(self) -> None:
        row = parse_data_line(rb"channel_name=TeamSpeak\s]\p[\sServer")[0]
        assert row["channel_name"] == "TeamSpeak ]|[ Server"

    def test_empty_line_yields_nothing(self) -> None:
        assert parse_data_line(b"") == []

    def test_parse_data_block_flattens(self) -> None:
        assert parse_data_block([b"clid=1", b"clid=2|clid=3"]) == [
            {"clid": "1"},
            {"clid": "2"},
            {"clid": "3"},
        ]

    def test_whoami_shape(self) -> None:
        line = (
            b"virtualserver_status=online virtualserver_id=1 "
            b"client_id=5 client_channel_id=1 client_nickname=serveradmin\\sfrom\\s172.19.0.5"
        )
        row = parse_data_line(line)[0]
        assert row["client_id"] == "5"
        assert row["client_nickname"] == "serveradmin from 172.19.0.5"

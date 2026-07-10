from atsq.escape import escape, unescape

# Every single-character mapping from the ServerQuery manual.
TABLE = [
    ("\\", r"\\"),
    ("/", r"\/"),
    (" ", r"\s"),
    ("|", r"\p"),
    ("\a", r"\a"),
    ("\b", r"\b"),
    ("\f", r"\f"),
    ("\n", r"\n"),
    ("\r", r"\r"),
    ("\t", r"\t"),
    ("\v", r"\v"),
]


def test_full_table_escape() -> None:
    for raw, escaped in TABLE:
        assert escape(raw) == escaped, raw


def test_full_table_unescape() -> None:
    for raw, escaped in TABLE:
        assert unescape(escaped) == raw, escaped


def test_manual_examples() -> None:
    assert escape("Hello World") == r"Hello\sWorld"
    assert escape("TeamSpeak ]|[ Server") == r"TeamSpeak\s]\p[\sServer"
    assert unescape(r"TeamSpeak\s]\p[\sServer") == "TeamSpeak ]|[ Server"


def test_ordering_trap_backslash_first() -> None:
    # A literal backslash followed by an 's' must NOT round-trip into a space.
    assert escape("\\s") == r"\\s"
    assert unescape(r"\\s") == "\\s"
    # A literal "\p" sequence in raw text.
    assert unescape(escape("a\\pb")) == "a\\pb"


def test_round_trip_corpus() -> None:
    corpus = [
        "",
        "plain",
        "with space",
        "pipe|slash/back\\slash",
        "tabs\tand\nnewlines\rand\x0bvertical",
        "bell\aback\bspace\ffeed",
        "unique_id=gZ7K1zAlGXHphTRl0lGIikB6/aE=",
        "über größe ünïcode 😀",
        "\\\\double\\\\",
        "  leading and trailing  ",
        "all| of/ the\\ specials\tat once\n",
    ]
    for value in corpus:
        assert unescape(escape(value)) == value, value


def test_escaped_text_stays_stable() -> None:
    # Escaping is idempotent-safe in the sense that escaping escaped text
    # and unescaping twice returns the middle form.
    once = escape("a b")
    twice = escape(once)
    assert unescape(twice) == once
    assert unescape(unescape(twice)) == "a b"

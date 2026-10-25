from studgroup.db_copy import async_url, fingerprint, host_of


def test_dashboard_urls_become_asyncpg_urls_with_ssl():
    assert (
        async_url("postgresql://u:p@host:5432/db?sslmode=require")
        == "postgresql+asyncpg://u:p@host:5432/db?ssl=require"
    )
    assert async_url("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert async_url("postgresql+asyncpg://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"


def test_the_host_never_includes_the_password():
    assert host_of("postgresql://user:secret@db.example.com:5432/app") == "db.example.com/app"


def test_a_row_fingerprint_is_stable_and_sees_every_change():
    row = ("a", 1, {"k": [1, 2]}, b"\x00\x01")
    assert fingerprint(row) == fingerprint(("a", 1, {"k": [1, 2]}, b"\x00\x01"))
    assert fingerprint(row) != fingerprint(("a", 1, {"k": [1, 3]}, b"\x00\x01"))
    assert fingerprint(row) != fingerprint(("a", 1, {"k": [1, 2]}, b"\x00\x02"))

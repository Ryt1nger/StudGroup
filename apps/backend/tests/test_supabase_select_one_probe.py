"""Safety checks for the standalone migration connectivity probe."""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

PROBE = Path(__file__).resolve().parents[1] / "probes" / "supabase_select_one.py"
spec = importlib.util.spec_from_file_location("supabase_select_one_probe", PROBE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_probe_rejects_other_hosts_and_missing_ca(tmp_path):
    ca = tmp_path / "ca.crt"
    ca.write_text("test")
    base = "postgresql+asyncpg://probe.egbodayfttbiznwtfiih:secret@{host}:5432/postgres"
    with pytest.raises(ValueError, match="verified session pooler"):
        module.settings(
            {"SUPABASE_PROBE_URL": base.format(host="other.example"), "SUPABASE_CA_FILE": str(ca)}
        )
    with pytest.raises(ValueError, match="CA file is missing"):
        module.settings(
            {
                "SUPABASE_PROBE_URL": base.format(host=module.POOLER_HOST),
                "SUPABASE_CA_FILE": str(tmp_path / "missing"),
            }
        )


def test_probe_runs_only_select_one_and_disposes(monkeypatch, tmp_path):
    calls = []

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def scalar(self, statement):
            calls.append(str(statement))
            return 1

    class Engine:
        def connect(self):
            calls.append("connect")
            return Connection()

        async def dispose(self):
            calls.append("dispose")

    monkeypatch.setattr(
        module.ssl, "create_default_context", lambda **_: SimpleNamespace(check_hostname=None)
    )
    monkeypatch.setattr(module, "create_async_engine", lambda *_args, **_kwargs: Engine())
    asyncio.run(module.probe("unused", tmp_path / "ca.crt"))
    assert calls == ["connect", "SELECT 1", "dispose"]

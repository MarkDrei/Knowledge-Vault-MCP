import pytest
from starlette.testclient import TestClient

from knowledge_vault_mcp.auth.passwords import hash_password
from knowledge_vault_mcp.config import Settings
from knowledge_vault_mcp.server import create_app

BASE = "http://localhost:8000"
PASSWORD = "correct horse battery staple"
STATIC_TOKEN = "static-test-token-0123456789"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture(scope="session")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture
def settings(tmp_path, password_hash) -> Settings:
    return Settings(
        _env_file=None,
        public_url=BASE,
        owner_password_hash=password_hash,
        auth_token=STATIC_TOKEN,
        state_db_path=tmp_path / "state.db",
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), base_url=BASE, follow_redirects=False) as c:
        yield c

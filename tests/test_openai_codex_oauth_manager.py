import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.openai_codex_oauth_manager import (  # noqa: E402
    DEFAULT_CALLBACK_PORT,
    OpenAICodexOAuthManager,
)


class _DummyVault:
    def exists(self, key):
        return False


def test_generate_auth_url_uses_supported_codex_scopes():
    manager = OpenAICodexOAuthManager(vault=_DummyVault(), port=DEFAULT_CALLBACK_PORT)

    auth_url, state = manager.generate_auth_url()

    parsed = urlparse(auth_url)
    params = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "auth.openai.com"
    assert parsed.path == "/oauth/authorize"
    assert state
    assert params["scope"] == ["openid profile email offline_access"]
    assert params["redirect_uri"] == [f"http://localhost:{DEFAULT_CALLBACK_PORT}/auth/callback"]

"""
Tests for local_models.fetch_model — download + checksum verification.
Prompt 9: fetch_model.py.
"""

import hashlib
import http.server
import pathlib
import threading
import pytest

from local_models.fetch_model import (
    fetch_model,
    verify_checksum,
    model_path,
    is_model_present,
    FetchError,
    _CHUNK_SIZE,
)
from local_models.lockfile import LockfileError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_file(path, content=b"test model data"):
    """Write binary content and return its SHA-256 hex digest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _make_lockfile_data(filename="test-model.gguf", size_mb=1, sha_hash=None, url=None):
    """Return a valid lockfile dict with overrides for testing."""
    return {
        "model": {
            "name": "test-model",
            "version": "1.0",
            "quantization": "Q4_K_M",
            "format": "gguf",
            "filename": filename,
            "size_mb": size_mb,
            "checksum": {
                "algorithm": "sha256",
                "hash": sha_hash or ("a" * 64),
            },
            "sources": [
                {"url": url or "http://localhost/fake.gguf", "provider": "test"}
            ],
            "license": {"model": "Apache-2.0", "runtime": "MIT"},
        },
        "runtime": {
            "engine": "llama.cpp",
            "context_length": 4096,
            "threads": 4,
            "gpu_layers": 0,
        },
        "prompts": ["classify_intent"],
    }


class _TestHTTPHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler that serves self.server.response_body."""

    def do_GET(self):
        body = getattr(self.server, "response_body", b"")
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # Suppress request logging during tests


@pytest.fixture
def http_server():
    """Start a local HTTP server that serves configurable content."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _TestHTTPHandler)
    server.response_body = b""
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, port
    server.shutdown()


# ===================================================================
# verify_checksum
# ===================================================================

class TestVerifyChecksum:

    def test_valid_checksum_returns_true(self, tmp_path):
        path = tmp_path / "model.bin"
        expected = _write_file(path, b"hello model")
        assert verify_checksum(path, expected) is True

    def test_mismatched_checksum_raises(self, tmp_path):
        path = tmp_path / "model.bin"
        _write_file(path, b"hello model")
        with pytest.raises(FetchError, match="Checksum mismatch"):
            verify_checksum(path, "0" * 64)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FetchError, match="not found"):
            verify_checksum(tmp_path / "nope.bin", "a" * 64)

    def test_empty_file_has_valid_hash(self, tmp_path):
        path = tmp_path / "empty.bin"
        expected = _write_file(path, b"")
        assert verify_checksum(path, expected) is True

    def test_large_content_hashes_correctly(self, tmp_path):
        data = b"x" * (_CHUNK_SIZE * 3 + 42)
        path = tmp_path / "big.bin"
        expected = _write_file(path, data)
        assert verify_checksum(path, expected) is True

    def test_error_message_includes_both_hashes(self, tmp_path):
        path = tmp_path / "model.bin"
        actual_hash = _write_file(path, b"content")
        fake_hash = "f" * 64
        with pytest.raises(FetchError) as exc_info:
            verify_checksum(path, fake_hash)
        msg = str(exc_info.value)
        assert fake_hash in msg
        assert actual_hash in msg


# ===================================================================
# model_path
# ===================================================================

class TestModelPath:

    def test_uses_lockfile_filename(self, tmp_path):
        data = _make_lockfile_data(filename="my-model.gguf")
        path = model_path(models_dir=tmp_path, lockfile_data=data)
        assert path.name == "my-model.gguf"
        assert path.parent == tmp_path

    def test_default_dir_is_weights(self):
        data = _make_lockfile_data(filename="test.gguf")
        path = model_path(lockfile_data=data)
        assert path.parent.name == "weights"


# ===================================================================
# is_model_present
# ===================================================================

class TestIsModelPresent:

    def test_missing_file_returns_false(self, tmp_path):
        data = _make_lockfile_data(filename="missing.gguf")
        assert is_model_present(models_dir=tmp_path, lockfile_data=data) is False

    def test_correct_file_returns_true(self, tmp_path):
        content = b"valid model weights"
        sha = hashlib.sha256(content).hexdigest()
        data = _make_lockfile_data(filename="ok.gguf", sha_hash=sha)
        (tmp_path / "ok.gguf").write_bytes(content)
        assert is_model_present(models_dir=tmp_path, lockfile_data=data) is True

    def test_corrupt_file_returns_false(self, tmp_path):
        content = b"valid model weights"
        sha = hashlib.sha256(content).hexdigest()
        data = _make_lockfile_data(filename="corrupt.gguf", sha_hash=sha)
        (tmp_path / "corrupt.gguf").write_bytes(b"wrong data")
        assert is_model_present(models_dir=tmp_path, lockfile_data=data) is False


# ===================================================================
# fetch_model — with local HTTP server
# ===================================================================

class TestFetchModel:

    def test_downloads_and_verifies(self, tmp_path, http_server):
        server, port = http_server
        content = b"fake gguf model weights for testing"
        sha = hashlib.sha256(content).hexdigest()
        server.response_body = content

        data = _make_lockfile_data(
            filename="fetched.gguf",
            sha_hash=sha,
            url=f"http://127.0.0.1:{port}/fetched.gguf",
        )

        result = fetch_model(models_dir=tmp_path, lockfile_data=data)
        assert result.exists()
        assert result.name == "fetched.gguf"
        assert result.read_bytes() == content

    def test_creates_models_dir_if_missing(self, tmp_path, http_server):
        server, port = http_server
        content = b"model data"
        sha = hashlib.sha256(content).hexdigest()
        server.response_body = content

        nested = tmp_path / "deep" / "nested" / "dir"
        data = _make_lockfile_data(
            filename="model.gguf",
            sha_hash=sha,
            url=f"http://127.0.0.1:{port}/model.gguf",
        )

        result = fetch_model(models_dir=nested, lockfile_data=data)
        assert result.exists()
        assert nested.is_dir()

    def test_skips_download_when_already_present(self, tmp_path, http_server):
        server, port = http_server
        content = b"existing weights"
        sha = hashlib.sha256(content).hexdigest()
        server.response_body = b"should not be downloaded"

        data = _make_lockfile_data(
            filename="existing.gguf",
            sha_hash=sha,
            url=f"http://127.0.0.1:{port}/existing.gguf",
        )
        (tmp_path / "existing.gguf").write_bytes(content)

        result = fetch_model(models_dir=tmp_path, lockfile_data=data)
        # File should still contain original content, not server's
        assert result.read_bytes() == content

    def test_redownloads_corrupt_existing(self, tmp_path, http_server):
        server, port = http_server
        good_content = b"good model data"
        sha = hashlib.sha256(good_content).hexdigest()
        server.response_body = good_content

        data = _make_lockfile_data(
            filename="corrupt.gguf",
            sha_hash=sha,
            url=f"http://127.0.0.1:{port}/corrupt.gguf",
        )
        (tmp_path / "corrupt.gguf").write_bytes(b"corrupted")

        result = fetch_model(models_dir=tmp_path, lockfile_data=data)
        assert result.read_bytes() == good_content

    def test_force_redownloads(self, tmp_path, http_server):
        server, port = http_server
        old_content = b"old weights"
        new_content = b"new weights"
        old_sha = hashlib.sha256(old_content).hexdigest()
        new_sha = hashlib.sha256(new_content).hexdigest()
        server.response_body = new_content

        data = _make_lockfile_data(
            filename="forced.gguf",
            sha_hash=new_sha,
            url=f"http://127.0.0.1:{port}/forced.gguf",
        )
        (tmp_path / "forced.gguf").write_bytes(old_content)

        result = fetch_model(models_dir=tmp_path, lockfile_data=data, force=True)
        assert result.read_bytes() == new_content

    def test_checksum_mismatch_after_download_raises(self, tmp_path, http_server):
        server, port = http_server
        server.response_body = b"downloaded data"

        data = _make_lockfile_data(
            filename="bad.gguf",
            sha_hash="0" * 64,  # Won't match
            url=f"http://127.0.0.1:{port}/bad.gguf",
        )

        with pytest.raises(FetchError, match="Checksum mismatch"):
            fetch_model(models_dir=tmp_path, lockfile_data=data)

        # Temp file should be cleaned up
        assert not (tmp_path / "bad.download").exists()
        # Destination should not exist either
        assert not (tmp_path / "bad.gguf").exists()

    def test_cleans_up_temp_on_download_failure(self, tmp_path):
        data = _make_lockfile_data(
            filename="fail.gguf",
            url="http://127.0.0.1:1/nonexistent",  # port 1 won't respond
        )

        with pytest.raises(FetchError, match="Download failed"):
            fetch_model(models_dir=tmp_path, lockfile_data=data)

        assert not (tmp_path / "fail.download").exists()

    def test_progress_callback_invoked(self, tmp_path, http_server):
        server, port = http_server
        content = b"a" * 1000
        sha = hashlib.sha256(content).hexdigest()
        server.response_body = content

        data = _make_lockfile_data(
            filename="progress.gguf",
            sha_hash=sha,
            url=f"http://127.0.0.1:{port}/progress.gguf",
        )

        calls = []
        def callback(downloaded, total):
            calls.append((downloaded, total))

        fetch_model(
            models_dir=tmp_path,
            lockfile_data=data,
            progress_callback=callback,
        )

        assert len(calls) > 0
        # Last call should show all bytes downloaded
        assert calls[-1][0] == len(content)
        # Total should match content length
        assert calls[-1][1] == len(content)

    def test_large_download_multi_chunk(self, tmp_path, http_server):
        server, port = http_server
        content = b"x" * (_CHUNK_SIZE * 3 + 99)
        sha = hashlib.sha256(content).hexdigest()
        server.response_body = content

        data = _make_lockfile_data(
            filename="large.gguf",
            sha_hash=sha,
            url=f"http://127.0.0.1:{port}/large.gguf",
        )

        result = fetch_model(models_dir=tmp_path, lockfile_data=data)
        assert result.read_bytes() == content


# ===================================================================
# fetch_model — no temp file left behind
# ===================================================================

class TestFetchCleanup:

    def test_no_download_suffix_after_success(self, tmp_path, http_server):
        server, port = http_server
        content = b"clean download"
        sha = hashlib.sha256(content).hexdigest()
        server.response_body = content

        data = _make_lockfile_data(
            filename="clean.gguf",
            sha_hash=sha,
            url=f"http://127.0.0.1:{port}/clean.gguf",
        )

        fetch_model(models_dir=tmp_path, lockfile_data=data)
        # Only the final file should exist, no .download temp
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "clean.gguf"

    def test_no_download_suffix_after_checksum_fail(self, tmp_path, http_server):
        server, port = http_server
        server.response_body = b"bad content"

        data = _make_lockfile_data(
            filename="checkfail.gguf",
            sha_hash="0" * 64,
            url=f"http://127.0.0.1:{port}/checkfail.gguf",
        )

        with pytest.raises(FetchError):
            fetch_model(models_dir=tmp_path, lockfile_data=data)

        # No files should remain
        assert list(tmp_path.iterdir()) == []


# ===================================================================
# Integration-style: verify real lockfile metadata is valid
# ===================================================================

class TestRealLockfileIntegration:

    def test_model_path_from_real_lockfile(self):
        path = model_path()
        assert path.name == "Hermes-2-Pro-Mistral-7B.Q4_K_M.gguf"
        assert path.parent.name == "weights"

    def test_real_lockfile_hash_is_64_chars(self):
        from local_models.lockfile import load_lockfile
        data = load_lockfile()
        h = data["model"]["checksum"]["hash"]
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_real_lockfile_source_url_is_https(self):
        from local_models.lockfile import load_lockfile
        data = load_lockfile()
        url = data["model"]["sources"][0]["url"]
        assert url.startswith("https://")
        assert "Hermes-2-Pro-Mistral-7B" in url

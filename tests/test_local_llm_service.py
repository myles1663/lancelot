"""
Tests for the local-llm Docker service.
Prompt 11: local-llm Docker Service.

Unit tests validate:
- docker-compose.yml service definition
- Dockerfile structure
- server.py endpoint contracts (mocked model)

Integration tests (docker-marked) validate live container behaviour.
"""

import os
import pathlib
import time
import pytest
import yaml
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_COMPOSE_PATH = _REPO_ROOT / "docker-compose.yml"
_DOCKERFILE_PATH = _REPO_ROOT / "local_models" / "Dockerfile"
_REQUIREMENTS_PATH = _REPO_ROOT / "local_models" / "requirements-llm.txt"


# ===================================================================
# docker-compose.yml validation
# ===================================================================

class TestDockerCompose:

    @pytest.fixture(autouse=True)
    def _load_compose(self):
        with open(_COMPOSE_PATH, "r", encoding="utf-8") as f:
            self.compose = yaml.safe_load(f)

    def test_compose_file_exists(self):
        assert _COMPOSE_PATH.exists()

    def test_local_llm_service_defined(self):
        assert "local-llm" in self.compose["services"]

    def test_local_llm_build_context(self):
        svc = self.compose["services"]["local-llm"]
        assert svc["build"]["context"] == "./local_models"
        assert svc["build"]["dockerfile"] == "Dockerfile"

    def test_local_llm_container_name(self):
        svc = self.compose["services"]["local-llm"]
        assert svc["container_name"] == "lancelot_local_llm"

    def test_local_llm_port_mapping(self):
        svc = self.compose["services"]["local-llm"]
        assert "8080:8080" in svc["ports"]

    def test_local_llm_healthcheck_present(self):
        svc = self.compose["services"]["local-llm"]
        hc = svc["healthcheck"]
        assert "curl" in str(hc["test"])
        assert "/health" in str(hc["test"])

    def test_local_llm_healthcheck_timings(self):
        svc = self.compose["services"]["local-llm"]
        hc = svc["healthcheck"]
        # start_period should be generous for model loading
        assert "60s" in str(hc.get("start_period", ""))
        assert hc.get("retries", 0) >= 3

    def test_local_llm_volume_mounts_weights(self):
        svc = self.compose["services"]["local-llm"]
        volumes = svc["volumes"]
        weight_mount = [v for v in volumes if "weights" in str(v)]
        assert len(weight_mount) > 0
        # Weights should be read-only
        assert ":ro" in str(weight_mount[0])

    def test_local_llm_restart_policy(self):
        svc = self.compose["services"]["local-llm"]
        assert svc.get("restart") == "unless-stopped"

    def test_local_llm_on_lancelot_network(self):
        svc = self.compose["services"]["local-llm"]
        assert "lancelot_net" in svc["networks"]

    def test_core_depends_on_local_llm(self):
        core = self.compose["services"]["lancelot-core"]
        deps = core.get("depends_on", {})
        assert "local-llm" in deps
        assert deps["local-llm"]["condition"] == "service_healthy"

    def test_core_has_local_llm_url_env(self):
        core = self.compose["services"]["lancelot-core"]
        env_list = core.get("environment", [])
        llm_url = [e for e in env_list if "LOCAL_LLM_URL" in str(e)]
        assert len(llm_url) > 0
        assert "local-llm:8080" in str(llm_url[0])

    def test_local_llm_env_vars(self):
        svc = self.compose["services"]["local-llm"]
        env_list = svc.get("environment", [])
        env_str = str(env_list)
        assert "LOCAL_MODELS_DIR" in env_str
        assert "LOCAL_MODEL_CTX" in env_str
        assert "LOCAL_MODEL_THREADS" in env_str


# ===================================================================
# Dockerfile validation
# ===================================================================

class TestDockerfile:

    @pytest.fixture(autouse=True)
    def _load_dockerfile(self):
        self.content = _DOCKERFILE_PATH.read_text(encoding="utf-8")

    def test_dockerfile_exists(self):
        assert _DOCKERFILE_PATH.exists()

    def test_base_image_is_python_311(self):
        assert "python:3.11" in self.content

    def test_creates_non_root_user(self):
        assert "useradd" in self.content
        assert "USER" in self.content

    def test_installs_cmake(self):
        # cmake needed for llama-cpp-python compilation
        assert "cmake" in self.content

    def test_installs_curl(self):
        # curl needed for HEALTHCHECK
        assert "curl" in self.content

    def test_healthcheck_defined(self):
        assert "HEALTHCHECK" in self.content
        assert "/health" in self.content

    def test_exposes_port_8080(self):
        assert "EXPOSE 8080" in self.content

    def test_copies_server_py(self):
        assert "server.py" in self.content

    def test_copies_lockfile(self):
        assert "models.lock.yaml" in self.content

    def test_copies_prompts(self):
        assert "prompts/" in self.content

    def test_weights_not_baked_in(self):
        # Weights should never be COPY'd into the image
        assert "weights" not in self.content.lower() or "mount" in self.content.lower()


# ===================================================================
# requirements-llm.txt
# ===================================================================

class TestRequirementsLLM:

    def test_requirements_file_exists(self):
        assert _REQUIREMENTS_PATH.exists()

    def test_includes_llama_cpp_python(self):
        content = _REQUIREMENTS_PATH.read_text(encoding="utf-8")
        assert "llama-cpp-python" in content

    def test_includes_fastapi(self):
        content = _REQUIREMENTS_PATH.read_text(encoding="utf-8")
        assert "fastapi" in content

    def test_includes_uvicorn(self):
        content = _REQUIREMENTS_PATH.read_text(encoding="utf-8")
        assert "uvicorn" in content

    def test_includes_pyyaml(self):
        content = _REQUIREMENTS_PATH.read_text(encoding="utf-8")
        assert "pyyaml" in content


# ===================================================================
# server.py — endpoint contracts (mocked model)
# ===================================================================

class TestServerEndpoints:

    @pytest.fixture(autouse=True)
    def _setup_client(self):
        """Import server and create test client with mocked model."""
        import local_models.server as srv
        self._srv = srv
        # Inject mocked model state
        self._original_llm = srv._llm
        self._original_name = srv._model_name
        self._original_loaded = srv._loaded_at

        mock_llm = MagicMock()
        mock_llm.return_value = {
            "choices": [{"text": "test output"}],
            "usage": {"completion_tokens": 2},
        }
        srv._llm = mock_llm
        srv._model_name = "test-model"
        srv._loaded_at = time.time()

        from fastapi.testclient import TestClient
        self.client = TestClient(srv.app, raise_server_exceptions=False)

        yield

        # Restore original state
        srv._llm = self._original_llm
        srv._model_name = self._original_name
        srv._loaded_at = self._original_loaded

    def test_health_returns_200(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["model"] == "test-model"
        assert "uptime_seconds" in data

    def test_health_503_when_no_model(self):
        self._srv._llm = None
        resp = self.client.get("/health")
        assert resp.status_code == 503

    def test_completions_returns_200(self):
        resp = self.client.post("/v1/completions", json={
            "prompt": "Hello",
            "max_tokens": 32,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["text"] == "test output"
        assert data["model"] == "test-model"
        assert data["tokens_generated"] == 2
        assert "elapsed_ms" in data

    def test_completions_503_when_no_model(self):
        self._srv._llm = None
        resp = self.client.post("/v1/completions", json={
            "prompt": "Hello",
        })
        assert resp.status_code == 503

    def test_completions_validates_max_tokens(self):
        resp = self.client.post("/v1/completions", json={
            "prompt": "Hello",
            "max_tokens": 0,
        })
        assert resp.status_code == 422

    def test_completions_validates_temperature(self):
        resp = self.client.post("/v1/completions", json={
            "prompt": "Hello",
            "temperature": 5.0,
        })
        assert resp.status_code == 422

    def test_completions_missing_prompt_returns_422(self):
        resp = self.client.post("/v1/completions", json={})
        assert resp.status_code == 422

    def test_completions_default_values(self):
        resp = self.client.post("/v1/completions", json={
            "prompt": "Test prompt",
        })
        assert resp.status_code == 200

    def test_completions_500_on_inference_error(self):
        self._srv._llm.side_effect = RuntimeError("GPU crash")
        resp = self.client.post("/v1/completions", json={
            "prompt": "Crash me",
        })
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Model inference failed"

    def test_completions_with_stop_sequences(self):
        resp = self.client.post("/v1/completions", json={
            "prompt": "Hello",
            "stop": [".", "\n"],
        })
        assert resp.status_code == 200


# ===================================================================
# Integration — live Docker service
# ===================================================================

@pytest.mark.docker
class TestLiveDockerService:
    """These tests only run when Docker is available and the
    local-llm container is running."""

    @pytest.fixture(autouse=True)
    def _check_container(self):
        """Skip if the local-llm container isn't running."""
        import shutil
        import subprocess
        if shutil.which("docker") is None:
            pytest.skip("Docker not available")
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}",
             "lancelot_local_llm"],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or "true" not in result.stdout:
            pytest.skip("lancelot_local_llm container not running")

    def test_health_endpoint_reachable(self):
        import urllib.request
        resp = urllib.request.urlopen("http://localhost:8080/health", timeout=10)
        assert resp.status == 200

    def test_completions_endpoint_works(self):
        import json
        import urllib.request
        data = json.dumps({
            "prompt": "Hello, the capital of France is",
            "max_tokens": 16,
        }).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:8080/v1/completions",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=30)
        assert resp.status == 200
        body = json.loads(resp.read())
        assert len(body["text"]) > 0

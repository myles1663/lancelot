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
_PRISM_DOCKERFILE_PATH = _REPO_ROOT / "local_models" / "Dockerfile.prism"
_PRISM_CUDA_DOCKERFILE_PATH = _REPO_ROOT / "local_models" / "Dockerfile.prism.cuda"
_REQUIREMENTS_PATH = _REPO_ROOT / "local_models" / "requirements-llm.txt"


def _compose_duration_seconds(value: object) -> int:
    text = str(value).strip()
    if ":-" in text and text.endswith("}"):
        text = text.rsplit(":-", 1)[1][:-1]
    assert text.endswith("s")
    return int(text[:-1])


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
        assert svc["build"]["dockerfile"] == "${LOCAL_LLM_DOCKERFILE:-Dockerfile}"
        assert svc["build"]["args"]["LOCAL_LLM_WHEEL_VARIANT"] == "${LOCAL_LLM_WHEEL_VARIANT:-cpu}"
        assert svc["build"]["args"]["LOCAL_LLM_WHEEL_VERSION"] == "${LOCAL_LLM_WHEEL_VERSION:-0.3.19}"
        assert "PRISM_RELEASE_TAG" in svc["build"]["args"]
        assert "PRISM_RELEASE_SHA256" in svc["build"]["args"]
        assert "PRISM_CUDA_BASE_IMAGE" in svc["build"]["args"]
        assert "PRISM_LLAMA_CPP_REF" in svc["build"]["args"]

    def test_local_llm_uses_prebuilt_image_with_build_fallback(self):
        svc = self.compose["services"]["local-llm"]
        assert svc["image"] == (
            "${LOCAL_LLM_IMAGE:-ghcr.io/myles1663/lancelot-local-llm:llama-cpp-0.3.19-cpu}"
        )
        assert svc["pull_policy"] == "${LOCAL_LLM_PULL_POLICY:-missing}"
        assert svc["build"]["context"] == "./local_models"

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
        # Model warmup varies by host; the default must be generous and operator-tunable.
        assert "LOCAL_LLM_HEALTH_START_PERIOD" in str(hc.get("start_period", ""))
        assert _compose_duration_seconds(hc.get("start_period", "")) >= 120
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

    def test_core_starts_while_local_llm_warms(self):
        core = self.compose["services"]["lancelot-core"]
        deps = core.get("depends_on", {})
        assert "local-llm" in deps
        assert deps["local-llm"]["condition"] == "service_started"

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
        assert "LOCAL_MODEL_PROFILE" in env_str
        assert "LOCAL_LLM_ENGINE" in env_str
        assert "LOCAL_MODEL_CTX" in env_str
        assert "LOCAL_MODEL_THREADS" in env_str
        assert "${LOCAL_MODEL_GPU_LAYERS:-0}" in env_str

    def test_optional_bonsai_services_defined(self):
        assert "local-bonsai-17b" in self.compose["services"]
        assert "local-bonsai-8b" in self.compose["services"]
        assert self.compose["services"]["local-bonsai-17b"]["profiles"] == ["bonsai"]
        assert self.compose["services"]["local-bonsai-8b"]["profiles"] == ["bonsai"]

    def test_bonsai_services_use_prism_runtime(self):
        svc = self.compose["services"]["local-bonsai-8b"]
        assert svc["image"] == "${BONSAI_LLM_IMAGE:-ghcr.io/myles1663/lancelot-local-llm:prism-b8846-d104cf1}"
        assert svc["pull_policy"] == "${BONSAI_LLM_PULL_POLICY:-missing}"
        assert svc["build"]["dockerfile"] == "${BONSAI_LLM_DOCKERFILE:-Dockerfile.prism}"
        assert svc["build"]["args"]["PRISM_CUDA_BASE_IMAGE"] == (
            "${PRISM_CUDA_BASE_IMAGE:-nvidia/cuda:12.3.2-devel-ubuntu22.04}"
        )
        assert svc["build"]["args"]["PRISM_LLAMA_CPP_REF"] == (
            "${PRISM_LLAMA_CPP_REF:-d104cf1b639a909ddea521d61f7cb023c6e41f57}"
        )
        assert "PRISM_CUDA_ARCHITECTURES" in svc["build"]["args"]
        env_str = str(svc.get("environment", []))
        assert "LOCAL_LLM_ENGINE=prism_llama_server" in env_str
        assert "bonsai-8b" in env_str
        assert "NVIDIA_VISIBLE_DEVICES" in env_str


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
        assert "ubuntu22.04" in self.content
        assert "python3.11" in self.content

    def test_creates_non_root_user(self):
        assert "useradd" in self.content
        assert "USER" in self.content

    def test_installs_cmake(self):
        # Current image uses a prebuilt llama-cpp-python wheel instead of local compilation
        assert "llama-cpp-python" in self.content
        assert "extra-index-url" in self.content
        assert "LOCAL_LLM_WHEEL_VARIANT" in self.content

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


class TestPrismDockerfile:

    @pytest.fixture(autouse=True)
    def _load_dockerfile(self):
        self.content = _PRISM_DOCKERFILE_PATH.read_text(encoding="utf-8")

    def test_prism_dockerfile_exists(self):
        assert _PRISM_DOCKERFILE_PATH.exists()

    def test_prism_runtime_uses_pinned_prebuilt_release(self):
        assert "PRISM_RELEASE_TAG=prism-b8846-d104cf1" in self.content
        assert (
            "PRISM_RELEASE_SHA256=80bb9a820bb61389dc9f34c976f00afc33018d199b8ae81dd1afeaad2044ec87"
            in self.content
        )
        assert "sha256sum -c" in self.content
        assert "git checkout" not in self.content

    def test_prism_runtime_preserves_fastapi_wrapper(self):
        assert "LOCAL_LLM_ENGINE=prism_llama_server" in self.content
        assert "server.py" in self.content
        assert "llama-server" in self.content


class TestPrismCudaDockerfile:

    @pytest.fixture(autouse=True)
    def _load_dockerfile(self):
        self.content = _PRISM_CUDA_DOCKERFILE_PATH.read_text(encoding="utf-8")

    def test_prism_cuda_dockerfile_exists(self):
        assert _PRISM_CUDA_DOCKERFILE_PATH.exists()

    def test_prism_cuda_runtime_uses_cuda_devel_base(self):
        assert "PRISM_CUDA_BASE_IMAGE=nvidia/cuda:12.3.2-devel-ubuntu22.04" in self.content
        assert "FROM ${PRISM_CUDA_BASE_IMAGE}" in self.content

    def test_prism_cuda_runtime_builds_pinned_source(self):
        assert "PRISM_LLAMA_CPP_REF=d104cf1b639a909ddea521d61f7cb023c6e41f57" in self.content
        assert "git checkout \"$PRISM_LLAMA_CPP_REF\"" in self.content
        assert "sha256sum -c" not in self.content

    def test_prism_cuda_runtime_enables_ggml_cuda(self):
        assert "PRISM_CMAKE_ARGS=-DGGML_CUDA=ON" in self.content
        assert "PRISM_CUDA_ARCHITECTURES=" in self.content
        assert "-DCMAKE_CUDA_ARCHITECTURES=$PRISM_CUDA_ARCHITECTURES" in self.content
        assert "LIBRARY_PATH=/usr/local/cuda/lib64/stubs" in self.content
        assert "libcuda.so.1" in self.content
        assert "-Wl,-rpath-link,/usr/local/cuda/lib64/stubs" in self.content
        assert "$PRISM_CMAKE_ARGS" in self.content
        assert "test -x /usr/local/bin/llama-server" in self.content

    def test_prism_cuda_runtime_preserves_fastapi_wrapper(self):
        assert "LOCAL_LLM_ENGINE=prism_llama_server" in self.content
        assert "server.py" in self.content
        assert "requirements-llm.txt" in self.content


# ===================================================================
# requirements-llm.txt
# ===================================================================

class TestRequirementsLLM:

    def test_requirements_file_exists(self):
        assert _REQUIREMENTS_PATH.exists()

    def test_requirements_file_excludes_llama_cpp_python(self):
        content = _REQUIREMENTS_PATH.read_text(encoding="utf-8")
        assert "llama-cpp-python" not in content

    def test_includes_fastapi(self):
        content = _REQUIREMENTS_PATH.read_text(encoding="utf-8")
        assert "fastapi" in content

    def test_includes_uvicorn(self):
        content = _REQUIREMENTS_PATH.read_text(encoding="utf-8")
        assert "uvicorn" in content

    def test_includes_pyyaml(self):
        content = _REQUIREMENTS_PATH.read_text(encoding="utf-8")
        assert "pyyaml" in content

    def test_dockerfile_installs_pinned_llama_cpp_python(self):
        dockerfile = _DOCKERFILE_PATH.read_text(encoding="utf-8")
        assert 'ARG LOCAL_LLM_WHEEL_VERSION=0.3.19' in dockerfile
        assert 'cpu) WHEEL_INDEX=""' in dockerfile
        assert 'cu123) WHEEL_INDEX="https://abetlen.github.io/llama-cpp-python/whl/cu123"' in dockerfile


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
        self._original_profile = srv._model_profile
        self._original_engine = srv._engine
        self._original_backend_url = srv._llama_server_url
        self._original_backend_process = srv._llama_server_process
        self._original_loaded = srv._loaded_at
        self._original_readiness = dict(srv._readiness)

        mock_llm = MagicMock()
        mock_llm.return_value = {
            "choices": [{"text": "test output"}],
            "usage": {"completion_tokens": 2},
        }
        srv._llm = mock_llm
        srv._model_name = "test-model"
        srv._model_profile = "test-profile"
        srv._engine = srv._ENGINE_LLAMA_CPP_PYTHON
        srv._llama_server_url = ""
        srv._llama_server_process = None
        srv._loaded_at = time.time()
        srv._readiness.update({
            "loaded": True,
            "ready": True,
            "status": "ready",
            "last_verified_at": "2026-04-17T12:00:00Z",
            "last_checked_at": "2026-04-17T12:00:00Z",
            "last_error": None,
            "consecutive_failures": 0,
            "last_smoke_elapsed_ms": 12.3,
        })

        from fastapi.testclient import TestClient
        self.client = TestClient(srv.app, raise_server_exceptions=False)

        yield

        # Restore original state
        srv._llm = self._original_llm
        srv._model_name = self._original_name
        srv._model_profile = self._original_profile
        srv._engine = self._original_engine
        srv._llama_server_url = self._original_backend_url
        srv._llama_server_process = self._original_backend_process
        srv._loaded_at = self._original_loaded
        srv._readiness.clear()
        srv._readiness.update(self._original_readiness)

    def test_health_returns_200(self):
        resp = self.client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["ready"] is True
        assert data["loaded"] is True
        assert data["model"] == "test-model"
        assert data["profile"] == "test-profile"
        assert data["engine"] == "llama_cpp_python"
        assert data["last_verified_at"] == "2026-04-17T12:00:00Z"
        assert "uptime_seconds" in data

    def test_health_503_when_no_model(self):
        self._srv._llm = None
        self._srv._readiness["loaded"] = False
        self._srv._readiness["ready"] = False
        self._srv._readiness["status"] = "unavailable"
        self._srv._readiness["last_error"] = "Model not loaded"
        resp = self.client.get("/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["ready"] is False
        assert data["loaded"] is False
        assert data["status"] == "unavailable"

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
        self._srv._readiness["loaded"] = False
        self._srv._readiness["ready"] = False
        self._srv._readiness["status"] = "unavailable"
        self._srv._readiness["last_error"] = "Model not loaded"
        resp = self.client.post("/v1/completions", json={
            "prompt": "Hello",
        })
        assert resp.status_code == 503

    def test_completions_503_when_model_loaded_but_not_ready(self):
        self._srv._readiness["loaded"] = True
        self._srv._readiness["ready"] = False
        self._srv._readiness["status"] = "loaded_not_ready"
        self._srv._readiness["last_error"] = "Inference smoke failed"
        resp = self.client.post("/v1/completions", json={
            "prompt": "Hello",
        })
        assert resp.status_code == 503
        assert resp.json()["detail"] == "Inference smoke failed"

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

    def test_completions_422_on_context_window_error(self):
        self._srv._llm.side_effect = RuntimeError(
            "Requested tokens (4419) exceed context window of 4096"
        )
        resp = self.client.post("/v1/completions", json={
            "prompt": "Too large",
        })
        assert resp.status_code == 422
        assert resp.json()["detail"] == "Context window exceeded"

    def test_completions_with_stop_sequences(self):
        resp = self.client.post("/v1/completions", json={
            "prompt": "Hello",
            "stop": [".", "\n"],
        })
        assert resp.status_code == 200

    def test_completions_can_proxy_to_prism_backend(self):
        self._srv._engine = self._srv._ENGINE_PRISM_LLAMA_SERVER
        self._srv._llama_server_url = "http://127.0.0.1:8091"
        with patch.object(self._srv, "_post_backend_json") as mock_backend:
            mock_backend.return_value = {
                "choices": [{"text": "proxied"}],
                "usage": {"completion_tokens": 1},
            }
            resp = self.client.post("/v1/completions", json={
                "prompt": "Hello",
                "max_tokens": 16,
            })
        assert resp.status_code == 200
        assert resp.json()["text"] == "proxied"
        mock_backend.assert_called_once()


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
        import urllib.request
        if shutil.which("docker") is None:
            pytest.skip("Docker not available")
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}",
             "lancelot_local_llm"],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or "true" not in result.stdout:
            pytest.skip("lancelot_local_llm container not running")
        try:
            resp = urllib.request.urlopen("http://localhost:8080/health", timeout=5)
            if resp.status != 200:
                pytest.skip("lancelot_local_llm not ready for inference")
        except Exception:
            pytest.skip("lancelot_local_llm not ready for inference")

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

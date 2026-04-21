from src.core.network_allowlist import NetworkAllowlistService


def test_load_domains_includes_core_domains_by_default(tmp_path):
    service = NetworkAllowlistService(path=str(tmp_path / "network_allowlist.yaml"))
    service.set_domains(["example.com"])

    domains = service.load_domains()

    assert "example.com" in domains
    assert "ghcr.io" in domains
    assert "api.projectlancelot.dev" in domains


def test_load_domains_without_core_returns_only_operator_domains(tmp_path):
    service = NetworkAllowlistService(path=str(tmp_path / "network_allowlist.yaml"))
    service.set_domains(["Example.com", "api.example.com", "example.com"])

    domains = service.load_domains(include_core=False)

    assert domains == ["api.example.com", "example.com"]


def test_domain_matching_supports_parent_domain_suffixes(tmp_path):
    service = NetworkAllowlistService(path=str(tmp_path / "network_allowlist.yaml"))
    service.set_domains(["github.com"])

    assert service.is_hostname_allowed("api.github.com", include_core=False) is True
    assert service.is_hostname_allowed("evilgithub.com", include_core=False) is False

from src.federation.graph_models import (
    GraphNode,
    InstanceRole,
    NodeBudgetConfig,
    TopologyDocument,
)
from src.federation.graph_persistence import TopologyStore
from src.federation.identity import generate_identity
from src.federation.runtime_budget_source import RuntimeBudgetResolver


def test_runtime_budget_resolver_prefers_deployed_local_node_budget(tmp_path):
    identity = generate_identity()
    store = TopologyStore(str(tmp_path))
    topology = TopologyDocument(
        topology_id="topo-1",
        topology_name="Budgeted",
        nodes=[
            GraphNode(
                node_id="LOCAL_INSTANCE",
                instance_name="Local",
                is_local=True,
                instance_role=InstanceRole.PEER,
                federation_identity_public_key=identity.public_key_hex(),
                fingerprint=identity.fingerprint,
                budget_config=NodeBudgetConfig(daily_ceiling_usd=37.5),
            )
        ],
    )
    store.save_deployed(topology)

    resolver = RuntimeBudgetResolver(
        topology_data_dir=str(tmp_path),
        identity=identity,
        fallback_daily_ceiling_usd=10.0,
        refresh_interval_s=0.0,
    )

    assert resolver.resolve_daily_ceiling_usd() == 37.5


def test_runtime_budget_resolver_falls_back_when_no_local_node_matches(tmp_path):
    identity = generate_identity()
    store = TopologyStore(str(tmp_path))
    topology = TopologyDocument(
        topology_id="topo-1",
        topology_name="Other",
        nodes=[
            GraphNode(
                node_id="remote",
                instance_name="Remote",
                federation_identity_public_key="abcd",
                fingerprint="fp",
                budget_config=NodeBudgetConfig(daily_ceiling_usd=99.0),
            )
        ],
    )
    store.save_deployed(topology)

    resolver = RuntimeBudgetResolver(
        topology_data_dir=str(tmp_path),
        identity=identity,
        fallback_daily_ceiling_usd=12.0,
        refresh_interval_s=0.0,
    )

    assert resolver.resolve_daily_ceiling_usd() == 12.0

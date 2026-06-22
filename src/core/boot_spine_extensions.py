"""Time-travel, A2A, and incident-response boot lifecycle ownership."""

from __future__ import annotations

import json
import os
import uuid


def register_spine_extension_subsystems(*, main_orchestrator, subsystem_manager, logger, connector_vault):
    _connector_vault = connector_vault
    _optional_receipt_service = getattr(main_orchestrator, "receipt_service", None)

    # Time-travel debugging
    try:
        from feature_flags import FEATURE_TIME_TRAVEL
        if FEATURE_TIME_TRAVEL:
            from timetravel.api import init_timetravel_api

            # Initialize with receipt service and live Soul provider
            _tt_soul = lambda: getattr(main_orchestrator, "soul", None)

            def _apply_timetravel_modifications(graph_dict, modifications):
                for field_path, value in (modifications or {}).items():
                    parts = str(field_path).split(".")
                    cursor = graph_dict
                    for raw_part in parts[:-1]:
                        part = int(raw_part) if isinstance(cursor, list) and raw_part.isdigit() else raw_part
                        cursor = cursor[part]
                    leaf = parts[-1]
                    leaf_key = int(leaf) if isinstance(cursor, list) and leaf.isdigit() else leaf
                    cursor[leaf_key] = value
                return graph_dict

            def _execute_timetravel_quest(*, mode, source_quest_id, new_quest_id, modifications, operator_id, session_id):
                from datetime import datetime, timezone
                from src.core.tasking.schema import TaskGraph, TaskRun

                source_run = main_orchestrator.task_store.get_run_by_quest_id(source_quest_id)
                if source_run is None:
                    raise RuntimeError(f"Source quest is not replayable by TaskRun: {source_quest_id}")

                source_graph = main_orchestrator.task_store.get_graph(source_run.task_graph_id)
                if source_graph is None:
                    raise RuntimeError(
                        f"TaskGraph not found for source quest {source_quest_id}: {source_run.task_graph_id}"
                    )

                cloned_graph = source_graph.to_dict()
                cloned_graph["id"] = str(uuid.uuid4())
                cloned_graph["created_at"] = datetime.now(timezone.utc).isoformat()
                cloned_graph["session_id"] = session_id or source_graph.session_id or source_run.session_id

                if mode == "fork" and modifications:
                    cloned_graph = _apply_timetravel_modifications(cloned_graph, modifications)

                replay_graph = TaskGraph.from_dict(cloned_graph)
                main_orchestrator.task_store.save_graph(replay_graph)

                replay_run = TaskRun(
                    task_graph_id=replay_graph.id,
                    execution_token_id=source_run.execution_token_id,
                    session_id=session_id or source_run.session_id,
                    operator_id=operator_id or source_run.operator_id,
                    quest_id=new_quest_id,
                )
                main_orchestrator.task_store.create_run(replay_run)
                result = main_orchestrator.task_runner.run(replay_run.id)
                return {
                    "run_id": replay_run.id,
                    "task_graph_id": replay_graph.id,
                    "status": result.status,
                    "step_count": len(result.step_results),
                }

            if _optional_receipt_service is not None:
                init_timetravel_api(
                    receipt_service=_optional_receipt_service,
                    soul=_tt_soul,
                    soul_dir=None,
                    quest_executor=_execute_timetravel_quest,
                    trust_ledger=getattr(main_orchestrator, "trust_ledger", None),
                    data_dir=main_orchestrator.data_dir,
                )
                logger.info("FEATURE_TIME_TRAVEL enabled; API mounted at /api/timetravel")
            else:
                logger.warning("Time-Travel: receipt service unavailable")
    except Exception as e:
        logger.warning(f"Time-Travel initialization failed: {e}")

    def _init_timetravel_subsystem():
        from timetravel.api import init_timetravel_api

        receipt_service = getattr(main_orchestrator, "receipt_service", None)
        if receipt_service is None:
            raise RuntimeError("Time-Travel requires receipt service")

        tt_soul = lambda: getattr(main_orchestrator, "soul", None)

        def _apply_timetravel_modifications(graph_dict, modifications):
            for field_path, value in (modifications or {}).items():
                parts = str(field_path).split(".")
                cursor = graph_dict
                for raw_part in parts[:-1]:
                    part = int(raw_part) if isinstance(cursor, list) and raw_part.isdigit() else raw_part
                    cursor = cursor[part]
                leaf = parts[-1]
                leaf_key = int(leaf) if isinstance(cursor, list) and leaf.isdigit() else leaf
                cursor[leaf_key] = value
            return graph_dict

        def _execute_timetravel_quest(*, mode, source_quest_id, new_quest_id, modifications, operator_id, session_id):
            from datetime import datetime, timezone
            from src.core.tasking.schema import TaskGraph, TaskRun

            source_run = main_orchestrator.task_store.get_run_by_quest_id(source_quest_id)
            if source_run is None:
                raise RuntimeError(f"Source quest is not replayable by TaskRun: {source_quest_id}")
            source_graph = main_orchestrator.task_store.get_graph(source_run.task_graph_id)
            if source_graph is None:
                raise RuntimeError(
                    f"TaskGraph not found for source quest {source_quest_id}: {source_run.task_graph_id}"
                )
            cloned_graph = source_graph.to_dict()
            cloned_graph["id"] = str(uuid.uuid4())
            cloned_graph["created_at"] = datetime.now(timezone.utc).isoformat()
            cloned_graph["session_id"] = session_id or source_graph.session_id or source_run.session_id
            if mode == "fork" and modifications:
                cloned_graph = _apply_timetravel_modifications(cloned_graph, modifications)
            replay_graph = TaskGraph.from_dict(cloned_graph)
            main_orchestrator.task_store.save_graph(replay_graph)
            replay_run = TaskRun(
                task_graph_id=replay_graph.id,
                execution_token_id=source_run.execution_token_id,
                session_id=session_id or source_run.session_id,
                operator_id=operator_id or source_run.operator_id,
                quest_id=new_quest_id,
            )
            main_orchestrator.task_store.create_run(replay_run)
            result = main_orchestrator.task_runner.run(replay_run.id)
            return {
                "run_id": replay_run.id,
                "task_graph_id": replay_graph.id,
                "status": result.status,
                "step_count": len(result.step_results),
            }

        init_timetravel_api(
            receipt_service=receipt_service,
            soul=tt_soul,
            soul_dir=None,
            quest_executor=_execute_timetravel_quest,
            trust_ledger=getattr(main_orchestrator, "trust_ledger", None),
            data_dir=main_orchestrator.data_dir,
        )
        logger.info("Time-Travel subsystem hot-started.")
        return {"receipt_service": receipt_service}

    def _shutdown_timetravel_subsystem(objects):
        from timetravel.api import shutdown_timetravel_api

        shutdown_timetravel_api()

    subsystem_manager.register(
        "time_travel",
        "FEATURE_TIME_TRAVEL",
        _init_timetravel_subsystem,
        _shutdown_timetravel_subsystem,
        ["/api/timetravel"],
    )
    try:
        from feature_flags import FEATURE_TIME_TRAVEL

        if FEATURE_TIME_TRAVEL:
            entry = subsystem_manager.get("time_travel")
            if entry and not entry.running:
                entry.objects = {"receipt_service": _optional_receipt_service}
                entry.running = True
    except Exception as exc:
        logger.warning("Time-Travel lifecycle registration failed: %s", exc)

    # A2A protocol
    try:
        from feature_flags import FEATURE_A2A
        if FEATURE_A2A:
            from a2a.registry import A2ARegistry
            from a2a.server import init_a2a_server
            from a2a.api import init_a2a_api
            from a2a.inbound_pipeline import InboundPipeline
            from a2a.outbound_pipeline import OutboundPipeline
            from a2a.client import A2AClient
            from a2a.types import A2AArtifact, A2AMessagePart

            # Initialize registry
            _a2a_registry = A2ARegistry()

            # Load Soul for A2A permissions
            _a2a_soul_provider = lambda: getattr(main_orchestrator, "soul", None)

            _a2a_client = A2AClient(_optional_receipt_service)
            _a2a_vault = _connector_vault

            # Initialize pipelines
            _a2a_inbound = InboundPipeline(
                _a2a_registry,
                _optional_receipt_service,
                _a2a_soul_provider,
                vault=_a2a_vault,
                a2a_client=_a2a_client,
            )
            _a2a_outbound = OutboundPipeline(
                _a2a_registry,
                _optional_receipt_service,
                _a2a_soul_provider,
                vault=_a2a_vault,
                a2a_client=_a2a_client,
                frontier_scrubber=(
                    lambda: main_orchestrator.get_frontier_scrubber()
                    if main_orchestrator is not None
                    else None
                ),
            )

            def _execute_inbound_a2a_task(*, task, caller, quest_id):
                """Route inbound A2A work through the live orchestrator."""
                text_parts = []
                if task.message:
                    for part in task.message.parts:
                        if part.text:
                            text_parts.append(part.text)
                        elif part.data is not None:
                            text_parts.append(json.dumps(part.data, sort_keys=True))
                        elif part.file_uri:
                            text_parts.append(f"[file] {part.file_uri}")

                user_message = "\n".join(p for p in text_parts if p).strip()
                if not user_message:
                    raise ValueError("Inbound A2A task contained no executable content")

                envelope = (
                    f"[External A2A task from {caller.display_name or caller.agent_id}"
                    f" ({caller.agent_framework})]\n{user_message}"
                )
                response_text = main_orchestrator.chat(
                    envelope,
                    channel="api",
                    quest_id=quest_id,
                )
                artifacts = [
                    A2AArtifact(
                        parts=[A2AMessagePart(type="text", text=response_text)],
                        metadata={
                            "quest_id": quest_id,
                            "source": "lancelot",
                            "external_peer": caller.agent_id,
                        },
                    ).to_dict()
                ]
                return {
                    "status": "completed",
                    "artifacts": artifacts,
                    "message": "Task executed successfully.",
                }

            # Mount protocol-standard endpoints at root
            init_a2a_server(
                _a2a_soul_provider,
                _optional_receipt_service,
                _a2a_registry,
                _a2a_inbound,
                task_executor=_execute_inbound_a2a_task,
                data_dir="/home/lancelot/data",
            )

            # Mount management API
            init_a2a_api(_a2a_registry, _optional_receipt_service, _a2a_soul_provider, _a2a_outbound, _a2a_client)

            logger.info("FEATURE_A2A enabled; protocol at /a2a/, management at /api/a2a/")
    except Exception as e:
        logger.warning(f"A2A initialization failed: {e}")

    def _init_a2a_subsystem():
        from a2a.registry import A2ARegistry
        from a2a.server import init_a2a_server
        from a2a.api import init_a2a_api
        from a2a.inbound_pipeline import InboundPipeline
        from a2a.outbound_pipeline import OutboundPipeline
        from a2a.client import A2AClient
        from a2a.types import A2AArtifact, A2AMessagePart

        receipt_service = getattr(main_orchestrator, "receipt_service", None)
        registry = A2ARegistry()
        soul_provider = lambda: getattr(main_orchestrator, "soul", None)
        client = A2AClient(receipt_service)
        vault = _connector_vault
        inbound = InboundPipeline(
            registry,
            receipt_service,
            soul_provider,
            vault=vault,
            a2a_client=client,
        )
        outbound = OutboundPipeline(
            registry,
            receipt_service,
            soul_provider,
            vault=vault,
            a2a_client=client,
            frontier_scrubber=(
                lambda: main_orchestrator.get_frontier_scrubber()
                if main_orchestrator is not None
                else None
            ),
        )

        def _execute_inbound_a2a_task(*, task, caller, quest_id):
            text_parts = []
            if task.message:
                for part in task.message.parts:
                    if part.text:
                        text_parts.append(part.text)
                    elif part.data is not None:
                        text_parts.append(json.dumps(part.data, sort_keys=True))
                    elif part.file_uri:
                        text_parts.append(f"[file] {part.file_uri}")
            user_message = "\n".join(p for p in text_parts if p).strip()
            if not user_message:
                raise ValueError("Inbound A2A task contained no executable content")
            envelope = (
                f"[External A2A task from {caller.display_name or caller.agent_id}"
                f" ({caller.agent_framework})]\n{user_message}"
            )
            response_text = main_orchestrator.chat(
                envelope,
                channel="api",
                quest_id=quest_id,
            )
            artifacts = [
                A2AArtifact(
                    parts=[A2AMessagePart(type="text", text=response_text)],
                    metadata={
                        "quest_id": quest_id,
                        "source": "lancelot",
                        "external_peer": caller.agent_id,
                    },
                ).to_dict()
            ]
            return {
                "status": "completed",
                "artifacts": artifacts,
                "message": "Task executed successfully.",
            }

        init_a2a_server(
            soul_provider,
            receipt_service,
            registry,
            inbound,
            task_executor=_execute_inbound_a2a_task,
            data_dir="/home/lancelot/data",
        )
        init_a2a_api(registry, receipt_service, soul_provider, outbound, client)
        logger.info("A2A subsystem hot-started.")
        return {
            "registry": registry,
            "client": client,
            "inbound": inbound,
            "outbound": outbound,
            "receipt_service": receipt_service,
        }

    def _shutdown_a2a_subsystem(objects):
        from a2a.api import shutdown_a2a_api
        from a2a.server import shutdown_a2a_server

        shutdown_a2a_api()
        shutdown_a2a_server()

    subsystem_manager.register(
        "a2a",
        "FEATURE_A2A",
        _init_a2a_subsystem,
        _shutdown_a2a_subsystem,
        ["/api/a2a", "/a2a", "/.well-known/agent.json"],
    )
    try:
        from feature_flags import FEATURE_A2A

        if FEATURE_A2A:
            entry = subsystem_manager.get("a2a")
            if entry and not entry.running:
                entry.objects = {"receipt_service": _optional_receipt_service}
                entry.running = True
    except Exception as exc:
        logger.warning("A2A lifecycle registration failed: %s", exc)

    # Incident response playbooks
    try:
        from feature_flags import FEATURE_INCIDENT_RESPONSE
        if FEATURE_INCIDENT_RESPONSE:
            from src.incidents.api import init_incidents_api
            from src.incidents.playbook_api import init_playbook_api
            from src.incidents.receipt_hook import configure as configure_incident_hook

            init_incidents_api(_optional_receipt_service, "/home/lancelot/data")

            _playbooks_dir = os.path.join(os.path.dirname(__file__), "..", "..", "playbooks")
            init_playbook_api(_playbooks_dir)

            configure_incident_hook(enabled=True, data_dir="/home/lancelot/data")
            try:
                from feature_flags import FEATURE_OBSERVABILITY
                if not FEATURE_OBSERVABILITY:
                    from src.observability.receipt_bridge import configure_bridge
                    configure_bridge(enabled=True, otel_enabled=False)
            except Exception as _bridge_exc:
                logger.debug("Incident receipt bridge activation skipped: %s", _bridge_exc)

            logger.info("FEATURE_INCIDENT_RESPONSE enabled; API at /api/incidents/, /api/playbooks/")
    except Exception as e:
        logger.warning(f"Incident Response initialization failed: {e}")

    def _init_incident_response_subsystem():
        from src.incidents.api import init_incidents_api
        from src.incidents.playbook_api import init_playbook_api
        from src.incidents.receipt_hook import configure as configure_incident_hook

        receipt_service = getattr(main_orchestrator, "receipt_service", None)
        init_incidents_api(receipt_service, "/home/lancelot/data")
        playbooks_dir = os.path.join(os.path.dirname(__file__), "..", "..", "playbooks")
        init_playbook_api(playbooks_dir)
        configure_incident_hook(enabled=True, data_dir="/home/lancelot/data")
        try:
            from feature_flags import FEATURE_OBSERVABILITY
            if not FEATURE_OBSERVABILITY:
                from src.observability.receipt_bridge import configure_bridge
                configure_bridge(enabled=True, otel_enabled=False)
        except Exception as exc:
            logger.debug("Incident receipt bridge activation skipped: %s", exc)
        logger.info("Incident Response subsystem hot-started.")
        return {"receipt_service": receipt_service, "playbooks_dir": playbooks_dir}

    def _shutdown_incident_response_subsystem(objects):
        from src.incidents.api import shutdown_incidents_api
        from src.incidents.playbook_api import shutdown_playbook_api
        from src.incidents.receipt_hook import configure as configure_incident_hook

        configure_incident_hook(enabled=False, data_dir="/home/lancelot/data")
        try:
            from feature_flags import FEATURE_OBSERVABILITY
            if not FEATURE_OBSERVABILITY:
                from src.observability.receipt_bridge import configure_bridge
                configure_bridge(enabled=False, otel_enabled=False)
        except Exception as exc:
            logger.debug("Incident receipt bridge shutdown skipped: %s", exc)
        shutdown_incidents_api()
        shutdown_playbook_api()

    subsystem_manager.register(
        "incident_response",
        "FEATURE_INCIDENT_RESPONSE",
        _init_incident_response_subsystem,
        _shutdown_incident_response_subsystem,
        ["/api/incidents", "/api/playbooks"],
    )
    try:
        from feature_flags import FEATURE_INCIDENT_RESPONSE

        if FEATURE_INCIDENT_RESPONSE:
            entry = subsystem_manager.get("incident_response")
            if entry and not entry.running:
                entry.objects = {"receipt_service": _optional_receipt_service}
                entry.running = True
    except Exception as exc:
        logger.warning("Incident Response lifecycle registration failed: %s", exc)

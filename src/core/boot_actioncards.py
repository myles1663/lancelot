"""ToolFlow and ActionCard boot lifecycle ownership."""

from __future__ import annotations


def init_toolflow_actioncards(app, *, main_orchestrator, subsystem_manager, telegram_bot, logger):
    try:
        from feature_flags import FEATURE_TOOL_FLOW_STREAMING, FEATURE_ACTION_CARDS
        from event_bus import event_bus as _event_bus

        if FEATURE_TOOL_FLOW_STREAMING:
            from toolflow.emitter import ToolFlowEmitter
            _toolflow_emitter = ToolFlowEmitter(event_bus=_event_bus, enabled=True)
            main_orchestrator.toolflow_emitter = _toolflow_emitter
            logger.info("ToolFlow streaming enabled; emitter injected into orchestrator")
        else:
            logger.info("ToolFlow streaming disabled by feature flag")

        if FEATURE_ACTION_CARDS:
            from actioncard.store import ActionCardStore
            from actioncard.factory import ActionCardFactory
            from actioncard.resolver import ActionCardResolver
            from actioncard_api import router as actioncard_router, init_actioncard_api

            _ac_store = ActionCardStore(data_dir=main_orchestrator.data_dir)
            _ac_factory = ActionCardFactory(card_store=_ac_store, event_bus=_event_bus)
            _ac_resolver = ActionCardResolver(
                card_store=_ac_store,
                event_bus=_event_bus,
                receipt_service=main_orchestrator.receipt_service,
            )

            try:
                from governance_api import _approve_item_direct, _deny_item_direct
                from src.core.operator_identity import OperatorIdentity

                def _gov_handler(item_id, button_id, **context):
                    identity = OperatorIdentity(
                        operator_id=context.get("operator_id", "") or "",
                        display_name=context.get("actor", "") or "",
                        session_id=context.get("session_id", "") or "",
                    )
                    card = context.get("card")
                    metadata = getattr(card, "metadata", {}) if card is not None else {}
                    batch_request_ids = metadata.get("approval_request_ids") or []
                    if metadata.get("approval_type") == "sentry_t3_batch" and batch_request_ids:
                        action = "approve" if button_id == "approve" else "deny" if button_id in ("deny", "reject") else ""
                        if not action:
                            return {"status": "error", "message": f"Unknown button: {button_id}"}
                        results = []
                        failures = []
                        for request_id in batch_request_ids:
                            if action == "approve":
                                result = _approve_item_direct(
                                    request_id,
                                    reason="Approved via grouped ActionCard",
                                    identity=identity if identity.operator_id and identity.display_name else None,
                                )
                            else:
                                result = _deny_item_direct(
                                    request_id,
                                    reason="Denied via grouped ActionCard",
                                    identity=identity if identity.operator_id and identity.display_name else None,
                                )
                            if result:
                                results.append(result)
                            else:
                                failures.append(request_id)
                        if failures:
                            return {
                                "status": "error",
                                "message": f"Could not {action} grouped request(s): {', '.join(failures)}",
                                "items": results,
                            }
                        return {
                            "status": "approved" if action == "approve" else "denied",
                            "message": (
                                f"{'Approved' if action == 'approve' else 'Denied'} "
                                f"{len(results)} grouped governance request(s)"
                            ),
                            "items": results,
                        }
                    if button_id == "approve":
                        result = _approve_item_direct(
                            item_id,
                            reason="Approved via ActionCard",
                            identity=identity if identity.operator_id and identity.display_name else None,
                        )
                        return result or {"status": "error", "message": f"Approval item {item_id} not found"}
                    elif button_id in ("deny", "reject"):
                        result = _deny_item_direct(
                            item_id,
                            reason="Denied via ActionCard",
                            identity=identity if identity.operator_id and identity.display_name else None,
                        )
                        return result or {"status": "error", "message": f"Approval item {item_id} not found"}
                    return {"status": "error", "message": f"Unknown button: {button_id}"}
                _ac_resolver.register_handler("governance", _gov_handler)
            except Exception as _e:
                logger.debug("Governance handler not available for ActionCards: %s", _e)

            try:
                if main_orchestrator.job_executor:
                    def _sched_handler(job_id, button_id, **context):
                        if button_id == "approve":
                            ok = main_orchestrator.job_executor.approve_job(
                                job_id,
                                operator_id=context.get("operator_id", "") or "",
                                session_id=context.get("session_id", "") or "",
                                actor=context.get("actor", "") or "",
                            )
                            return {"status": "approved" if ok else "error",
                                    "message": "Approved" if ok else "Not pending"}
                        return {"status": "denied", "message": "Denied"}
                    _ac_resolver.register_handler("scheduler", _sched_handler)
            except Exception as _e:
                logger.debug("Scheduler handler not available for ActionCards: %s", _e)

            try:
                from soul.api import _approve_proposal_direct, _reject_proposal_direct

                def _soul_handler(proposal_id, button_id, **context):
                    actor = context.get("actor", "") or context.get("operator_id", "") or "operator"
                    if button_id == "approve":
                        result = _approve_proposal_direct(proposal_id, actor=actor)
                        result["message"] = f"Soul proposal {proposal_id} approved via ActionCard"
                        return result
                    elif button_id in ("deny", "reject"):
                        result = _reject_proposal_direct(proposal_id, actor=actor)
                        result["message"] = f"Soul proposal {proposal_id} denied via ActionCard"
                        return result
                    return {"status": "error", "message": f"Unknown button: {button_id}"}
                _ac_resolver.register_handler("soul", _soul_handler)
            except Exception as _e:
                logger.debug("Soul handler not available for ActionCards: %s", _e)

            try:
                def _skills_handler(proposal_id, button_id, **context):
                    actor = context.get("actor", "") or context.get("operator_id", "") or "operator"
                    if button_id == "approve":
                        if main_orchestrator.skill_factory:
                            main_orchestrator.skill_factory.approve_proposal(
                                proposal_id,
                                approved_by=actor,
                            )
                            return {"status": "approved", "message": f"Skill proposal {proposal_id} approved"}
                        return {"status": "error", "message": "Skill factory not available"}
                    elif button_id in ("reject", "deny"):
                        if main_orchestrator.skill_factory:
                            main_orchestrator.skill_factory.reject_proposal(proposal_id)
                            return {"status": "denied", "message": f"Skill proposal {proposal_id} rejected"}
                        return {"status": "error", "message": "Skill factory not available"}
                    return {"status": "error", "message": f"Unknown button: {button_id}"}
                _ac_resolver.register_handler("skills", _skills_handler)
            except Exception as _e:
                logger.debug("Skills handler not available for ActionCards: %s", _e)

            try:
                from procedural_recommendations_api import resolve_recommendation_action
                from procedural_recommendations_api import bind_procedural_recommendations_actioncard_store

                bind_procedural_recommendations_actioncard_store(_ac_store)

                def _procedural_recommendation_handler(recommendation_id, button_id, **context):
                    return resolve_recommendation_action(
                        recommendation_id,
                        button_id,
                        operator_id=context.get("operator_id", "") or "",
                        session_id=context.get("session_id", "") or "",
                        actor=context.get("actor", "") or "",
                        channel="actioncard",
                    )

                _ac_resolver.register_handler(
                    "procedural_recommendations",
                    _procedural_recommendation_handler,
                )
            except Exception as _e:
                logger.debug("Procedural recommendation handler not available for ActionCards: %s", _e)

            init_actioncard_api(_ac_store, _ac_resolver)

            app.state.actioncard_store = _ac_store
            app.state.actioncard_factory = _ac_factory
            app.state.actioncard_resolver = _ac_resolver

            try:
                from soul.api import init_soul_actioncards
                init_soul_actioncards(_ac_factory)
            except Exception as _e:
                logger.debug("Soul ActionCard wiring skipped: %s", _e)
            try:
                if main_orchestrator.skill_factory:
                    main_orchestrator.skill_factory.actioncard_factory = _ac_factory
            except Exception as _e:
                logger.debug("Skills ActionCard wiring skipped: %s", _e)
            main_orchestrator.actioncard_factory = _ac_factory

            logger.info("ActionCards enabled; store, factory, resolver, API initialized")
        else:
            logger.info("ActionCards disabled by feature flag")
    except Exception as e:
        logger.warning("ToolFlow/ActionCards initialization failed: %s", e)

    def _init_toolflow_streaming():
        from event_bus import event_bus as _event_bus
        from toolflow.emitter import ToolFlowEmitter

        emitter = ToolFlowEmitter(event_bus=_event_bus, enabled=True)
        main_orchestrator.toolflow_emitter = emitter
        logger.info("ToolFlow streaming hot-started.")
        return {"emitter": emitter}

    def _shutdown_toolflow_streaming(objects):
        emitter = objects.get("emitter") or getattr(main_orchestrator, "toolflow_emitter", None)
        if emitter is not None and hasattr(emitter, "enabled"):
            emitter.enabled = False
        main_orchestrator.toolflow_emitter = None
        logger.info("ToolFlow streaming stopped.")

    def _wire_actioncard_telegram_runtime(*, store, resolver):
        if not telegram_bot:
            return
        from event_bus import event_bus as _event_bus

        if not getattr(telegram_bot, "_actioncard_event_bridge_wired", False):
            _event_bus.subscribe("actioncard_presented", telegram_bot.handle_actioncard_event)
            _event_bus.subscribe("actioncard_resolved", telegram_bot.handle_actioncard_resolved_event)
            telegram_bot._actioncard_event_bridge_wired = True
        telegram_bot.attach_actioncard_runtime(resolver=resolver, store=store)

    def _init_actioncards():
        from actioncard.store import ActionCardStore
        from actioncard.factory import ActionCardFactory
        from actioncard.resolver import ActionCardResolver
        from actioncard_api import init_actioncard_api
        from event_bus import event_bus as _event_bus

        card_store = ActionCardStore(data_dir=main_orchestrator.data_dir)
        card_factory = ActionCardFactory(card_store=card_store, event_bus=_event_bus)
        card_resolver = ActionCardResolver(
            card_store=card_store,
            event_bus=_event_bus,
            receipt_service=main_orchestrator.receipt_service,
        )

        try:
            from governance_api import _approve_item_direct, _deny_item_direct
            from src.core.operator_identity import OperatorIdentity

            def _gov_handler(item_id, button_id, **context):
                identity = OperatorIdentity(
                    operator_id=context.get("operator_id", "") or "",
                    display_name=context.get("actor", "") or "",
                    session_id=context.get("session_id", "") or "",
                )
                identity_arg = identity if identity.operator_id and identity.display_name else None
                if button_id == "approve":
                    result = _approve_item_direct(
                        item_id,
                        reason="Approved via ActionCard",
                        identity=identity_arg,
                    )
                    return result or {"status": "error", "message": f"Approval item {item_id} not found"}
                if button_id in ("deny", "reject"):
                    result = _deny_item_direct(
                        item_id,
                        reason="Denied via ActionCard",
                        identity=identity_arg,
                    )
                    return result or {"status": "error", "message": f"Approval item {item_id} not found"}
                return {"status": "error", "message": f"Unknown button: {button_id}"}

            card_resolver.register_handler("governance", _gov_handler)
        except Exception as exc:
            logger.debug("Governance handler not available for ActionCards: %s", exc)

        try:
            if main_orchestrator.job_executor:
                def _sched_handler(job_id, button_id, **context):
                    if button_id == "approve":
                        ok = main_orchestrator.job_executor.approve_job(
                            job_id,
                            operator_id=context.get("operator_id", "") or "",
                            session_id=context.get("session_id", "") or "",
                            actor=context.get("actor", "") or "",
                        )
                        return {
                            "status": "approved" if ok else "error",
                            "message": "Approved" if ok else "Not pending",
                        }
                    return {"status": "denied", "message": "Denied"}
                card_resolver.register_handler("scheduler", _sched_handler)
        except Exception as exc:
            logger.debug("Scheduler handler not available for ActionCards: %s", exc)

        try:
            from soul.api import _approve_proposal_direct, _reject_proposal_direct

            def _soul_handler(proposal_id, button_id, **context):
                actor = context.get("actor", "") or context.get("operator_id", "") or "operator"
                if button_id == "approve":
                    result = _approve_proposal_direct(proposal_id, actor=actor)
                    result["message"] = f"Soul proposal {proposal_id} approved via ActionCard"
                    return result
                if button_id in ("deny", "reject"):
                    result = _reject_proposal_direct(proposal_id, actor=actor)
                    result["message"] = f"Soul proposal {proposal_id} denied via ActionCard"
                    return result
                return {"status": "error", "message": f"Unknown button: {button_id}"}

            card_resolver.register_handler("soul", _soul_handler)
        except Exception as exc:
            logger.debug("Soul handler not available for ActionCards: %s", exc)

        try:
            def _skills_handler(proposal_id, button_id, **context):
                actor = context.get("actor", "") or context.get("operator_id", "") or "operator"
                if button_id == "approve":
                    if main_orchestrator.skill_factory:
                        main_orchestrator.skill_factory.approve_proposal(
                            proposal_id,
                            approved_by=actor,
                        )
                        return {"status": "approved", "message": f"Skill proposal {proposal_id} approved"}
                    return {"status": "error", "message": "Skill factory not available"}
                if button_id in ("reject", "deny"):
                    if main_orchestrator.skill_factory:
                        main_orchestrator.skill_factory.reject_proposal(proposal_id)
                        return {"status": "denied", "message": f"Skill proposal {proposal_id} rejected"}
                    return {"status": "error", "message": "Skill factory not available"}
                return {"status": "error", "message": f"Unknown button: {button_id}"}
            card_resolver.register_handler("skills", _skills_handler)
        except Exception as exc:
            logger.debug("Skills handler not available for ActionCards: %s", exc)

        try:
            from procedural_recommendations_api import resolve_recommendation_action
            from procedural_recommendations_api import bind_procedural_recommendations_actioncard_store

            bind_procedural_recommendations_actioncard_store(card_store)

            def _procedural_recommendation_handler(recommendation_id, button_id, **context):
                return resolve_recommendation_action(
                    recommendation_id,
                    button_id,
                    operator_id=context.get("operator_id", "") or "",
                    session_id=context.get("session_id", "") or "",
                    actor=context.get("actor", "") or "",
                    channel="actioncard",
                )

            card_resolver.register_handler(
                "procedural_recommendations",
                _procedural_recommendation_handler,
            )
        except Exception as exc:
            logger.debug("Procedural recommendation handler not available for ActionCards: %s", exc)

        init_actioncard_api(card_store, card_resolver)
        app.state.actioncard_store = card_store
        app.state.actioncard_factory = card_factory
        app.state.actioncard_resolver = card_resolver
        main_orchestrator.actioncard_factory = card_factory

        try:
            from soul.api import init_soul_actioncards

            init_soul_actioncards(card_factory)
        except Exception as exc:
            logger.debug("Soul ActionCard wiring skipped: %s", exc)
        try:
            if main_orchestrator.skill_factory:
                main_orchestrator.skill_factory.actioncard_factory = card_factory
        except Exception as exc:
            logger.debug("Skills ActionCard wiring skipped: %s", exc)

        try:
            _wire_actioncard_telegram_runtime(store=card_store, resolver=card_resolver)
        except Exception as exc:
            logger.warning("Telegram ActionCard runtime wiring failed: %s", exc)

        logger.info("ActionCards hot-started.")
        return {
            "store": card_store,
            "factory": card_factory,
            "resolver": card_resolver,
        }

    def _shutdown_actioncards(objects):
        from actioncard_api import shutdown_actioncard_api

        shutdown_actioncard_api()
        for attr in ("actioncard_store", "actioncard_factory", "actioncard_resolver"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)
        main_orchestrator.actioncard_factory = None
        try:
            if main_orchestrator.skill_factory:
                main_orchestrator.skill_factory.actioncard_factory = None
        except Exception as exc:
            logger.debug("Skills ActionCard unwiring skipped: %s", exc)
        try:
            if telegram_bot:
                telegram_bot.attach_actioncard_runtime(resolver=None, store=None)
        except Exception as exc:
            logger.debug("Telegram ActionCard runtime detach skipped: %s", exc)
        logger.info("ActionCards stopped.")

    subsystem_manager.register(
        "toolflow_streaming",
        "FEATURE_TOOL_FLOW_STREAMING",
        _init_toolflow_streaming,
        _shutdown_toolflow_streaming,
        [],
    )
    subsystem_manager.register(
        "actioncards",
        "FEATURE_ACTION_CARDS",
        _init_actioncards,
        _shutdown_actioncards,
        ["/api/actioncards"],
    )
    try:
        from feature_flags import FEATURE_TOOL_FLOW_STREAMING, FEATURE_ACTION_CARDS

        if FEATURE_TOOL_FLOW_STREAMING:
            entry = subsystem_manager.get("toolflow_streaming")
            if entry and not entry.running:
                entry.objects = {"emitter": getattr(main_orchestrator, "toolflow_emitter", None)}
                entry.running = True
        if FEATURE_ACTION_CARDS:
            entry = subsystem_manager.get("actioncards")
            if entry and not entry.running:
                entry.objects = {
                    "store": getattr(app.state, "actioncard_store", None),
                    "factory": getattr(app.state, "actioncard_factory", None),
                    "resolver": getattr(app.state, "actioncard_resolver", None),
                }
                entry.running = True
    except Exception as exc:
        logger.warning("ToolFlow/ActionCard lifecycle registration failed: %s", exc)

    return _wire_actioncard_telegram_runtime

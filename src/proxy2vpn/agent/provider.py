"""Manual provider operations with durable claims and independently verified facts."""

import asyncio
import ipaddress
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from proxy2vpn.adapters.external_proxy import (
    external_config_path,
    load_external_endpoints,
)
from proxy2vpn.adapters.iproyal import IPRoyalMobileAdapter
from proxy2vpn.agent.models import ActionRecord, AgentState, AgentStatus
from proxy2vpn.agent.retention import as_utc
from proxy2vpn.agent.state import AgentStateStore
from proxy2vpn.core.iproyal import ControlResult, ProviderOperation, valid_rotation_key


class ProviderOperationError(RuntimeError):
    """Safe operator message without credential, URL, or response interpolation."""


class MobileOperations:
    """One compose-root coordinator; other hosts require external coordination."""

    def __init__(self, store: AgentStateStore):
        self.store = store

    def _snapshot(self, name):
        path = external_config_path(
            self.store.compose_file, self.store.settings.external_proxies_file
        )
        endpoint = next(
            (e for e in load_external_endpoints(path) if e.name == name), None
        )
        if endpoint is None or endpoint.mobile_provider is None:
            raise ProviderOperationError(
                "Endpoint does not support manual mobile exit-IP requests"
            )
        provider = endpoint.mobile_provider
        token = os.environ.get(provider.access_token_env)
        key = os.environ.get(provider.rotation_key_env)
        credentials = None
        if endpoint.credentials is not None:
            username = os.environ.get(endpoint.credentials.username_env)
            password = os.environ.get(endpoint.credentials.password_env)
            if not username or not password or ":" in username:
                raise ProviderOperationError(
                    "Proxy credential references are missing or invalid"
                )
            try:
                (username + ":" + password).encode("latin1")
            except UnicodeEncodeError:
                raise ProviderOperationError("Proxy credentials are invalid") from None
            credentials = (username, password)
        if (
            not token
            or not key
            or not valid_rotation_key(key)
            or any(ord(c) < 32 or ord(c) >= 127 for c in token)
        ):
            raise ProviderOperationError(
                "Provider credential references are missing or invalid"
            )
        sanitizer = self.store.sanitizer()
        resource = sanitizer.metric_identity(
            "iproyal_mobile\0" + key, domain="provider_resource"
        )
        fingerprint = sanitizer.metric_identity(
            json.dumps(
                [endpoint.model_dump(mode="json"), token, key, credentials],
                sort_keys=True,
            ),
            domain="provider_configuration",
        )
        adapter = IPRoyalMobileAdapter(
            endpoint,
            self.store.settings.probe_timeout_seconds,
            credential_values=credentials,
        )
        return endpoint, token, key, resource, fingerprint, adapter

    def _state(self):
        return self.store.read_state() or AgentState(
            status=AgentStatus(
                compose_path=str(self.store.compose_file),
                interval_seconds=self.store.settings.interval_seconds,
                llm_mode=self.store.settings.llm_mode,
            )
        )

    def _patch(self, operation, mutate):
        with self.store.provider_transaction():
            state = self._state()
            current = next(
                (
                    o
                    for o in state.provider_operations
                    if o.resource_id == operation.resource_id
                ),
                None,
            )
            if current is None or current.operation_id != operation.operation_id:
                raise ProviderOperationError("Provider operation ownership changed")
            mutate(current, state)
            self.store.write_state(state)
            return current.model_copy(deep=True)

    def _result(self, operation, result, cooldown):
        now = datetime.now(timezone.utc)

        def commit(current, state):
            if current.audit_recorded:
                return
            current.request_outcome = result.request_outcome
            current.reason_code = result.reason_code
            current.retry_after_seconds = result.retry_after_seconds
            current.operation_phase = (
                "unknown" if result.request_outcome == "unknown" else "completed"
            )
            current.completed_at = now
            guard = (
                max(cooldown, 1800) if result.request_outcome == "unknown" else cooldown
            )
            guard = max(guard, result.retry_after_seconds or 0)
            current.cooldown_until = max(
                as_utc(current.cooldown_until)
                if result.request_outcome == "unknown"
                else now,
                now + timedelta(seconds=guard),
            )
            audit_result = {
                "acknowledged": "accepted",
                "rejected": "rejected",
                "unknown": "unknown",
                "not_dispatched": "failed",
            }[result.request_outcome]
            action = ActionRecord(
                ts=now,
                service_name=current.service_name,
                source="external_proxy",
                action="request_different_exit_ip",
                trigger="manual",
                result=audit_result,
                details={
                    "runtime_request_result": audit_result,
                    "request_outcome": result.request_outcome,
                    "reason_code": result.reason_code,
                    "operation_id": current.operation_id,
                },
            )
            state.actions.append(action)
            state.actions = state.actions[-self.store.settings.action_history_limit :]
            state.metrics.count_recovery(action, state.services)
            current.audit_recorded = True

        return self._patch(operation, commit)

    async def request(self, name):
        endpoint, token, key, resource, fingerprint, adapter = self._snapshot(name)
        provider = endpoint.mobile_provider
        assert provider is not None
        now = datetime.now(timezone.utc)
        if provider.expires_at is not None and as_utc(provider.expires_at) <= now:
            raise ProviderOperationError(
                "Operator-configured provider expiry has passed"
            )
        with self.store.provider_transaction():
            state = self._state()
            existing = next(
                (o for o in state.provider_operations if o.resource_id == resource),
                None,
            )
            if existing is not None and (
                now < as_utc(existing.cooldown_until) or (not existing.audit_recorded)
            ):
                raise ProviderOperationError(
                    "Provider request is guarded by a pending operation or cooldown"
                )
            if existing is None and len(state.provider_operations) >= 1000:
                raise ProviderOperationError(
                    "Provider operation inventory is full; retain existing guards"
                )
            operation = ProviderOperation(
                operation_id=uuid4().hex,
                service_name=name,
                resource_id=resource,
                configuration_id=fingerprint,
                started_at=now,
                cooldown_until=now
                + timedelta(seconds=max(1800, provider.cooldown_seconds)),
            )
            state.provider_operations = [
                o for o in state.provider_operations if o.resource_id != resource
            ] + [operation]
            self.store.write_state(state)
        dispatched = False
        try:
            baseline = await adapter.observe()
            if self._snapshot(name)[4] != fingerprint:
                return self._result(
                    operation,
                    ControlResult(
                        request_outcome="not_dispatched",
                        reason_code="configuration_changed",
                    ),
                    provider.cooldown_seconds,
                )

            def intent(current, state):
                if provider.expires_at is not None and as_utc(
                    provider.expires_at
                ) <= datetime.now(timezone.utc):
                    raise ProviderOperationError(
                        "Operator-configured provider expiry has passed"
                    )
                if (
                    current.operation_phase != "reserved"
                    or current.audit_recorded
                    or datetime.now(timezone.utc) >= as_utc(current.cooldown_until)
                ):
                    raise ProviderOperationError(
                        "Provider reservation no longer authorizes dispatch"
                    )
                current.operation_phase = "dispatched"
                current.request_outcome = "unknown"
                current.reason_code = "dispatch_intent"
                current.cooldown_until = datetime.now(timezone.utc) + timedelta(
                    seconds=max(1800, provider.cooldown_seconds)
                )

            operation = self._patch(operation, intent)
            dispatched = True
            result = await adapter.request_exit_ip(token, key)
            operation = self._result(operation, result, provider.cooldown_seconds)
            after = await adapter.observe()
            changed = self._snapshot(name)[4] != fingerprint

            def verify(current, state):
                if changed:
                    current.verification_state = "configuration_changed"
                    return
                current.verification_state = "completed"
                current.authentication = after.authentication
                current.connectivity = after.connectivity
                current.connectivity_restored = (
                    after.connectivity
                    if baseline.connectivity is False
                    else False
                    if baseline.connectivity is True and after.connectivity is not None
                    else None
                )
                before_ip, after_ip = (
                    baseline.current_egress_ip,
                    after.current_egress_ip,
                )
                if (
                    before_ip
                    and after_ip
                    and ipaddress.ip_address(before_ip).version
                    == ipaddress.ip_address(after_ip).version
                ):
                    current.exit_change = (
                        "changed" if before_ip != after_ip else "unchanged"
                    )

            return self._patch(operation, verify)
        except asyncio.CancelledError:
            # Cancellation after durable intent cannot establish that no request
            # reached the vendor. Never retry the HTTP call.
            self._result(
                operation,
                ControlResult(
                    request_outcome="unknown" if dispatched else "not_dispatched",
                    reason_code="cancelled",
                ),
                provider.cooldown_seconds,
            )
            raise
        except (ValueError, ProviderOperationError):
            return self._result(
                operation,
                ControlResult(
                    request_outcome="unknown" if dispatched else "not_dispatched",
                    reason_code="configuration_changed",
                ),
                provider.cooldown_seconds,
            )

    def status(self, name):
        with self.store.provider_transaction():
            state = self._state()
            return [
                o.model_copy(deep=True)
                for o in state.provider_operations
                if o.service_name == name
            ]

    async def reconcile(self, name):
        """Observe only; a lost request is never replayed or inferred successful."""
        endpoint, token, key, resource, fingerprint, adapter = self._snapshot(name)
        with self.store.provider_transaction():
            state = self._state()
            operation = next(
                (
                    o.model_copy(deep=True)
                    for o in state.provider_operations
                    if o.resource_id == resource
                ),
                None,
            )
        if operation is None:
            raise ProviderOperationError(
                "No provider operation exists for this resource"
            )
        if not operation.audit_recorded:
            if datetime.now(timezone.utc) < as_utc(operation.cooldown_until):
                raise ProviderOperationError(
                    "Pending operation remains inside its uncertainty guard"
                )
            provider = endpoint.mobile_provider
            assert provider is not None
            operation = self._result(
                operation,
                ControlResult(
                    request_outcome="unknown"
                    if operation.operation_phase == "dispatched"
                    else "not_dispatched",
                    reason_code="timeout"
                    if operation.operation_phase == "dispatched"
                    else "cancelled",
                ),
                provider.cooldown_seconds,
            )
        observation = await adapter.observe()
        changed = (
            self._snapshot(name)[4] != operation.configuration_id
            or fingerprint != operation.configuration_id
        )

        def record(current, state):
            if changed:
                current.verification_state = "configuration_changed"
                return
            current.verification_state = "completed"
            current.authentication = observation.authentication
            current.connectivity = observation.connectivity
            # Reconciliation has no fresh pre-request baseline. Preserve original
            # change facts; an observed IP alone cannot attribute a request effect.

        return self._patch(operation, record)

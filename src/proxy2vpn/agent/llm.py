"""OpenAI-backed incident enrichment and investigation for the proxy2vpn agent."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from proxy2vpn.adapters.logging_utils import get_logger
from proxy2vpn.agent.config import AgentSettings

logger = get_logger(__name__)


class IncidentContext(BaseModel):
    """Structured incident context sent to the LLM."""

    service_name: str
    fallback_summary: str
    recommended_action: str
    failure_count: int
    issues: list[dict[str, Any]]
    recent_actions: list[dict[str, str]]

    model_config = ConfigDict(extra="ignore")


class IncidentEnrichment(BaseModel):
    """Structured enrichment returned by the LLM."""

    summary: str
    human_explanation: str

    model_config = ConfigDict(extra="ignore")


class InvestigationContext(BaseModel):
    """Structured incident context sent to the investigator."""

    incident_id: str
    incident_type: str
    severity: str
    status: str
    service_name: str
    incident_summary: str
    recommended_action: str
    failure_count: int
    provider: str | None = None
    location: str | None = None
    profile_name: str | None = None
    profile_env_file: str | None = None
    container_status: str
    health_score: int | None = None
    control_api_reachable: bool | None = None
    profile_validation_errors: list[str]
    issues: list[dict[str, Any]]
    recent_actions: list[dict[str, str]]
    human_explanation: str | None = None

    model_config = ConfigDict(extra="ignore")


class InvestigationPlan(BaseModel):
    """Structured investigation result returned by the LLM."""

    summary: str
    findings: list[str]
    action_plan: list[str]

    model_config = ConfigDict(extra="ignore")


class _OpenAIResponderBase:
    """Shared OpenAI client setup for structured watchdog outputs."""

    def __init__(
        self,
        model: str | None = None,
        timeout_seconds: float | None = None,
        max_output_tokens: int | None = None,
        reasoning_effort: str | None = None,
        settings: AgentSettings | None = None,
        client: Any | None = None,
    ) -> None:
        self.settings = settings or AgentSettings()
        self.model = (model or self.settings.openai_model).strip()
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else self.settings.openai_timeout_seconds
        )
        self.max_output_tokens = int(
            max_output_tokens
            if max_output_tokens is not None
            else self.settings.openai_max_output_tokens
        )
        self.reasoning_effort = (
            reasoning_effort or self.settings.openai_reasoning_effort
        ).strip()
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        try:
            from openai import OpenAI
        except (
            ImportError
        ) as exc:  # pragma: no cover - import is runtime/environment dependent
            raise RuntimeError(
                "OpenAI SDK is not installed. Install the 'openai' package to enable LLM enrichment."
            ) from exc

        self._client = OpenAI()
        return self._client

    def _parse(self, *, system_prompt: str, user_prompt: str, output_model: Any) -> Any:
        client = self._get_client()
        response = client.responses.parse(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
            text_format=output_model,
            reasoning={"effort": self.reasoning_effort},
            max_output_tokens=self.max_output_tokens,
            timeout=self.timeout_seconds,
        )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise RuntimeError(
                "OpenAI response did not return parsed structured output"
            )
        return parsed


class OpenAIIncidentEnricher(_OpenAIResponderBase):
    """Use OpenAI to improve incident summaries without changing decisions."""

    def enrich(self, context: IncidentContext) -> IncidentEnrichment:
        """Return a structured summary and human explanation for one incident."""

        return self._parse(
            system_prompt=(
                "You summarize infrastructure watchdog incidents. "
                "Do not recommend new actions outside the provided recommended_action. "
                "Be concise, factual, and operator-oriented."
            ),
            user_prompt=(
                "Produce incident JSON for this service.\n"
                f"{context.model_dump_json(indent=2)}"
            ),
            output_model=IncidentEnrichment,
        )


class OpenAIIncidentInvestigator(_OpenAIResponderBase):
    """Use OpenAI to produce a concise operator action plan for an incident."""

    def investigate(self, context: InvestigationContext) -> InvestigationPlan:
        """Return a structured investigation summary with findings and plan."""

        return self._parse(
            system_prompt=(
                "You investigate proxy2vpn watchdog incidents. "
                "Be concise, factual, and operator-oriented. "
                "Do not expose secrets, redact credential values, and keep action_plan "
                "to safe operational steps that a proxy2vpn operator can execute."
            ),
            user_prompt=(
                "Produce investigation JSON for this incident.\n"
                f"{context.model_dump_json(indent=2)}"
            ),
            output_model=InvestigationPlan,
        )

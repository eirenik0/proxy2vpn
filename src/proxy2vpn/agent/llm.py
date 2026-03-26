"""OpenAI-backed incident enrichment for the proxy2vpn agent."""

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


class OpenAIIncidentEnricher:
    """Use OpenAI to improve incident summaries without changing decisions."""

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

    def enrich(self, context: IncidentContext) -> IncidentEnrichment:
        """Return a structured summary and human explanation for one incident."""

        client = self._get_client()
        response = client.responses.parse(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You summarize infrastructure watchdog incidents. "
                        "Do not recommend new actions outside the provided recommended_action. "
                        "Be concise, factual, and operator-oriented."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Produce incident JSON for this service.\n"
                        f"{context.model_dump_json(indent=2)}"
                    ),
                },
            ],
            text_format=IncidentEnrichment,
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

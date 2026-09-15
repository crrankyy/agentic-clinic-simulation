"""The judge: scores a final answer against ground truth.

Runs on the **Anthropic SDK**, not OpenRouter. That is an explicit, recorded
override of brief §0.2 (D-021), taken for two reasons: judge calls then do not
consume the OpenRouter free-tier daily allowance, and the judge must never be a
weaker model than the agents it grades — which matters a great deal when the
agents run on a free model.

It is the only component that ever sees `Correct_Diagnosis`, and it runs
**outside the encounter graph entirely** (Q-21), constructed by the eval runner
after the encounter has returned and its state has been discarded. That is what
makes "ground truth never reaches an encounter checkpoint" a structural fact
rather than a per-boundary argument.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import CONFIG_DIR
from ..data.views import JudgeView
from ..graphs.schemas import FinalAnswer, JudgeVerdict


class JudgeAuthMode(RuntimeError):
    """The judge would have used a credential the project does not want."""


class JudgeParseFailure(RuntimeError):
    """The judge produced no usable verdict. Never synthesised into one."""


class JudgeCallCapExceeded(RuntimeError):
    """The per-run judge call cap was hit — it is the judge's only limit."""


def build_prompt(view: JudgeView, final: FinalAnswer, config_dir: Path | None = None) -> str:
    template = (config_dir or CONFIG_DIR).joinpath("prompts", "judge.md").read_text(encoding="utf-8")
    differential = "\n".join(
        f"{i}. {d.diagnosis}" for i, d in enumerate(final.differential, 1)
    ) or "(empty)"
    return template.format(
        correct_diagnosis=view.correct_diagnosis,
        diagnosis=final.diagnosis or "(abstained)",
        differential=differential,
    )


class Judge:
    """Wraps the Anthropic client, the call cap, and the separate trace stream."""

    def __init__(
        self,
        model: str = "claude-opus-5",
        max_calls_per_run: int = 200,
        client: Any = None,
        tracer: Any = None,
        config_dir: Path | None = None,
        require_subscription: bool = True,
    ) -> None:
        self.model = model
        self.max_calls_per_run = max_calls_per_run
        self._client = client
        self.tracer = tracer
        self.config_dir = config_dir
        #: D-046: refuse to fall back to API-key billing without it being asked for.
        self.require_subscription = require_subscription
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0

    def auth_mode(self) -> str:
        """Which credential the SDK will actually use, resolved the same way it does.

        The order is fixed by the SDK: `ANTHROPIC_API_KEY`, then
        `ANTHROPIC_AUTH_TOKEN`, then the OAuth profile written by `ant auth
        login`. That order is why this check exists — an API key appearing in the
        environment would silently take precedence over the subscription profile,
        and the first sign would be a bill.
        """
        import os

        if os.environ.get("ANTHROPIC_API_KEY"):
            return "api_key"
        if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return "auth_token"
        profile_dir = Path.home() / ".config" / "anthropic"
        if (profile_dir / "configs").exists() or (profile_dir / "credentials.json").exists():
            return "oauth_profile"
        return "none"

    def _ensure_client(self) -> Any:
        if self._client is None:
            from anthropic import AsyncAnthropic

            mode = self.auth_mode()
            if self.require_subscription and mode in {"api_key", "auth_token"}:
                raise JudgeAuthMode(
                    f"the judge is configured for subscription auth, but the SDK would "
                    f"use {mode} because that environment variable is set. Unset it, or "
                    f"set judge.auth: any in config/models.yaml to allow API billing."
                )
            if mode == "none":
                raise JudgeAuthMode(
                    "no Anthropic credential found. Run `ant auth login` in a terminal "
                    "(it opens a browser and must not be backgrounded)."
                )
            # A bare constructor resolves the OAuth profile — no key needed.
            self._client = AsyncAnthropic()
        return self._client

    async def verdict(self, *, case_id: str, final: FinalAnswer, view: JudgeView) -> JudgeVerdict:
        """Score one answer. The caller must not invoke this for an abstention."""
        if self.calls >= self.max_calls_per_run:
            raise JudgeCallCapExceeded(
                f"judge call cap of {self.max_calls_per_run} reached (D-037). "
                "Something is looping, or the run is larger than expected."
            )
        self.calls += 1

        prompt = build_prompt(view, final, self.config_dir)
        client = self._ensure_client()
        parsed = await client.messages.parse(
            model=self.model,
            max_tokens=4096,
            output_format=JudgeVerdict,
            messages=[{"role": "user", "content": prompt}],
        )
        verdict = getattr(parsed, "parsed_output", None)
        if verdict is None:
            # A refusal or a truncated response yields no parsed output. Raising
            # keeps the failure visible; a synthesised verdict would silently
            # become a data point.
            raise JudgeParseFailure(
                f"judge returned no parsed output for {case_id} "
                f"(stop_reason={getattr(parsed, 'stop_reason', None)})"
            )

        usage = getattr(parsed, "usage", None)
        if usage is not None:
            self.tokens_in += int(getattr(usage, "input_tokens", 0) or 0)
            self.tokens_out += int(getattr(usage, "output_tokens", 0) or 0)

        if self.tracer is not None:
            # Judge records go to judge.jsonl only — never a per-case trace,
            # which is a doctor-side artefact.
            self.tracer.judge(
                case_id=case_id, match_type=verdict.match_type,
                correct=verdict.correct, lenient=verdict.lenient_correct,
                entry_matches=verdict.entry_matches, model=self.model,
            )
        return verdict


def top_k(verdict: JudgeVerdict, k: int) -> bool:
    """Whether any of the first `k` differential entries matched.

    Computed from the judge's own per-entry booleans, not from string
    comparison: a pure-Python match would systematically disagree with
    `match_type == "synonym"` and produce a report where top-1 accuracy is lower
    than overall accuracy, which is incoherent.
    """
    return any(verdict.entry_matches[:k])

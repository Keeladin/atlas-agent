from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Literal

DeliverableKind = Literal["narrative", "answer", "conversation", "analysis", "code", "generic"]
ProducedKind = Literal["narrative", "analysis", "code", "structured", "prose", "empty"]
PresentationProfile = Literal[
    "conversational",
    "answer",
    "evidence",
    "research",
    "compose",
    "execute",
]

INTERNAL_ARTIFACT_KINDS = frozenset(
    {
        "verification_result",
        "execution_receipt",
        "task_plan",
        "planning_request",
        "morning_request",
        "knowledge_search_request",
        "capability_request",
    }
)

_QUALITY_CRITERION = re.compile(
    r"\b(believable|compelling|vivid|immersive|well[- ]written|high[- ]quality)\b",
    re.IGNORECASE,
)

_NARRATIVE_REQUEST = re.compile(
    r"""
    \bshort\s+stor(?:y|ies)\b
    | \b(?:fairy|folk|ghost|love)\s+tale\b
    | \b(?:tell|write|compose|draft|spin)\b
      .{0,60}
      \b(?:stor(?:y|ies)|tale|fable|parable|poem|poetry|sonnet|haiku|limerick|
          letter|email|speech|script|joke|monologue)\b
    | \b(?:poem|poetry|sonnet|haiku|limerick|fable|parable)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_USER_STORY = re.compile(r"\buser\s+stor(?:y|ies)\b", re.IGNORECASE)
_SUCCESS_STORY = re.compile(r"\bsuccess\s+stor(?:y|ies)\b", re.IGNORECASE)

_ANALYSIS_REQUEST = re.compile(
    r"""
    \b(?:analy[sz]e|investigat(?:e|ion)|compare|trade-?offs?|root\s+cause)\b
    | \bresearch\s+(?:this|the|whether|how|why|into)\b
    | \b(?:do|conduct|perform)\s+(?:a\s+)?research\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_CODE_REQUEST = re.compile(
    r"\b(?:implement|refactor|write\s+(?:a\s+|the\s+)?(?:function|class|module|test|patch))\b",
    re.IGNORECASE,
)

_DEEP_ANALYSIS = re.compile(
    r"""
    \bdeep(?:er)?\s+(?:analys[ie]s|dive|investigation|research)\b
    | \bin[- ]depth\s+(?:analys|review|investigation|research)
    | \bthorough(?:ly)?\s+(?:analys|investigat|research)
    | \bfull\s+(?:research|investigation)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_HIGH_STAKES = re.compile(
    r"""
    \b(?:medical|legal|financial|investment|tax)\s+advice\b
    | \bshould\s+i\s+(?:invest|sue|take|stop\s+taking)\b
    | \b(?:suicid(?:e|al)|self[- ]harm)\b
    | \b(?:prove|disprove|debunk)\s+that\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_CASUAL = re.compile(
    r"""
    ^\s*(?:hi|hello|hey|yo|thanks|thank\s+you|thx|
          good\s+(?:morning|afternoon|evening|night)|
          how\s+are\s+you|how'?s\s+it\s+going|what'?s\s+up|sup)
    [\s!.?]*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

_FACTUAL = re.compile(
    r"""
    \?
    | ^\s*(?:what|when|where|who|whom|which|whose|how|why|define|explain)\b
    | \b(?:what\s+is|what\s+are|what\s+was|who\s+is|how\s+(?:much|many|long|does|do|did|is|are))\b
    | \btell\s+me\s+(?:about|what|who|when|where|why|how)\b
    | \b(?:definition|meaning)\s+of\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_ANALYSIS_MARKERS = (
    "### evidence",
    "## evidence",
    "### uncertainty",
    "## uncertainty",
    "### inference",
    "user objective:",
    "success criteria:",
    "system constraints:",
    "current artifact status:",
    "scope of investigation",
    "definition of \"believability\"",
    "definition of “believability”",
    "there is no external evidence",
    "investigate before concluding",
    "separate evidence, uncertainty and inference",
)

_CODE_MARKERS = (
    "def ",
    "class ",
    "function ",
    "import ",
    "```python",
    "```js",
    "```ts",
)


@dataclass(frozen=True)
class DeliverableContract:
    kind: DeliverableKind
    requested: str
    must_produce: str
    must_not_produce: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_user_facing(self) -> bool:
        return self.kind != "generic"

    @property
    def requires_semantic_check(self) -> bool:
        return self.kind in {"narrative", "code"}


def infer_presentation_profile(
    objective: str,
    success_criteria: Iterable[str] = (),
) -> PresentationProfile:
    """Select a user-facing presentation profile independent of capability.

    `reasoning.general` may still execute the step. It must not automatically
    mean "write an Evidence / Uncertainty / Inference report."
    """
    contract = infer_deliverable(objective, success_criteria)
    text = " ".join([objective, *success_criteria])
    if contract.kind == "narrative":
        return "compose"
    if contract.kind == "code":
        return "execute"
    if contract.kind == "conversation":
        return "conversational"
    if contract.kind == "answer":
        return "answer"
    if contract.kind == "analysis":
        if _DEEP_ANALYSIS.search(text):
            return "research"
        return "evidence"
    return "execute"


def infer_deliverable(
    objective: str,
    success_criteria: Iterable[str] = (),
) -> DeliverableContract:
    text = " ".join(
        [objective, *success_criteria]
    )
    if _looks_like_narrative_request(text):
        requested = _requested_narrative_label(text)
        return DeliverableContract(
            kind="narrative",
            requested=requested,
            must_produce=f"the requested {requested} itself",
            must_not_produce=(
                "analysis, investigation notes, evidence/uncertainty reports, "
                "or commentary about the request"
            ),
        )
    if _CODE_REQUEST.search(text):
        return DeliverableContract(
            kind="code",
            requested="code",
            must_produce="the requested code or patch",
            must_not_produce="a discussion of the request without the code",
        )
    if _DEEP_ANALYSIS.search(text) or _ANALYSIS_REQUEST.search(text) or _HIGH_STAKES.search(text):
        return DeliverableContract(
            kind="analysis",
            requested="analysis",
            must_produce="an analysis of the asked question",
            must_not_produce="an unrelated artifact",
        )
    if _CASUAL.search((objective or "").strip()):
        return DeliverableContract(
            kind="conversation",
            requested="conversational reply",
            must_produce="a direct conversational reply",
            must_not_produce="a research report or Evidence / Uncertainty / Inference sections",
        )
    if _FACTUAL.search(text):
        return DeliverableContract(
            kind="answer",
            requested="answer",
            must_produce="a concise direct answer",
            must_not_produce="an Evidence / Uncertainty / Inference report or investigation notes",
        )
    return DeliverableContract(
        kind="generic",
        requested="requested result",
        must_produce="the requested result",
        must_not_produce="an empty or unusable substitute",
    )


def has_quality_criteria(success_criteria: Iterable[str]) -> bool:
    return any(_QUALITY_CRITERION.search(item) for item in success_criteria)


def output_text(output: Any) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        for key in ("text", "content", "story", "body", "answer", "markdown"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return json.dumps(output, ensure_ascii=False, default=str)
    return str(output)


def classify_output(output: Any) -> ProducedKind:
    if output is None:
        return "empty"
    text = output_text(output).strip()
    if not text:
        return "empty"
    lowered = text.casefold()
    if _analysis_marker_hits(lowered) >= 2:
        return "analysis"
    if isinstance(output, dict):
        prose = next(
            (
                str(output[key])
                for key in ("text", "content", "story", "body", "answer", "markdown")
                if isinstance(output.get(key), str) and output[key].strip()
            ),
            "",
        )
        if not prose:
            return "structured"
        text = prose
        lowered = text.casefold()
        if _analysis_marker_hits(lowered) >= 2:
            return "analysis"
    code_hits = sum(1 for marker in _CODE_MARKERS if marker in text or marker in lowered)
    if code_hits >= 2 and len(text.splitlines()) >= 3:
        return "code"
    if _looks_like_narrative_body(text):
        return "narrative"
    return "prose"


def check_deliverable(contract: DeliverableContract, output: Any) -> tuple[bool, str, ProducedKind]:
    produced = classify_output(output)
    if contract.kind == "generic":
        return True, "generic task does not require a typed deliverable", produced
    if produced == "empty":
        return (
            False,
            f"missing {contract.requested}; {contract.must_produce}",
            produced,
        )
    if contract.kind == "narrative":
        if produced == "analysis":
            return (
                False,
                (
                    f"produced analysis/report instead of the requested {contract.requested}. "
                    f"Produce only {contract.must_produce}."
                ),
                produced,
            )
        if produced == "structured":
            return (
                False,
                (
                    f"produced a structured report instead of the requested {contract.requested}. "
                    f"Produce only {contract.must_produce}."
                ),
                produced,
            )
        if produced == "code":
            return (
                False,
                f"produced code instead of the requested {contract.requested}.",
                produced,
            )
        return True, f"output is a {contract.requested}", produced
    if contract.kind in {"answer", "conversation"}:
        if produced == "analysis":
            wanted = "a concise direct answer" if contract.kind == "answer" else "a direct conversational reply"
            return (
                False,
                (
                    f"produced an Evidence / Uncertainty / Inference report instead of {wanted}. "
                    f"Produce only {contract.must_produce}."
                ),
                produced,
            )
        if produced == "code":
            return (
                False,
                f"produced code instead of {contract.must_produce}.",
                produced,
            )
        return True, f"output is {contract.must_produce}", produced
    if contract.kind == "code" and produced == "analysis":
        return False, "produced analysis instead of the requested code", produced
    if contract.kind == "analysis" and produced == "empty":
        return False, "missing analysis", produced
    return True, "output matches the requested deliverable type", produced


def _looks_like_narrative_request(text: str) -> bool:
    if not _NARRATIVE_REQUEST.search(text):
        return False
    # Software "user story" / business "success story" are not fiction requests.
    stripped = _USER_STORY.sub(" ", text)
    stripped = _SUCCESS_STORY.sub(" ", stripped)
    return bool(_NARRATIVE_REQUEST.search(stripped))


def _requested_narrative_label(text: str) -> str:
    lowered = text.casefold()
    for label in (
        "short story",
        "story",
        "tale",
        "fable",
        "poem",
        "haiku",
        "letter",
        "email",
        "speech",
        "script",
        "joke",
    ):
        if label in lowered:
            return label
    return "narrative"


def _analysis_marker_hits(lowered: str) -> int:
    return sum(1 for marker in _ANALYSIS_MARKERS if marker in lowered)


def _looks_like_narrative_body(text: str) -> bool:
    words = text.split()
    if len(words) < 20:
        return False
    if _analysis_marker_hits(text.casefold()) >= 2:
        return False
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(paragraphs) >= 2:
        return True
    return text.count(".") >= 3 and not text.lstrip().startswith("#")

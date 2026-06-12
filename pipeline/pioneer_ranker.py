import json
import logging
import re
from typing import TYPE_CHECKING, Optional

import anthropic
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from pipeline.pioneer_finetuner import PioneerFinetuner

logger = logging.getLogger(__name__)

# ── LLM prompts (fallback path only) ────────────────────────────────────────

_SYSTEM_PROMPT = """You are a legal intelligence model trained to identify consumer complaints with class action lawsuit potential.
Return only valid JSON. No prose, no markdown."""

_USER_PROMPT = """Analyze this consumer complaint for class action lawsuit viability.
Post: {post_content}

Respond with JSON only:
{{"is_class_action_candidate": true/false, "class_action_score": 0.0-1.0,
  "confidence": 0.0-1.0, "legal_theory": "brief theory",
  "applicable_statutes": ["list"], "reasoning": "2-3 sentences",
  "existing_class_action": true/false, "pattern_strength": "weak|moderate|strong"}}"""


class LegalAssessment(BaseModel):
    is_class_action_candidate: bool
    class_action_score: float
    confidence: float
    legal_theory: str
    applicable_statutes: list[str]
    reasoning: str
    existing_class_action: bool
    pattern_strength: str


def _extract_json(text: str) -> str:
    text = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else text


_STATUTE_LABELS = {
    "tcpa_violation", "fcra_violation", "fdcpa_violation",
    "data_breach", "subscription_trap", "deceptive_advertising",
    "product_liability", "privacy_violation", "consumer_fraud",
}


def _assessment_from_encoder(resp: dict) -> LegalAssessment:
    """
    Parse Pioneer /inference response.
    Shape: {"result": {"data": {"legal": {"label": "...", "confidence": 0.97}}}}
    """
    data = resp.get("result", {}).get("data", {})
    task_result = data.get("legal") or data.get("legal_classification") or {}

    label = task_result.get("label", "not_actionable")
    confidence = float(task_result.get("confidence", 0.0))

    is_statute = label in _STATUTE_LABELS
    is_candidate = label == "class_action_candidate" or (is_statute and confidence >= 0.7)

    if confidence >= 0.8:
        pattern_strength = "strong"
    elif confidence >= 0.5:
        pattern_strength = "moderate"
    else:
        pattern_strength = "weak"

    return LegalAssessment(
        is_class_action_candidate=is_candidate,
        class_action_score=confidence if is_candidate else confidence * 0.5,
        confidence=confidence,
        legal_theory=label.replace("_", " "),
        applicable_statutes=[label] if is_statute else [],
        reasoning="",
        existing_class_action=False,
        pattern_strength=pattern_strength,
    )


class PioneerRanker:
    def __init__(
        self,
        db_client,
        anthropic_client: anthropic.Anthropic,
        model: str = "claude-opus-4-8",
        finetuner: Optional["PioneerFinetuner"] = None,
        encoder_model_id: Optional[str] = None,
    ):
        self.db = db_client
        self.client = anthropic_client
        self.model = model
        self.finetuner = finetuner
        self.encoder_model_id = encoder_model_id

    def fetch_unranked_posts(self, limit: Optional[int] = None) -> list[dict]:
        limit_clause = f"LIMIT {limit}" if limit else ""
        result = self.db.query(
            f"""
            SELECT post_id, post, signal_score, complaint_type, taxonomy
            FROM seconds.posts FINAL
            WHERE pioneer_score IS NULL
              AND post IS NOT NULL
              AND length(post) > 50
            ORDER BY signal_score DESC
            {limit_clause}
            """
        )
        return [
            {
                "post_id": row[0],
                "post": row[1],
                "signal_score": row[2],
                "complaint_type": row[3],
                "taxonomy": row[4],
            }
            for row in result.result_rows
        ]

    def rank_post_encoder(self, post: dict) -> tuple[Optional[LegalAssessment], Optional[str]]:
        if not self.finetuner or not self.encoder_model_id:
            raise RuntimeError("encoder_model_id and finetuner required")
        try:
            resp = self.finetuner.classify(self.encoder_model_id, post.get("post") or "")
            return _assessment_from_encoder(resp), json.dumps(resp)
        except Exception as e:
            logger.error("encoder failed for %s: %s", post["post_id"], e)
            return None, None

    def rank_post(self, post: dict) -> tuple[Optional[LegalAssessment], Optional[str]]:
        prompt = _USER_PROMPT.format(post_content=(post.get("post") or "")[:3000])
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                thinking={"type": "adaptive"},
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = next((b.text for b in response.content if b.type == "text"), "")
            assessment = LegalAssessment(**json.loads(_extract_json(raw)))
            return assessment, raw
        except (json.JSONDecodeError, ValidationError) as e:
            logger.error("parse error for %s: %s", post["post_id"], e)
            return None, None
        except Exception as e:
            logger.error("llm failed for %s: %s", post["post_id"], e)
            return None, None

    def save_ranking(self, post_id: str, assessment: LegalAssessment) -> None:
        model_id = self.encoder_model_id or self.model
        self.db.insert(
            "rankings",
            data=[[post_id, assessment.class_action_score, assessment.legal_theory, model_id]],
            column_names=["post_id", "pioneer_score", "pioneer_label", "pioneer_model"],
        )

    def run(self, batch_size: Optional[int] = None) -> list[dict]:
        """
        Rank all unranked posts (or up to batch_size).
        Returns list of result dicts sorted by score desc, also written to ClickHouse.
        """
        posts = self.fetch_unranked_posts(limit=batch_size)
        if not posts:
            logger.info("No unranked posts.")
            return []

        use_encoder = bool(self.encoder_model_id and self.finetuner)
        mode = f"encoder:{self.encoder_model_id}" if use_encoder else f"llm:{self.model}"
        logger.info("Ranking %d posts via %s...", len(posts), mode)

        results = []
        for post in posts:
            assessment, raw = (
                self.rank_post_encoder(post) if use_encoder else self.rank_post(post)
            )
            if assessment:
                try:
                    self.save_ranking(post["post_id"], assessment)
                    results.append({
                        "post_id": post["post_id"],
                        "pioneer_score": round(assessment.class_action_score, 4),
                        "pioneer_label": assessment.legal_theory,
                        "confidence": round(assessment.confidence, 4),
                        "is_class_action_candidate": assessment.is_class_action_candidate,
                        "pattern_strength": assessment.pattern_strength,
                        "signal_score": post.get("signal_score"),
                        "complaint_type": post.get("complaint_type"),
                        "taxonomy": post.get("taxonomy"),
                        "post": post["post"],
                    })
                    logger.info(
                        "post_id=%-20s score=%.2f label=%-25s candidate=%s",
                        post["post_id"],
                        assessment.class_action_score,
                        assessment.legal_theory,
                        assessment.is_class_action_candidate,
                    )
                except Exception as e:
                    logger.error("save failed for %s: %s", post["post_id"], e)
            else:
                logger.warning("skipped %s (inference error)", post["post_id"])

        results.sort(key=lambda r: r["pioneer_score"], reverse=True)
        return results

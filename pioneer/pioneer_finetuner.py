import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

PIONEER_BASE_URL = "https://api.pioneer.ai"
BASE_MODEL = "fastino/gliner2-base-v1"

# Label vocabulary for the fine-tuned classifier
LEGAL_LABELS = [
    "class_action_candidate",
    "tcpa_violation",       # robocalls / spam texts
    "fcra_violation",       # credit reporting
    "fdcpa_violation",      # debt collection
    "data_breach",
    "subscription_trap",
    "deceptive_advertising",
    "product_liability",
    "privacy_violation",
    "consumer_fraud",
    "not_actionable",
]

STATUTE_LABELS = {
    "tcpa_violation",
    "fcra_violation",
    "fdcpa_violation",
    "data_breach",
    "subscription_trap",
    "deceptive_advertising",
    "product_liability",
    "privacy_violation",
    "consumer_fraud",
}

_DOMAIN_DESCRIPTION = (
    "Consumer complaints on Reddit about companies violating consumer rights: "
    "unwanted robocalls, credit reporting errors, abusive debt collectors, "
    "data breaches, hidden subscription charges, deceptive advertising, "
    "defective products, and privacy violations."
)


class PioneerFinetuner:
    """
    Manages GLiNER encoder fine-tuning and inference via Pioneer's native API.

    Two training paths:
      - generate_training_data(): Pioneer synthesises labelled examples (no DB needed)
      - auto_label_posts():       Pioneer labels your real ClickHouse posts

    After run_finetune() completes, save the returned job ID as
    PIONEER_MODEL_ID in .env and pass it to classify() for inference.
    """

    def __init__(self, api_key: str, db_client=None):
        self.api_key = api_key
        self.db = db_client
        self._headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        }

    # ── HTTP helpers ────────────────────────────────────────────────────────

    def _get(self, path: str) -> dict:
        r = httpx.get(f"{PIONEER_BASE_URL}{path}", headers=self._headers, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = httpx.post(
            f"{PIONEER_BASE_URL}{path}",
            headers=self._headers,
            json=body,
            timeout=120,
        )
        r.raise_for_status()
        return r.json()

    # ── Step 1a: synthetic training data ───────────────────────────────────

    def generate_training_data(
        self,
        dataset_name: str,
        num_examples: int = 300,
    ) -> None:
        """Ask Pioneer to synthesise labelled training examples (async — poll after)."""
        logger.info("Generating %d synthetic examples → dataset '%s'", num_examples, dataset_name)
        resp = self._post("/generate", {
            "task_type": "classification",
            "dataset_name": dataset_name,
            "labels": LEGAL_LABELS,
            "num_examples": num_examples,
            "domain_description": _DOMAIN_DESCRIPTION,
            "multi_label": True,
        })
        logger.info("Generate queued: %s", resp)

    # ── Step 1b: auto-label real posts ─────────────────────────────────────

    def auto_label_posts(
        self,
        batch_size: int = 500,
    ) -> list[dict]:
        """
        Fetch posts from ClickHouse and auto-label them synchronously.
        Returns [{text, labels}, ...] — hand the result to a dataset upload
        or print it for review before training.
        """
        if self.db is None:
            raise RuntimeError("db_client required for auto_label_posts")

        result = self.db.query(
            "SELECT post FROM posts WHERE post IS NOT NULL AND length(post) > 50 LIMIT {n:UInt32}",
            parameters={"n": batch_size},
        )
        texts = [row[0][:1000] for row in result.result_rows]
        if not texts:
            raise RuntimeError("No posts found in ClickHouse")

        logger.info("Auto-labeling %d posts...", len(texts))
        labeled = []
        chunk_size = 500  # Pioneer max per call
        for i in range(0, len(texts), chunk_size):
            chunk = texts[i: i + chunk_size]
            resp = self._post("/generate/classification/label-existing", {
                "labels": LEGAL_LABELS,
                "inputs": chunk,
            })
            rows = resp if isinstance(resp, list) else resp.get("results", [])
            labeled.extend(rows)

        logger.info("Auto-labeling complete: %d rows", len(labeled))
        return labeled

    # ── Step 2: wait for dataset ────────────────────────────────────────────

    def wait_for_dataset(
        self,
        dataset_name: str,
        poll_interval: int = 10,
        timeout: int = 600,
    ) -> None:
        logger.info("Waiting for dataset '%s' to be ready...", dataset_name)
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = self._get(f"/felix/datasets/{dataset_name}")
            # Response: {"success": true, "versions": [{"status": "ready", ...}]}
            versions = resp.get("versions") or []
            status = (versions[0].get("status") if versions else None) or "pending"
            logger.info("Dataset '%s': %s", dataset_name, status)
            if status == "ready":
                return
            if status in ("failed", "error"):
                raise RuntimeError(f"Dataset failed: {resp}")
            time.sleep(poll_interval)
        raise TimeoutError(f"Dataset '{dataset_name}' not ready after {timeout}s")

    # ── Step 3: submit training ─────────────────────────────────────────────

    def start_training(
        self,
        model_name: str,
        dataset_name: str,
        nr_epochs: int = 5,
        learning_rate: float = 5e-5,
    ) -> str:
        """Submit LoRA fine-tuning job. Returns job ID (= model ID for inference)."""
        logger.info("Starting training: model='%s', dataset='%s'", model_name, dataset_name)
        resp = self._post("/felix/training-jobs", {
            "model_name": model_name,
            "base_model": BASE_MODEL,
            "datasets": [{"name": dataset_name}],
            "training_type": "lora",
            "nr_epochs": nr_epochs,
            "learning_rate": learning_rate,
        })
        job_id = resp["id"]
        logger.info("Job %s started (status=%s)", job_id, resp.get("status"))
        return job_id

    # ── Step 4: poll training ───────────────────────────────────────────────

    def wait_for_training(
        self,
        job_id: str,
        poll_interval: int = 30,
        timeout: int = 7200,
    ) -> dict:
        logger.info("Polling training job %s...", job_id)
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = self._get(f"/felix/training-jobs/{job_id}")
            status = resp.get("status", "unknown")
            logger.info("Job %s: %s", job_id, status)
            if status == "complete":
                m = resp.get("metrics", {})
                logger.info(
                    "Training complete. F1=%.3f  Precision=%.3f  Recall=%.3f",
                    m.get("f1", 0), m.get("precision", 0), m.get("recall", 0),
                )
                return resp
            if status in ("failed", "stopped"):
                raise RuntimeError(f"Job {job_id} {status}: {resp}")
            time.sleep(poll_interval)
        raise TimeoutError(f"Training did not complete after {timeout}s")

    # ── Inference ───────────────────────────────────────────────────────────

    def classify(self, model_id: str, text: str) -> dict:
        """Run inference with the fine-tuned GLiNER model. Returns raw Pioneer response."""
        return self._post("/inference", {
            "model_id": model_id,
            "text": text[:2000],
            "schema": {
                "classifications": [{
                    "task": "legal",
                    "labels": LEGAL_LABELS,
                    "multi_label": False,
                }]
            },
            "threshold": 0.0,
        })

    # ── End-to-end orchestration ────────────────────────────────────────────

    def run_finetune(
        self,
        model_name: str = "seconds-ai-legal-classifier",
        dataset_name: str = "seconds-ai-legal-data",
        num_examples: int = 300,
    ) -> str:
        """
        Generate synthetic data → wait for dataset → train → return job ID.
        To use real posts instead of synthetic data, call:
            auto_label_posts() then start_training() manually.
        """
        self.generate_training_data(dataset_name, num_examples=num_examples)
        self.wait_for_dataset(dataset_name)
        job_id = self.start_training(model_name, dataset_name)
        self.wait_for_training(job_id)
        logger.info("Set PIONEER_MODEL_ID=%s in .env to use this model.", job_id)
        return job_id

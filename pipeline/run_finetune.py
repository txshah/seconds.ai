"""
One-time fine-tuning step.
"""

#   curl -s "https://api.pioneer.ai/felix/datasets/3335e516-8508-4d82-a050-36ac48fad9af/1/download" \
    # -H "X-API-Key: $PIONEER_KEY"

import logging
import os

from dotenv import load_dotenv

from pipeline.pioneer_finetuner import PioneerFinetuner


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    use_real_posts = os.environ.get("USE_REAL_POSTS", "").strip() in ("1", "true", "yes")

    if use_real_posts:
        from pipeline.db import get_client
        db = get_client()
    else:
        db = None

    finetuner = PioneerFinetuner(api_key=os.environ["PIONEER_KEY"], db_client=db)

    task_model = "LawClassActionClassifier"
    dataset_name = "seconds-ai-legal-data"

    if use_real_posts:
        logging.info("Auto-labeling real posts from ClickHouse...")
        labeled = finetuner.auto_label_posts(batch_size=500)
        logging.info("Got %d labeled rows. Starting training...", len(labeled))
        # Note: Pioneer requires the labeled data to be uploaded as a dataset first.
        # If the auto-label endpoint returns a dataset_name, pass it to start_training.
        # Otherwise, upload labeled rows via the Pioneer dashboard and then call:
        #   job_id = finetuner.start_training(task_model, "<dataset-name>")
        #   finetuner.wait_for_training(job_id)
        raise NotImplementedError(
            "Upload the auto-labeled data via the Pioneer dashboard, then "
            "call finetuner.start_training() with the dataset name."
        )
    else:
        job_id = finetuner.run_finetune(
            model_name=task_model,
            dataset_name=dataset_name,
            num_examples=300,
        )

    print(f"\nAdapter trained for {task_model}. Job ID: {job_id}")
    print(f"Inference already routes to {task_model} — PIONEER_MODEL_ID is already set in .env")


if __name__ == "__main__":
    main()

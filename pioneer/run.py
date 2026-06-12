import json
import logging
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from pioneer.db import get_client
from pioneer.pioneer_finetuner import PioneerFinetuner
from pioneer.pioneer_ranker import PioneerRanker

OUTPUT_FILE = Path(__file__).parent.parent / "rankings_output.json"


def main() -> None:
    load_dotenv(Path(__file__).parent.parent / ".env")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    db = get_client()
    pioneer_key = os.environ["PIONEER_KEY"]
    encoder_model_id = os.environ.get("PIONEER_MODEL_ID")

    if encoder_model_id:
        finetuner = PioneerFinetuner(api_key=pioneer_key)
        pioneer_client = anthropic.Anthropic(api_key="unused")
        logging.info("Using GLiNER encoder model %s", encoder_model_id)
    else:
        finetuner = None
        pioneer_client = anthropic.Anthropic(
            base_url="https://api.pioneer.ai/v1",
            api_key=pioneer_key,
        )
        logging.info("Using LLM model %s", os.environ.get("PIONEER_MODEL", "claude-opus-4-8"))

    ranker = PioneerRanker(
        db_client=db,
        anthropic_client=pioneer_client,
        model=os.environ.get("PIONEER_MODEL", "claude-opus-4-8"),
        finetuner=finetuner,
        encoder_model_id=encoder_model_id,
    )

    results = ranker.run()  # all unranked posts

    OUTPUT_FILE.write_text(json.dumps(results, indent=2))
    logging.info(
        "Done. %d posts ranked. Output → %s",
        len(results), OUTPUT_FILE,
    )
    print(f"\nTop 5:")
    for r in results[:5]:
        print(f"  {r['post_id']:25s}  score={r['pioneer_score']:.3f}  label={r['pioneer_label']}")


if __name__ == "__main__":
    main()

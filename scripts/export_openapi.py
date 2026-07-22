"""Export the committed OpenAPI contract from the FastAPI application."""

import json
from pathlib import Path

from commercevision_api.main import app


def main() -> None:
    target = Path("docs/api/openapi.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(target)


if __name__ == "__main__":
    main()

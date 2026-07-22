import json
from pathlib import Path

from jobos_api.app import create_app
from jobos_api.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "packages" / "contracts" / "openapi.json"


def main() -> None:
    app = create_app(
        Settings(
            device_token="contract-generation-only",
            mcp_token="contract-mcp-generation-only",
            state_db_path=ROOT / ".contract-generation.db",
        )
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

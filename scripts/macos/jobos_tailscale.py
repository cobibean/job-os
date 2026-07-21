from __future__ import annotations

import sys
from pathlib import Path

api_source = Path(__file__).resolve().parents[2] / "services/api"
sys.path.insert(0, str(api_source))

from jobos_api.tailscale_adapter import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

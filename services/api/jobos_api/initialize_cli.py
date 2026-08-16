from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jobos_api.initialize import initialize_jobos
from jobos_api.local_config import LocalConfigError, default_data_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize a local JobOS profile")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--config-path", type=Path)
    parser.add_argument("--no-demo", action="store_true")
    parser.add_argument("--reset-demo", action="store_true")
    parser.add_argument("--confirm-reset-demo", action="store_true")
    parser.add_argument("--state-db", type=Path)
    parser.add_argument("--jobs-db", type=Path)
    parser.add_argument("--artifacts-dir", type=Path)
    parser.add_argument("--logs-dir", type=Path)
    parser.add_argument("--credentials-dir", type=Path)
    args = parser.parse_args()
    if args.reset_demo and not args.confirm_reset_demo:
        parser.error("--reset-demo requires --confirm-reset-demo")
    try:
        result = initialize_jobos(
            args.data_dir,
            config_path_override=args.config_path,
            demo_enabled=not args.no_demo,
            reset_demo_requested=args.reset_demo,
            reset_confirmed=args.confirm_reset_demo,
            state_db_path=args.state_db,
            jobs_db_path=args.jobs_db,
            artifacts_path=args.artifacts_dir,
            logs_path=args.logs_dir,
            credentials_path=args.credentials_dir,
        )
    except (LocalConfigError, OSError, RuntimeError, ValueError) as error:
        print(f"JobOS setup failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result.safe_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import sys

from . import store
from .pipeline import run_job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    args = parser.parse_args(argv)
    store.init_db()
    return run_job(args.job_id)


if __name__ == "__main__":
    sys.exit(main())


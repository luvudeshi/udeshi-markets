"""Prints the API's OpenAPI schema as JSON. The frontend's TypeScript types are generated from
it, and CI checks that they are up to date:

    uv run python -m udeshi_api.openapi > frontend/openapi.json
    (cd frontend && npm run generate:api)
"""

import json
import sys

from udeshi_api.main import create_app
from udeshi_api.settings import Settings


def main() -> None:
    # Default settings, not the environment's: the schema does not depend on them, and a
    # configured database (perhaps unreachable from here) must not be touched.
    json.dump(create_app(Settings()).openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

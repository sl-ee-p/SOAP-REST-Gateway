"""Manual smoke test: call the live SOAP service through the gateway.

Usage:  python scripts/smoke_soap.py IT ZZ
"""

import sys

from app.config import Settings
from app.domain.errors import DomainError
from app.logging_config import configure_logging
from app.soap.gateway import build_gateway


def main(codes: list[str]) -> int:
    configure_logging("INFO")
    # API keys are irrelevant here; pass a dummy so no .env file is required.
    gateway = build_gateway(Settings(api_keys=["unused"]))
    print(f"continents: {[c.code for c in gateway.list_continents()]}")
    print(f"countries:  {len(gateway.list_countries())}")
    print(f"currencies: {len(gateway.list_currencies())}")
    exit_code = 0
    for code in codes or ["IT"]:
        try:
            print(gateway.get_country(code).model_dump_json(indent=2))
        except DomainError as exc:
            print(f"{code}: {exc.code} - {exc.detail}")
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

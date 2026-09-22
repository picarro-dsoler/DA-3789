#!/usr/bin/env python3
"""Daily Veho GeoPackage export for Northern Gas Networks and SGN."""

from __future__ import annotations

import argparse
from datetime import datetime

import bootstrap_path

bootstrap_path.activate()

from export_veho_geopackage import export_veho_geopackage, parse_process_date

CUSTOMERS = (
    'Northern Gas Networks',
    'SGN',
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Run Veho GeoPackage export for Northern Gas Networks then SGN.'
    )
    parser.add_argument(
        'process_date',
        nargs='?',
        default=None,
        help='Process datetime (default: now). Formats: YYYY-MM-DD[THH:MM[:SS]]',
    )
    parser.add_argument(
        '--no-send-email',
        action='store_true',
        help='Skip emailing GeoPackages (email is sent by default)',
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    process_date = parse_process_date(args.process_date)
    send_email = not args.no_send_email

    exit_code = 0
    for customer_name in CUSTOMERS:
        print(f'=== Exporting {customer_name} @ {process_date.isoformat()} ===')
        result = export_veho_geopackage(
            customer_name=customer_name,
            process_date=process_date,
            send_email=send_email,
        )
        if result is None:
            exit_code = 1

    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())

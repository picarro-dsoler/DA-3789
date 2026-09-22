#!/usr/bin/env python3
"""Export Veho survey layers to a GeoPackage for a customer."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import bootstrap_path

SCRIPT_ROOT = bootstrap_path.activate()

import geopandas as gpd
import pandas as pd

from locallib.pandas import *  # noqa: F401,F403
from locallib.picarrodb import *  # noqa: F401,F403
from locallib.query import survey_query, query_segments_table

from geopackage import write_veho_geopackage
from msapi import send_email_via_outlook_api
from query import get_reports_veho
from utils import camel_to_snake

REPORT_COLS = [
    'ReportId',
    'ReportName',
    'ReportTitle',
    'ReportDate',
    'BoundaryName',
    'BoundaryType',
    'DistributionPipePercentCovered',
    'Labels',
]
DEFAULT_RECIPIENTS = [
    'dsoler@picarro.com',
    'Theo@veho-solutions.co.uk',
]
LOOKBACK_HOURS = 6


def parse_process_date(value: str | None) -> datetime:
    if value is None:
        return datetime.now()
    for fmt in (
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%dT%H:%M',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d',
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f'Invalid process_date {value!r}. Use e.g. 2026-09-21T09:00:00'
    )


def processed_reports_csv_path(short_name: str, base_dir: Path | None = None) -> Path:
    return Path(base_dir or SCRIPT_ROOT) / f'{short_name}_reports_processed.csv'


def load_reports_processed(csv_path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(csv_path)
    except FileNotFoundError:
        return pd.DataFrame(columns=REPORT_COLS)


def build_veho_layers(
    conn,
    customer_name: str,
    process_date: datetime,
    reports_processed: pd.DataFrame,
):
    delta_time = process_date - timedelta(hours=LOOKBACK_HOURS)
    reports = get_reports_veho(customer_name=customer_name).execute(conn)[REPORT_COLS]
    report_dates = pd.to_datetime(reports['ReportDate'])
    # Only reports in the lookback window ending at process_date
    reports = reports[(report_dates >= delta_time) & (report_dates <= process_date)]
    reports = reports[~reports['ReportId'].isin(reports_processed['ReportId'])]
    if reports.empty:
        return None

    reports_veho = reports.copy()
    reports_veho.rename(
        columns={
            'DistributionPipePercentCovered': 'DistmainsCoveragePct',
            'BoundaryName': 'BoundaryId',
        },
        inplace=True,
    )
    reports_veho.columns = [camel_to_snake(col) for col in reports_veho.columns]

    reports.db.set_query(survey_query(report_table='#TempReport'))
    surveys = reports.db.execute(conn, source_col='ReportId', temp_table_name='#TempReport')
    surveys_veho = surveys[
        [
            'SurveyId',
            'ReportId',
            'Tag',
            'SurveyorUnit',
            'AnalyzerSerialNumber',
            'StartDateTimeSurvey',
            'EndDateTimeSurvey',
            'DurationMinutes',
        ]
    ].copy()
    surveys_veho.rename(
        columns={
            'SurveyId': 'SessionId',
            'AnalyzerSerialNumber': 'AnalyserSerial',
            'StartDateTimeSurvey': 'StartUtc',
            'EndDateTimeSurvey': 'EndUtc',
            'DurationMinutes': 'DurationMin',
            'Tag': 'SurveyTag',
        },
        inplace=True,
    )
    surveys_veho['StartUtc'] = pd.to_datetime(surveys_veho['StartUtc'])
    surveys_veho['EndUtc'] = pd.to_datetime(surveys_veho['EndUtc'])
    surveys_veho['DurationMin'] = surveys_veho['DurationMin'].astype(int)
    surveys_veho.columns = [camel_to_snake(col) for col in surveys_veho.columns]

    surveys.db.set_query(query_segments_table(survey_table='#TempSurvey'))
    segments = surveys.db.execute(conn, source_col='SurveyId', temp_table_name='#TempSurvey')
    segments_veho = pd.merge(
        surveys[['SurveyId', 'ReportId']],
        segments[
            [
                'SurveyId',
                'Id',
                'Shape',
                'StartEpoch',
                'EndEpoch',
                'DurationSeconds',
                'LengthMeters',
                'CarSpeedMedian',
            ]
        ],
        on='SurveyId',
    )
    segments_veho.rename(
        columns={
            'SurveyId': 'SessionId',
            'Id': 'SegmentId',
            'Shape': 'Geometry',
            'DurationSeconds': 'DurationS',
            'LengthMeters': 'LengthM',
            'CarSpeedMedian': 'CarSpeedMed',
        },
        inplace=True,
    )
    segments_veho.columns = [camel_to_snake(col) for col in segments_veho.columns]

    segments_veho = gpd.GeoDataFrame(
        segments_veho,
        geometry=gpd.GeoSeries.from_wkt(segments_veho['geometry']),
        crs='EPSG:4326',
    ).to_crs(epsg=27700)

    fov_query = """
        SELECT SurveyId as SurveyId, FieldOfView.STAsText() as FieldOfView
        FROM SurveyResult SR
        WHERE SR.SurveyId IN (SELECT SurveyId FROM #TempSurvey)
    """
    surveys.db.set_query(fov_query)
    fov = pd.merge(
        surveys[['SurveyId', 'ReportId']],
        surveys.db.execute(conn, source_col='SurveyId', temp_table_name='#TempSurvey')[
            ['SurveyId', 'FieldOfView']
        ],
        on='SurveyId',
    )
    fov_veho = fov.copy()
    fov_veho.rename(
        columns={'SurveyId': 'SessionId', 'FieldOfView': 'geometry'},
        inplace=True,
    )
    fov_veho.columns = [camel_to_snake(col) for col in fov_veho.columns]
    fov_veho = gpd.GeoDataFrame(
        fov_veho,
        geometry=gpd.GeoSeries.from_wkt(fov_veho['geometry']),
        crs='EPSG:4326',
    ).to_crs(epsg=27700)

    return reports, reports_veho, surveys_veho, segments_veho, fov_veho


def export_veho_geopackage(
    customer_name: str,
    process_date: datetime | None = None,
    *,
    conn=None,
    output_dir: Path | None = None,
    send_email: bool = True,
    recipients: list[str] | None = None,
) -> Path | None:
    process_date = process_date or datetime.now()
    conn = conn or EU1_Conn
    output_dir = Path(output_dir or SCRIPT_ROOT / 'output')
    output_dir.mkdir(parents=True, exist_ok=True)

    short_name = customer_name.replace(' ', '')
    csv_path = processed_reports_csv_path(short_name)
    reports_processed = load_reports_processed(csv_path)

    layers = build_veho_layers(conn, customer_name, process_date, reports_processed)
    if layers is None:
        print(
            f'No new reports for {customer_name!r} since '
            f'{(process_date - timedelta(hours=LOOKBACK_HOURS)).isoformat()} '
            f'(already processed: {len(reports_processed)})'
        )
        return None

    reports, reports_veho, surveys_veho, segments_veho, fov_veho = layers
    output_gpkg = output_dir / (
        f'{short_name}{str(process_date.year)[-2:]}_survey_{process_date.date().isoformat()}.gpkg'
    )
    write_veho_geopackage(
        output_gpkg,
        reports_veho,
        surveys_veho,
        segments_veho,
        fov_veho,
    )
    print(f'GeoPackage written to {output_gpkg.resolve()}')

    reports_processed = pd.concat([reports_processed, reports], ignore_index=True)
    reports_processed.to_csv(csv_path, index=False)
    print(f'Processed reports saved to {csv_path.resolve()}')

    if send_email:
        for rcpt in recipients or DEFAULT_RECIPIENTS:
            try:
                send_email_via_outlook_api(
                    subject=f'{customer_name} - GeoPackage {output_gpkg.name}',
                    body=f'Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}.',
                    recipient=rcpt,
                    attachments=[output_gpkg],
                )
                print(f'Email sent successfully to {rcpt}')
            except Exception as exc:
                print(f'Failed to send email to {rcpt}: {exc}')

    return output_gpkg


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Export Veho GeoPackage layers for a customer.'
    )
    parser.add_argument(
        'customer_name',
        help='Customer name, e.g. "Northern Gas Networks"',
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
        help='Skip emailing the GeoPackage (email is sent by default)',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=None,
        help='Directory for the output GeoPackage (default: ./output)',
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    process_date = parse_process_date(args.process_date)
    result = export_veho_geopackage(
        customer_name=args.customer_name,
        process_date=process_date,
        output_dir=args.output_dir,
        send_email=not args.no_send_email,
    )
    return 0 if result is not None else 1


if __name__ == '__main__':
    raise SystemExit(main())

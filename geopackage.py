from pathlib import Path
import sqlite3

import geopandas as gpd
import pandas as pd


def _ensure_string_ids(df, id_cols):
    df = df.copy()
    for col in id_cols:
        df[col] = df[col].astype(str)
    return df


def _prepare_for_write(df):
    rows = df.copy()
    for col in rows.columns:
        if pd.api.types.is_datetime64_any_dtype(rows[col]):
            rows[col] = rows[col].dt.strftime('%Y-%m-%d %H:%M:%S')
    return rows


LAYER_ORDER = ['report_area', 'driving_session', 'breadcrumb', 'fov']


def _as_feature_layer(df, crs='EPSG:27700'):
    return gpd.GeoDataFrame(df, geometry=[None] * len(df), crs=crs)


def _reorder_gpkg_layers(gpkg_path, layer_order):
    conn = sqlite3.connect(gpkg_path)
    rows_by_name = {
        row[0]: row
        for row in conn.execute(
            """
            SELECT table_name, data_type, identifier, description,
                   last_change, min_x, min_y, max_x, max_y, srs_id
            FROM gpkg_contents
            """
        )
    }

    conn.execute('DELETE FROM gpkg_contents')
    for table_name in layer_order:
        conn.execute(
            """
            INSERT INTO gpkg_contents (
                table_name, data_type, identifier, description,
                last_change, min_x, min_y, max_x, max_y, srs_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows_by_name[table_name],
        )

    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gpkg_ogr_contents'"
    ).fetchone():
        conn.execute('DELETE FROM gpkg_ogr_contents')
        for table_name in layer_order:
            feature_count = conn.execute(
                f'SELECT COUNT(*) FROM "{table_name}"'
            ).fetchone()[0]
            conn.execute(
                'INSERT INTO gpkg_ogr_contents (table_name, feature_count) VALUES (?, ?)',
                (table_name, feature_count),
            )

    conn.commit()
    conn.close()


def _apply_constraints(gpkg_path, table_specs):
    conn = sqlite3.connect(gpkg_path)
    conn.execute('PRAGMA foreign_keys = OFF')

    for table, spec in table_specs.items():
        pk_col = spec['pk']
        fks = spec.get('fks', [])
        drop_fid = spec.get('drop_fid', False)
        cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()

        col_defs = []
        select_cols = []
        for _, name, ctype, _, _, _ in cols:
            if drop_fid and name == 'fid':
                continue
            select_cols.append(f'"{name}"')
            if name == pk_col:
                col_defs.append(f'"{name}" TEXT PRIMARY KEY NOT NULL')
            else:
                col_defs.append(f'"{name}" {ctype}')

        for col, ref_table, ref_col in fks:
            col_defs.append(
                f'FOREIGN KEY ("{col}") REFERENCES "{ref_table}" ("{ref_col}")'
            )

        temp_table = f'{table}__new'
        conn.execute(f'DROP TABLE IF EXISTS "{temp_table}"')
        conn.execute(f'CREATE TABLE "{temp_table}" ({", ".join(col_defs)})')
        conn.execute(
            f'INSERT INTO "{temp_table}" SELECT {", ".join(select_cols)} FROM "{table}"'
        )
        conn.execute(f'DROP TABLE "{table}"')
        conn.execute(f'ALTER TABLE "{temp_table}" RENAME TO "{table}"')

    conn.execute('PRAGMA foreign_keys = ON')
    conn.commit()
    conn.close()


def write_veho_geopackage(
    gpkg_path,
    report_veho,
    survey_veho,
    segment_veho,
    fov_veho,
):
    gpkg_path = Path(gpkg_path)
    if gpkg_path.exists():
        gpkg_path.unlink()

    report_gdf = _as_feature_layer(
        _prepare_for_write(_ensure_string_ids(report_veho, ['report_id']))
    )
    survey_gdf = _as_feature_layer(
        _prepare_for_write(
            _ensure_string_ids(survey_veho, ['session_id', 'report_id'])
        )
    )
    segment_gdf = _ensure_string_ids(
        segment_veho, ['segment_id', 'session_id', 'report_id']
    )
    fov_gdf = _ensure_string_ids(fov_veho, ['session_id', 'report_id'])

    report_gdf.to_file(
        gpkg_path,
        layer='report_area',
        driver='GPKG',
        engine='pyogrio',
        SPATIAL_INDEX='NO',
    )
    survey_gdf.to_file(
        gpkg_path,
        layer='driving_session',
        driver='GPKG',
        engine='pyogrio',
        mode='a',
        SPATIAL_INDEX='NO',
    )
    segment_gdf.to_file(
        gpkg_path,
        layer='breadcrumb',
        driver='GPKG',
        engine='pyogrio',
        mode='a',
        SPATIAL_INDEX='NO',
    )
    fov_gdf.to_file(
        gpkg_path,
        layer='fov',
        driver='GPKG',
        engine='pyogrio',
        mode='a',
        SPATIAL_INDEX='NO',
    )

    _apply_constraints(
        gpkg_path,
        {
            'report_area': {
                'pk': 'report_id',
                'drop_fid': True,
            },
            'driving_session': {
                'pk': 'session_id',
                'drop_fid': True,
                'fks': [('report_id', 'report_area', 'report_id')],
            },
            'breadcrumb': {
                'pk': 'segment_id',
                'drop_fid': True,
                'fks': [
                    ('session_id', 'driving_session', 'session_id'),
                    ('report_id', 'report_area', 'report_id'),
                ],
            },
            'fov': {
                'pk': 'session_id',
                'drop_fid': True,
                'fks': [
                    ('session_id', 'driving_session', 'session_id'),
                    ('report_id', 'report_area', 'report_id'),
                ],
            },
        },
    )
    _reorder_gpkg_layers(gpkg_path, LAYER_ORDER)

    return gpkg_path

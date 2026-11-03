import os
import sys
from pathlib import Path
import pandas as pd
from sqlalchemy import text

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.db import get_owner_engine, SCHEMA

data_path = project_root / "data" / "raw" / "dynamic_supply_chain_logistics_dataset.csv"

agent_user = os.getenv("AGENT_DB_USER", "agent_ro")
agent_password = os.getenv("AGENT_DB_PASSWORD")
if not agent_password:
    raise ValueError("Missing AGENT_DB_PASSWORD in .env")

# 1. Load the raw dataset
print(f"Loading CSV from {data_path}...")
df = pd.read_csv(data_path)

# 2. Map clean columns to a messy legacy-style schema
legacy_mapping = {
    'timestamp': 'TS_UTC',
    'vehicle_gps_latitude': 'V_LAT',
    'vehicle_gps_longitude': 'V_LON',
    'iot_temperature': 'IOT_TEMP_VAL_C',
    'cargo_condition_status': 'CGO_COND_CD',
    'risk_classification': 'RISK_CLS_TXT',
    'delay_probability': 'DELAY_PROB_DEC',
    'port_congestion_level': 'PRT_CNG_LVL',
    'route_risk_level': 'RT_RSK_IDX'
}

df_legacy = df[list(legacy_mapping.keys())].rename(columns=legacy_mapping)
df_legacy['SYS_INGEST_FLAG'] = 'Y'
df_legacy['TS_UTC'] = pd.to_datetime(df_legacy['TS_UTC'])

engine = get_owner_engine()

# 3. Raw legacy table (owner-only)
print(f"Writing {len(df_legacy)} rows into public.tbl_sc_fleet_hist_raw...")
df_legacy.columns = [c.lower() for c in df_legacy.columns]
df_legacy.to_sql('tbl_sc_fleet_hist_raw', engine, if_exists='replace', index=False,
                 schema='public', chunksize=5000, method='multi')

# 4. Schema, semantic view, audit table, and the read-only agent role
with engine.begin() as conn:
    conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    conn.execute(text(f"""
        CREATE OR REPLACE VIEW {SCHEMA}.vw_active_fleet AS
        SELECT
            ts_utc AS timestamp,
            v_lat AS latitude,
            v_lon AS longitude,
            CAST(iot_temp_val_c AS DOUBLE PRECISION) AS current_temperature_c,
            cgo_cond_cd AS cargo_condition_code,
            risk_cls_txt AS risk_classification,
            delay_prob_dec AS delay_probability,
            prt_cng_lvl AS port_congestion_level,
            rt_rsk_idx AS route_risk_index
        FROM public.tbl_sc_fleet_hist_raw
    """))
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.agent_audit_log (
            log_id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            timestamp TIMESTAMPTZ DEFAULT now(),
            session_id TEXT,
            node_executed TEXT,
            tool_name TEXT,
            content TEXT
        )
    """))

    exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": agent_user}).first()
    verb = "ALTER" if exists else "CREATE"
    # Role DDL cannot take bind parameters, so the literal is escaped by hand
    pw = agent_password.replace("'", "''")
    conn.execute(text(f"{verb} ROLE {agent_user} LOGIN PASSWORD '{pw}'"))
    conn.execute(text(f"ALTER ROLE {agent_user} SET statement_timeout = '10s'"))

    conn.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {agent_user}"))
    conn.execute(text(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {agent_user}"))
    conn.execute(text(f"GRANT SELECT ON {SCHEMA}.vw_active_fleet TO {agent_user}"))
    conn.execute(text(f"GRANT INSERT ON {SCHEMA}.agent_audit_log TO {agent_user}"))

print("✅ Database setup complete!")

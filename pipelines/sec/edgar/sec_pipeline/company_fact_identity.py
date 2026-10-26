"""Company-fact identity must retain durations, frames and source vintages.

The semantic ID already binds start/end and frame. A replacing key using only
the end date destroys distinct observations before downstream qualification.
Existing tables require an explicit verified migration; writers never alter them.
"""
import json
import re

from pipelines.sec.edgar.sec_text_layout import normalized_clickhouse_key

PREFIX = "cik, taxonomy, tag, unit_code, ifNull(period_end_date, toDate('1970-01-01')), ifNull(accession_number, '')"
SORTING_KEY = PREFIX + ', company_fact_id, source_content_sha256'


def verify(client, database, table='sec_xbrl_company_fact_v3'):
    if not all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', x) for x in (database, table)):
        raise ValueError('Invalid SEC identity table')
    rows = [json.loads(line) for line in client.execute(
        "SELECT sorting_key,engine FROM system.tables WHERE database='"+database+
        "' AND name='"+table+"' FORMAT JSONEachRow").splitlines() if line.strip()]
    if (len(rows) != 1 or rows[0]['engine'] != 'ReplacingMergeTree'
            or normalized_clickhouse_key(rows[0]['sorting_key']) != normalized_clickhouse_key(SORTING_KEY)):
        raise ValueError('SEC Company Facts replacing key loses fact/source identity; verified migration required')


def corrected_ddl(source_ddl, database, table):
    """Build an empty destination only; never modify/drop an existing table."""
    if not all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', x) for x in (database, table)):
        raise ValueError('Invalid SEC migration destination')
    order = re.search(r'\bORDER\s+BY\s+(.+?)(?=\s+(?:SETTINGS|PRIMARY\s+KEY)\b|$)', source_ddl, re.S)
    if not order or normalized_clickhouse_key(order[1]) not in {
            normalized_clickhouse_key(PREFIX), normalized_clickhouse_key(SORTING_KEY)}:
        raise ValueError('Unreviewed SEC Company Facts source key')
    ddl = source_ddl[:order.start()]+'\nORDER BY ('+SORTING_KEY+')'+source_ddl[order.end():]
    if not re.search(r'\bPRIMARY\s+KEY\s', ddl):
        ddl = ddl.replace('\nORDER BY ', '\nPRIMARY KEY ('+PREFIX+')\nORDER BY ', 1)
    ddl = re.sub(r"\s+UUID\s+'[^']+'", '', ddl, count=1, flags=re.I)
    ddl, count = re.subn(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:`[^`]+`|\w+)\.(?:`[^`]+`|\w+)',
        'CREATE TABLE `'+database+'`.`'+table+'`', ddl, count=1, flags=re.I)
    if count != 1:
        raise ValueError('SEC source DDL is not a qualified table definition')
    return ddl

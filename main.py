import os
import sys
import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv
import plaid
from plaid.api import plaid_api
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.accounts_get_request import AccountsGetRequest

# Load environment variables from connections/.env and root .env
load_dotenv('connections/.env')
load_dotenv('.env')

# Initialize Plaid Client
PLAID_CLIENT_ID = os.getenv('PLAID_CLIENT_ID')
PLAID_SECRET = os.getenv('PLAID_SECRET')
PLAID_ENV = os.getenv('PLAID_ENV', 'production').lower()

if PLAID_ENV == 'production':
    host = plaid.Environment.Production
else:
    host = plaid.Environment.Sandbox

configuration = plaid.Configuration(
    host=host,
    api_key={
        'clientId': PLAID_CLIENT_ID,
        'secret': PLAID_SECRET,
    }
)
api_client = plaid.ApiClient(configuration)
plaid_client = plaid_api.PlaidApi(api_client)


def get_db_connection():
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        raise ValueError("DATABASE_URL environment variable is not set.")
    # Strip any accidental duplicate prefix
    if db_url.startswith('DATABASE_URL='):
        db_url = db_url.replace('DATABASE_URL=', '', 1)
    return psycopg2.connect(db_url)


def init_db(conn):
    """Initializes the required database schema if not already present."""
    schema_sql = """
    CREATE TABLE IF NOT EXISTS plaid_items (
        id TEXT PRIMARY KEY,
        institution_name TEXT,
        access_token TEXT NOT NULL,
        next_cursor TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
    );

    CREATE TABLE IF NOT EXISTS accounts (
        id TEXT PRIMARY KEY,
        item_id TEXT REFERENCES plaid_items(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        mask TEXT,
        type TEXT,
        subtype TEXT,
        current_balance NUMERIC(12, 2),
        available_balance NUMERIC(12, 2),
        iso_currency_code TEXT,
        updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
    );

    CREATE TABLE IF NOT EXISTS transactions (
        id TEXT PRIMARY KEY,
        account_id TEXT REFERENCES accounts(id) ON DELETE CASCADE,
        amount NUMERIC(12, 2) NOT NULL,
        date DATE NOT NULL,
        datetime TIMESTAMPTZ,
        name TEXT NOT NULL,
        merchant_name TEXT,
        category_primary TEXT,
        category_detailed TEXT,
        payment_channel TEXT,
        pending BOOLEAN DEFAULT FALSE,
        iso_currency_code TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_transactions_account_date ON transactions(account_id, date DESC);
    """
    with conn.cursor() as cur:
        cur.execute(schema_sql)
    conn.commit()


def sync_accounts(conn, item_id: str, access_token: str, institution_name: str = None):
    """Fetches and upserts account balances and metadata."""
    # Ensure parent plaid_items record exists for foreign key constraint
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO plaid_items (id, institution_name, access_token, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                institution_name = COALESCE(EXCLUDED.institution_name, plaid_items.institution_name),
                access_token = EXCLUDED.access_token,
                updated_at = NOW();
        """, (item_id, institution_name, access_token))
    conn.commit()

    req = AccountsGetRequest(access_token=access_token)
    res = plaid_client.accounts_get(req).to_dict()

    upsert_sql = """
        INSERT INTO accounts (id, item_id, name, mask, type, subtype, current_balance, available_balance, iso_currency_code, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (id) DO UPDATE SET
            item_id = EXCLUDED.item_id,
            name = EXCLUDED.name,
            mask = EXCLUDED.mask,
            type = EXCLUDED.type,
            subtype = EXCLUDED.subtype,
            current_balance = EXCLUDED.current_balance,
            available_balance = EXCLUDED.available_balance,
            iso_currency_code = EXCLUDED.iso_currency_code,
            updated_at = NOW();
    """

    records = []
    for acc in res.get('accounts', []):
        balances = acc.get('balances', {})
        records.append((
            acc.get('account_id'),
            item_id,
            acc.get('name'),
            acc.get('mask'),
            str(acc.get('type')) if acc.get('type') else None,
            str(acc.get('subtype')) if acc.get('subtype') else None,
            balances.get('current'),
            balances.get('available'),
            balances.get('iso_currency_code') or balances.get('unofficial_currency_code')
        ))

    with conn.cursor() as cur:
        if records:
            execute_batch(cur, upsert_sql, records)
    conn.commit()
    print(f"[{institution_name or item_id}] Synced {len(records)} accounts.")


def sync_transactions(conn, item_id: str, access_token: str, institution_name: str = None):
    """Fetches transaction updates incrementally using Plaid transactions_sync."""
    with conn.cursor() as cur:
        cur.execute("SELECT next_cursor FROM plaid_items WHERE id = %s", (item_id,))
        row = cur.fetchone()
        cursor = row[0] if row and row[0] else ''

    added = []
    modified = []
    removed = []
    has_more = True

    while has_more:
        req = TransactionsSyncRequest(
            access_token=access_token,
            cursor=cursor
        )
        res = plaid_client.transactions_sync(req).to_dict()
        cursor = res.get('next_cursor', '')
        added.extend(res.get('added', []))
        modified.extend(res.get('modified', []))
        removed.extend(res.get('removed', []))
        has_more = res.get('has_more', False)

    upsert_sql = """
        INSERT INTO transactions (
            id, account_id, amount, date, datetime, name, merchant_name,
            category_primary, category_detailed, payment_channel, pending, iso_currency_code, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (id) DO UPDATE SET
            account_id = EXCLUDED.account_id,
            amount = EXCLUDED.amount,
            date = EXCLUDED.date,
            datetime = EXCLUDED.datetime,
            name = EXCLUDED.name,
            merchant_name = EXCLUDED.merchant_name,
            category_primary = EXCLUDED.category_primary,
            category_detailed = EXCLUDED.category_detailed,
            payment_channel = EXCLUDED.payment_channel,
            pending = EXCLUDED.pending,
            iso_currency_code = EXCLUDED.iso_currency_code,
            updated_at = NOW();
    """

    tx_records = []
    for tx in added + modified:
        pfc = tx.get('personal_finance_category') or {}
        tx_records.append((
            tx.get('transaction_id'),
            tx.get('account_id'),
            tx.get('amount'),
            tx.get('date'),
            tx.get('datetime'),
            tx.get('name'),
            tx.get('merchant_name'),
            pfc.get('primary'),
            pfc.get('detailed'),
            tx.get('payment_channel'),
            tx.get('pending', False),
            tx.get('iso_currency_code') or tx.get('unofficial_currency_code')
        ))

    with conn.cursor() as cur:
        if tx_records:
            execute_batch(cur, upsert_sql, tx_records)

        if removed:
            del_ids = [r['transaction_id'] if isinstance(r, dict) else r for r in removed]
            cur.execute("DELETE FROM transactions WHERE id = ANY(%s)", (del_ids,))

        # Persist updated cursor
        cur.execute("""
            UPDATE plaid_items
            SET next_cursor = %s, updated_at = NOW()
            WHERE id = %s;
        """, (cursor, item_id))

    conn.commit()
    print(f"[{institution_name or item_id}] Sync complete: +{len(added)} added, ~{len(modified)} modified, -{len(removed)} removed.")


def main() -> list:
    """Run a full sync. Returns a list of error messages (empty on success)."""
    items = []
    errors = []
    
    amex_item_id = os.getenv('AMEX_ITEM_ID')
    amex_token = os.getenv('AMEX_ACCESS_TOKEN')
    if amex_item_id and amex_token:
        items.append(("American Express", amex_item_id, amex_token))

    chase_item_id = os.getenv('CHASE_ITEM_ID')
    chase_token = os.getenv('CHASE_ACCESS_TOKEN')
    if chase_item_id and chase_token:
        items.append(("Chase", chase_item_id, chase_token))

    if not items:
        msg = "No Plaid item access tokens found in environment variables (e.g. AMEX_ACCESS_TOKEN, CHASE_ACCESS_TOKEN)."
        print(msg, file=sys.stderr)
        return [msg]

    print("Connecting to Supabase PostgreSQL database...")
    try:
        conn = get_db_connection()
    except Exception as e:
        print(f"Database connection error: {e}", file=sys.stderr)
        return [f"Database connection error: {e}"]

    try:
        print("Ensuring database tables exist...")
        init_db(conn)

        for institution_name, item_id, token in items:
            print(f"\n--- Syncing {institution_name} ({item_id}) ---")
            try:
                sync_accounts(conn, item_id, token, institution_name=institution_name)
                sync_transactions(conn, item_id, token, institution_name=institution_name)
            except plaid.ApiException as e:
                print(f"Plaid API error for {institution_name}: {e.body}", file=sys.stderr)
                errors.append(f"Plaid API error for {institution_name}: {e.body}")
            except Exception as e:
                print(f"Error syncing {institution_name}: {e}", file=sys.stderr)
                errors.append(f"Error syncing {institution_name}: {e}")
    finally:
        conn.close()
        print("\nFinished synchronization run.")
    return errors


if __name__ == '__main__':
    sys.exit(1 if main() else 0)

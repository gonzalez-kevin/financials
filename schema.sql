-- Plaid to Supabase Database Schema

-- Track connected Plaid items and their sync progress
CREATE TABLE IF NOT EXISTS plaid_items (
    id TEXT PRIMARY KEY,                   -- e.g. AMEX_ITEM_ID, CHASE_ITEM_ID
    institution_name TEXT,
    access_token TEXT NOT NULL,
    next_cursor TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Track accounts linked to each item
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,                   -- Plaid account_id
    item_id TEXT REFERENCES plaid_items(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    mask TEXT,
    type TEXT,
    subtype TEXT,
    current_balance NUMERIC(12, 2),
    available_balance NUMERIC(12, 2),
    credit_limit NUMERIC(12, 2),
    iso_currency_code TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS credit_limit NUMERIC(12, 2);

-- Store transaction ledger
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,                   -- Plaid transaction_id
    account_id TEXT REFERENCES accounts(id) ON DELETE CASCADE,
    amount NUMERIC(12, 2) NOT NULL,        -- Plaid amounts: positive = debit/expense, negative = credit/income
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

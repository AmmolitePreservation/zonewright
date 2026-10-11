-- A row change holds only INSERT, UPDATE, DELETE and REPLACE; the ledger runs them in one transaction, between a guard that
-- the scope's rows are as staged and guards that no row outside the scope changed and that check.sql gives 1.
INSERT INTO variables (varname, value, information) VALUES ('ledgerExample', '1', 'ledger template example');

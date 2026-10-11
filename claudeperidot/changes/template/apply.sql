-- A row change holds only INSERT, UPDATE, DELETE and REPLACE statements; the ledger runs them in one transaction,
-- between a guard that the rows are as staged and a guard that check.sql gives 1.
INSERT INTO variables (varname, value, information) VALUES ('ledgerExample', '1', 'ledger template example');

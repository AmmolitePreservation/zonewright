-- One SELECT giving one row with a column named applied: 1 when the change is in place and 0 when it is not
-- (for a schema change, the number of steps done).
SELECT COUNT(*) AS applied FROM variables WHERE varname = 'ledgerExample' AND value = '1';

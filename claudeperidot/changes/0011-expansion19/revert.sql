-- Written by ledger.py stage from the rows in saved.json; ledger.py revert runs it to put back exactly those rows.
DELETE FROM `rule_values` WHERE (ruleset_id = 1 AND rule_name = 'Expansion:CurrentExpansion');
INSERT INTO `rule_values` (`ruleset_id`, `rule_name`, `rule_value`, `notes`) VALUES
  (1, 'Expansion:CurrentExpansion', '4', 'The current expansion enabled for the server [-1 = ALL, 0 = Classic, 1 = Kunark etc.]');

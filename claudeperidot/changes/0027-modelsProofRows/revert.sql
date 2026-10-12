-- Written by ledger.py stage from the rows in saved.json; ledger.py revert runs it to put back exactly those rows.
DELETE FROM `zone` WHERE (short_name = 'interiorwalltest');
DELETE FROM `doors` WHERE (zone = 'interiorwalltest');
DELETE FROM `object` WHERE (zoneid = 767);

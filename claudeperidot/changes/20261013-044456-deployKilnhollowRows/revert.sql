-- Written by ledger.py stage from the rows in saved.json; ledger.py revert runs it to put back exactly those rows.
DELETE FROM `zone` WHERE (short_name = 'kilnhollow');
DELETE FROM `zone_points` WHERE (zone = 'kilnhollow');
DELETE FROM `doors` WHERE (zone = 'kilnhollow');
DELETE FROM `object` WHERE (zoneid = 787);

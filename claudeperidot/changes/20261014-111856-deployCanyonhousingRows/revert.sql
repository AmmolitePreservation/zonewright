-- Written by ledger.py stage from the rows in saved.json; ledger.py revert runs it to put back exactly those rows.
DELETE FROM `zone` WHERE (short_name = 'canyonhousing');
DELETE FROM `zone_points` WHERE (zone = 'canyonhousing');
DELETE FROM `doors` WHERE (zone = 'canyonhousing');
DELETE FROM `object` WHERE (zoneid = 788);

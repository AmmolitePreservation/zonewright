SELECT ((SELECT COUNT(*) FROM inventory WHERE character_id IN (SELECT id FROM character_data WHERE name = 'Claudewright')) = 0) AS applied;

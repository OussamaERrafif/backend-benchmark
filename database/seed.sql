INSERT INTO users (id, name, email, created_at)
SELECT i, 'User ' || i, 'user' || i || '@example.test', TIMESTAMP '2026-01-01 00:00:00'
FROM generate_series(1, 100000) AS i
ON CONFLICT (id) DO NOTHING;
SELECT setval('users_id_seq', 100000, true);
ANALYZE users;

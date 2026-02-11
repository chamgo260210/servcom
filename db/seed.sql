-- File: /db/seed.sql
-- Seed data for Dasan Information Center shift management

-- Clean existing data
TRUNCATE audit_logs, shift_requests, user_shifts, shifts, auth_accounts, users RESTART IDENTITY CASCADE;

INSERT INTO users (id, name, identifier, role)
VALUES
    ('11111111-1111-1111-1111-111111111111', 'Master Admin', 'M0001', 'MASTER'),
    ('22222222-2222-2222-2222-222222222222', 'Operator One', 'O0001', 'OPERATOR'),
    ('33333333-3333-3333-3333-333333333333', 'Member Kim', 'S2023001', 'MEMBER'),
    ('44444444-4444-4444-4444-444444444444', 'Member Lee', 'S2023002', 'MEMBER'),
    ('55555555-5555-5555-5555-555555555555', 'Member Park', 'S2023003', 'MEMBER');

INSERT INTO auth_accounts (user_id, login_id, password_hash)
VALUES
    ('11111111-1111-1111-1111-111111111111', 'master', '$2b$12$pSX/BzwVIcXXHPNdD6LRueFZnHu7aQ4mZPDE5IZ8T.MpYIxaFmjPC'), -- Password: Master123!
    ('22222222-2222-2222-2222-222222222222', 'operator', '$2b$12$HtMj5ZEZ7YfceJy4tDtW1eAneriBJIg/pGDPIX.SKp9xC7A36QGfK'), -- Password: Operator123!
    ('33333333-3333-3333-3333-333333333333', 'kim', '$2b$12$bpgqOwoDZy47/tUcY2f8uOaqk2tqLlS9m.uvmBKnl3SXsyZ65wAIK'), -- Password: Member123!
    ('44444444-4444-4444-4444-444444444444', 'lee', '$2b$12$bpgqOwoDZy47/tUcY2f8uOaqk2tqLlS9m.uvmBKnl3SXsyZ65wAIK'),
    ('55555555-5555-5555-5555-555555555555', 'park', '$2b$12$bpgqOwoDZy47/tUcY2f8uOaqk2tqLlS9m.uvmBKnl3SXsyZ65wAIK');

-- Shifts (weekday: 0=Mon ... 6=Sun)
INSERT INTO shifts (id, name, weekday, start_time, end_time, location)
VALUES
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'Morning Desk', 0, '09:00', '12:00', 'Reference Desk'),
    ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'Afternoon Desk', 0, '13:00', '17:00', 'Reference Desk'),
    ('cccccccc-cccc-cccc-cccc-cccccccccccc', 'Evening Desk', 1, '17:00', '21:00', 'Reference Desk');

INSERT INTO user_shifts (id, user_id, shift_id, valid_from)
VALUES
    ('dddddddd-dddd-dddd-dddd-dddddddddddd', '33333333-3333-3333-3333-333333333333', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '2024-01-01'),
    ('eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee', '44444444-4444-4444-4444-444444444444', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '2024-01-01'),
    ('ffffffff-ffff-ffff-ffff-ffffffffffff', '55555555-5555-5555-5555-555555555555', 'cccccccc-cccc-cccc-cccc-cccccccccccc', '2024-01-01');

INSERT INTO shift_requests (id, user_id, type, target_date, target_shift_id, reason, status)
VALUES
    ('99999999-9999-9999-9999-999999999999', '33333333-3333-3333-3333-333333333333', 'ABSENCE', '2024-01-08', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'Medical appointment', 'PENDING');

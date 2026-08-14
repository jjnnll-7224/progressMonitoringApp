-- PLC Intelligence: weekly PLC calendar / first-term planning layer.
-- Safe to add to an existing prototype DB because these are new tables.

CREATE TABLE IF NOT EXISTS school_terms (
    term_id INTEGER PRIMARY KEY,
    school_year TEXT NOT NULL,
    term_name TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 1,
    UNIQUE (school_year, term_name)
);

CREATE TABLE IF NOT EXISTS calendar_weeks (
    week_id INTEGER PRIMARY KEY,
    term_id INTEGER NOT NULL
        REFERENCES school_terms(term_id) ON DELETE CASCADE,
    week_number INTEGER NOT NULL,
    week_start_date TEXT NOT NULL,
    week_end_date TEXT NOT NULL,
    label TEXT,
    UNIQUE (term_id, week_number)
);

-- Optional district pacing layer. Leave empty when a district does not provide
-- a pacing guide. The PLC team can still create/assign a cycle manually.
CREATE TABLE IF NOT EXISTS district_pacing_week_standards (
    pacing_week_standard_id INTEGER PRIMARY KEY,
    week_id INTEGER NOT NULL
        REFERENCES calendar_weeks(week_id) ON DELETE CASCADE,
    subject TEXT NOT NULL,
    grade_level TEXT NOT NULL,
    standard_id INTEGER NOT NULL
        REFERENCES standards(standard_id),
    instructional_focus TEXT,
    UNIQUE (week_id, subject, grade_level, standard_id)
);

-- One PLC team can have one weekly workspace per calendar week.
-- The same PLC cycle may span more than one week if a team chooses.
CREATE TABLE IF NOT EXISTS plc_week_assignments (
    week_assignment_id INTEGER PRIMARY KEY,
    team_id INTEGER NOT NULL
        REFERENCES plc_teams(team_id) ON DELETE CASCADE,
    week_id INTEGER NOT NULL
        REFERENCES calendar_weeks(week_id) ON DELETE CASCADE,
    cycle_id INTEGER
        REFERENCES plc_cycles(cycle_id) ON DELETE SET NULL,
    assignment_source TEXT NOT NULL DEFAULT 'Team Assigned'
        CHECK (assignment_source IN ('District Pacing','Team Assigned','Manual')),
    completed_steps INTEGER NOT NULL DEFAULT 0
        CHECK (completed_steps BETWEEN 0 AND 5),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (team_id, week_id)
);

CREATE TABLE IF NOT EXISTS plc_week_notes (
    note_id INTEGER PRIMARY KEY,
    week_assignment_id INTEGER NOT NULL
        REFERENCES plc_week_assignments(week_assignment_id) ON DELETE CASCADE,
    user_id INTEGER
        REFERENCES app_users(user_id),
    note_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_calendar_weeks_term
    ON calendar_weeks(term_id, week_number);

CREATE INDEX IF NOT EXISTS idx_plc_week_assignments_team
    ON plc_week_assignments(team_id, week_id);

CREATE INDEX IF NOT EXISTS idx_plc_week_notes_assignment
    ON plc_week_notes(week_assignment_id, created_at);

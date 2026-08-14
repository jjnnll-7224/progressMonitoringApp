-- Instructional responses are the saved "what will we do next?" decisions
-- made directly inside a weekly PLC cycle.  Student groups are derived from
-- CFA evidence, so teachers do not need to maintain a second grouping workflow.

CREATE TABLE IF NOT EXISTS plc_instructional_responses (
    response_id INTEGER PRIMARY KEY,
    cycle_id INTEGER NOT NULL
        REFERENCES plc_cycles(cycle_id) ON DELETE CASCADE,
    source_administration_id INTEGER NOT NULL
        REFERENCES assessment_administrations(administration_id) ON DELETE CASCADE,
    mastery_status TEXT NOT NULL
        CHECK (mastery_status IN ('Mastered','Approaching','Developing','Intensive')),
    response_type TEXT NOT NULL,
    focus_core_idea_id INTEGER
        REFERENCES standard_core_ideas(core_idea_id),
    focus_text TEXT,
    strategy TEXT,
    owner_user_id INTEGER
        REFERENCES app_users(user_id),
    reassess_date TEXT,
    created_by_user_id INTEGER
        REFERENCES app_users(user_id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (cycle_id, source_administration_id, mastery_status)
);

CREATE TABLE IF NOT EXISTS plc_instructional_response_students (
    response_id INTEGER NOT NULL
        REFERENCES plc_instructional_responses(response_id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL
        REFERENCES students(student_id) ON DELETE CASCADE,
    PRIMARY KEY (response_id, student_id)
);

CREATE INDEX IF NOT EXISTS idx_instructional_responses_cycle
    ON plc_instructional_responses(cycle_id, source_administration_id);

CREATE INDEX IF NOT EXISTS idx_instructional_response_students_student
    ON plc_instructional_response_students(student_id);

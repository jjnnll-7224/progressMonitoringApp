
CREATE TABLE IF NOT EXISTS schools (
    school_id INTEGER PRIMARY KEY,
    school_code TEXT NOT NULL UNIQUE,
    school_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_users (
    user_id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('Teacher','Coach','Principal','District Administrator')),
    school_id INTEGER REFERENCES schools(school_id)
);

CREATE TABLE IF NOT EXISTS plc_teams (
    team_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    school_id INTEGER NOT NULL REFERENCES schools(school_id),
    grade_level TEXT NOT NULL,
    subject TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plc_team_members (
    team_id INTEGER NOT NULL REFERENCES plc_teams(team_id),
    user_id INTEGER NOT NULL REFERENCES app_users(user_id),
    PRIMARY KEY (team_id, user_id)
);

CREATE TABLE IF NOT EXISTS standards (
    standard_id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    subject TEXT NOT NULL,
    grade_level TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS standard_core_ideas (
    core_idea_id INTEGER PRIMARY KEY,
    standard_id INTEGER NOT NULL
        REFERENCES standards(standard_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE (standard_id, name)
);

CREATE TABLE IF NOT EXISTS students (
    student_id INTEGER PRIMARY KEY,
    student_number TEXT NOT NULL UNIQUE,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    grade_level TEXT NOT NULL,
    school_id INTEGER NOT NULL REFERENCES schools(school_id),
    user_id INTEGER NULL REFERENCES app_users(user_id)
);

CREATE TABLE IF NOT EXISTS plc_cycles (
    cycle_id INTEGER PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES plc_teams(team_id),
    standard_id INTEGER NOT NULL REFERENCES standards(standard_id),
    name TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plc_cycle_standards (
    cycle_id INTEGER NOT NULL REFERENCES plc_cycles(cycle_id) ON DELETE CASCADE,
    standard_id INTEGER NOT NULL REFERENCES standards(standard_id),
    PRIMARY KEY (cycle_id, standard_id)
);

CREATE TABLE IF NOT EXISTS assessments (
    assessment_id INTEGER PRIMARY KEY,
    cycle_id INTEGER REFERENCES plc_cycles(cycle_id),
    name TEXT NOT NULL,
    standard_id INTEGER NOT NULL REFERENCES standards(standard_id),
    assessment_type TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assessment_standards (
    assessment_id INTEGER NOT NULL
        REFERENCES assessments(assessment_id) ON DELETE CASCADE,
    standard_id INTEGER NOT NULL
        REFERENCES standards(standard_id),
    PRIMARY KEY (assessment_id, standard_id)
);

CREATE TABLE IF NOT EXISTS assessment_questions (
    question_id INTEGER PRIMARY KEY,
    assessment_id INTEGER NOT NULL
        REFERENCES assessments(assessment_id) ON DELETE CASCADE,

    question_number INTEGER NOT NULL,
    question_type TEXT NOT NULL,
    max_points REAL NOT NULL CHECK (max_points > 0),

    -- Temporary compatibility fields
    standard_id INTEGER
        REFERENCES standards(standard_id),

    subskill TEXT,

    -- New Core Idea relationship
    core_idea_id INTEGER
        REFERENCES standard_core_ideas(core_idea_id),

    UNIQUE (assessment_id, question_number)
);

CREATE TABLE IF NOT EXISTS assessment_administrations (
    administration_id INTEGER PRIMARY KEY,
    assessment_id INTEGER NOT NULL
        REFERENCES assessments(assessment_id),
    cycle_assessment_id INTEGER
        REFERENCES plc_cycle_assessments(cycle_assessment_id) ON DELETE CASCADE,
    administration_type TEXT NOT NULL
        CHECK (administration_type IN ('PRE','POST')),
    administered_on TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plc_cycle_assessments (
    cycle_assessment_id INTEGER PRIMARY KEY,
    cycle_id INTEGER NOT NULL
        REFERENCES plc_cycles(cycle_id) ON DELETE CASCADE,
    assessment_id INTEGER NOT NULL
        REFERENCES assessments(assessment_id) ON DELETE CASCADE,
    assigned_on TEXT NOT NULL DEFAULT (date('now')),
    status TEXT NOT NULL DEFAULT 'Assigned',
    UNIQUE (cycle_id, assessment_id)
);

CREATE TABLE IF NOT EXISTS student_item_scores (
    administration_id INTEGER NOT NULL REFERENCES assessment_administrations(administration_id),
    student_id INTEGER NOT NULL REFERENCES students(student_id),
    question_id INTEGER NOT NULL REFERENCES assessment_questions(question_id),
    points_earned REAL CHECK (points_earned >= 0),
    PRIMARY KEY (administration_id, student_id, question_id)
);

CREATE TABLE IF NOT EXISTS interventions (
    intervention_id INTEGER PRIMARY KEY,
    cycle_id INTEGER NOT NULL REFERENCES plc_cycles(cycle_id),
    name TEXT NOT NULL,
    owner_user_id INTEGER REFERENCES app_users(user_id),
    start_date TEXT NOT NULL,
    end_date TEXT,
    status TEXT NOT NULL
);

-- Details are separate from the original interventions table so existing demo
-- databases remain compatible when this feature is added.
CREATE TABLE IF NOT EXISTS intervention_details (
    intervention_id INTEGER PRIMARY KEY REFERENCES interventions(intervention_id) ON DELETE CASCADE,
    intervention_type TEXT NOT NULL,
    strategy TEXT,
    evidence_to_collect TEXT,
    success_criterion TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- An intervention is assigned to one saved instructional group. Group members
-- stay in student_group_members, so we do not duplicate student rosters here.
CREATE TABLE IF NOT EXISTS intervention_assignments (
    intervention_id INTEGER NOT NULL REFERENCES interventions(intervention_id) ON DELETE CASCADE,
    group_id INTEGER NOT NULL REFERENCES student_groups(group_id) ON DELETE CASCADE,
    PRIMARY KEY (intervention_id, group_id)
);

-- A saved instructional group belongs to one PLC cycle and one CFA administration.
CREATE TABLE IF NOT EXISTS student_groups (
    group_id INTEGER PRIMARY KEY,
    cycle_id INTEGER NOT NULL REFERENCES plc_cycles(cycle_id) ON DELETE CASCADE,
    administration_id INTEGER NOT NULL REFERENCES assessment_administrations(administration_id),
    name TEXT NOT NULL,
    focus TEXT,
    group_type TEXT NOT NULL DEFAULT 'Suggested',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Each student can belong to one saved group in the current grouping set.
CREATE TABLE IF NOT EXISTS student_group_members (
    group_id INTEGER NOT NULL REFERENCES student_groups(group_id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(student_id),
    PRIMARY KEY (group_id, student_id)
);

CREATE TABLE IF NOT EXISTS commitments (
    commitment_id INTEGER PRIMARY KEY,
    cycle_id INTEGER NOT NULL REFERENCES plc_cycles(cycle_id),
    name TEXT NOT NULL,
    action_step TEXT NOT NULL,
    evidence TEXT,
    due_date TEXT NOT NULL,
    assigned_user_id INTEGER REFERENCES app_users(user_id),
    notes TEXT,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cycle_meeting_progress (
    cycle_id INTEGER PRIMARY KEY REFERENCES plc_cycles(cycle_id),
    completed_steps INTEGER NOT NULL DEFAULT 0 CHECK (completed_steps BETWEEN 0 AND 5)
);

CREATE TABLE IF NOT EXISTS pacing_guides (
    pacing_guide_id INTEGER PRIMARY KEY,
    school_year TEXT NOT NULL,
    grade_level TEXT NOT NULL,
    subject TEXT NOT NULL,
    current_term TEXT NOT NULL,
    current_week INTEGER NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (school_year, grade_level, subject)
);

CREATE TABLE IF NOT EXISTS pacing_guide_entries (
    pacing_guide_id INTEGER NOT NULL REFERENCES pacing_guides(pacing_guide_id) ON DELETE CASCADE,
    term_name TEXT NOT NULL,
    week_number INTEGER NOT NULL,
    standard_id INTEGER NOT NULL REFERENCES standards(standard_id),
    PRIMARY KEY (pacing_guide_id, term_name, week_number)
);

CREATE INDEX IF NOT EXISTS idx_assessments_standard
    ON assessments(standard_id);

CREATE INDEX IF NOT EXISTS idx_administrations_assessment_status_date
    ON assessment_administrations(assessment_id, status, administered_on);

CREATE INDEX IF NOT EXISTS idx_item_scores_administration_student
    ON student_item_scores(administration_id, student_id);

CREATE TABLE IF NOT EXISTS courses (
    course_id INTEGER PRIMARY KEY,
    course_code TEXT NOT NULL,
    course_name TEXT NOT NULL,
    subject TEXT NOT NULL,
    grade_level TEXT
);

CREATE TABLE IF NOT EXISTS sections (
    section_id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(course_id),
    school_id INTEGER NOT NULL REFERENCES schools(school_id),
    teacher_user_id INTEGER NOT NULL REFERENCES app_users(user_id),
    section_name TEXT NOT NULL,
    term_name TEXT
);

CREATE TABLE IF NOT EXISTS section_enrollments (
    section_id INTEGER NOT NULL REFERENCES sections(section_id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    PRIMARY KEY (section_id, student_id)
);

CREATE TABLE IF NOT EXISTS assessment_sections (
    assessment_id INTEGER NOT NULL REFERENCES assessments(assessment_id) ON DELETE CASCADE,
    section_id INTEGER NOT NULL REFERENCES sections(section_id),
    PRIMARY KEY (assessment_id, section_id)
);

CREATE TABLE IF NOT EXISTS cycle_assessment_sections (
    cycle_assessment_id INTEGER NOT NULL
        REFERENCES plc_cycle_assessments(cycle_assessment_id) ON DELETE CASCADE,
    section_id INTEGER NOT NULL
        REFERENCES sections(section_id),
    PRIMARY KEY (cycle_assessment_id, section_id)
);

CREATE TABLE IF NOT EXISTS coach_teacher_assignments (
    coach_user_id INTEGER NOT NULL REFERENCES app_users(user_id) ON DELETE CASCADE,
    teacher_user_id INTEGER NOT NULL REFERENCES app_users(user_id) ON DELETE CASCADE,
    PRIMARY KEY (coach_user_id, teacher_user_id)
);

CREATE TABLE IF NOT EXISTS user_school_assignments (
    user_id INTEGER NOT NULL REFERENCES app_users(user_id) ON DELETE CASCADE,
    school_id INTEGER NOT NULL REFERENCES schools(school_id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, school_id)
);

CREATE TABLE IF NOT EXISTS plc_cycle_notes (
    note_id INTEGER PRIMARY KEY,
    cycle_id INTEGER NOT NULL
        REFERENCES plc_cycles(cycle_id) ON DELETE CASCADE,
    user_id INTEGER
        REFERENCES app_users(user_id),
    note_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_plc_cycle_notes_cycle
    ON plc_cycle_notes(cycle_id, created_at);
CREATE INDEX IF NOT EXISTS idx_core_ideas_standard
    ON standard_core_ideas(standard_id);
CREATE INDEX IF NOT EXISTS idx_assessment_standards_standard
    ON assessment_standards(standard_id);
CREATE INDEX IF NOT EXISTS idx_questions_core_idea
    ON assessment_questions(core_idea_id);
CREATE INDEX IF NOT EXISTS idx_questions_standard
    ON assessment_questions(standard_id);
CREATE INDEX IF NOT EXISTS idx_cycle_assessment_cycle
    ON plc_cycle_assessments(cycle_id);
CREATE INDEX IF NOT EXISTS idx_cycle_assessment_assessment
    ON plc_cycle_assessments(assessment_id);
CREATE INDEX IF NOT EXISTS idx_admin_cycle_assessment
    ON assessment_administrations(cycle_assessment_id);
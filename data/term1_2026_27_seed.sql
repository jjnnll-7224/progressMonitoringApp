-- Salt Lake City School District 2026-27 first quarter.
-- Official district calendar:
-- First day: 2026-08-18
-- 1st Quarter: 2026-08-18 through 2026-10-23

INSERT OR IGNORE INTO school_terms
    (term_id, school_year, term_name, start_date, end_date, sort_order)
VALUES
    (202701, '2026-2027', 'Term 1', '2026-08-18', '2026-10-23', 1);

INSERT OR IGNORE INTO calendar_weeks
    (week_id, term_id, week_number, week_start_date, week_end_date, label)
VALUES
    (20270101, 202701, 1,  '2026-08-18', '2026-08-21', 'Week 1'),
    (20270102, 202701, 2,  '2026-08-24', '2026-08-28', 'Week 2'),
    (20270103, 202701, 3,  '2026-08-31', '2026-09-04', 'Week 3'),
    (20270104, 202701, 4,  '2026-09-07', '2026-09-11', 'Week 4'),
    (20270105, 202701, 5,  '2026-09-14', '2026-09-18', 'Week 5'),
    (20270106, 202701, 6,  '2026-09-21', '2026-09-25', 'Week 6'),
    (20270107, 202701, 7,  '2026-09-28', '2026-10-02', 'Week 7'),
    (20270108, 202701, 8,  '2026-10-05', '2026-10-09', 'Week 8'),
    (20270109, 202701, 9,  '2026-10-12', '2026-10-16', 'Week 9'),
    (20270110, 202701, 10, '2026-10-19', '2026-10-23', 'Week 10');

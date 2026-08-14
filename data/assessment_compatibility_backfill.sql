INSERT OR IGNORE INTO assessment_standards (assessment_id, standard_id)
SELECT assessment_id, standard_id
FROM assessments
WHERE standard_id IS NOT NULL;

INSERT OR IGNORE INTO standard_core_ideas (
    standard_id,
    name,
    description,
    sort_order
)
SELECT DISTINCT
    q.standard_id,
    COALESCE(NULLIF(TRIM(q.subskill), ''), 'General'),
    NULL,
    CASE COALESCE(NULLIF(TRIM(q.subskill), ''), 'General')
        WHEN 'Concept' THEN 1
        WHEN 'Representation' THEN 2
        WHEN 'Application' THEN 3
        WHEN 'Reasoning' THEN 4
        WHEN 'Transfer' THEN 5
        ELSE 99
    END
FROM assessment_questions AS q
WHERE q.standard_id IS NOT NULL;

UPDATE assessment_questions
SET core_idea_id = (
    SELECT ci.core_idea_id
    FROM standard_core_ideas AS ci
    WHERE ci.standard_id = assessment_questions.standard_id
      AND ci.name = COALESCE(
          NULLIF(TRIM(assessment_questions.subskill), ''),
          'General'
      )
)
WHERE core_idea_id IS NULL
  AND standard_id IS NOT NULL;

INSERT OR IGNORE INTO plc_cycle_assessments (
    cycle_id,
    assessment_id,
    assigned_on,
    status
)
SELECT
    a.cycle_id,
    a.assessment_id,
    COALESCE(
        (
            SELECT MIN(ad.administered_on)
            FROM assessment_administrations AS ad
            WHERE ad.assessment_id = a.assessment_id
        ),
        date('now')
    ),
    'Assigned'
FROM assessments AS a
WHERE a.cycle_id IS NOT NULL;

INSERT OR IGNORE INTO cycle_assessment_sections (
    cycle_assessment_id,
    section_id
)
SELECT
    pca.cycle_assessment_id,
    old_sections.section_id
FROM assessment_sections AS old_sections
JOIN plc_cycle_assessments AS pca
    ON pca.assessment_id = old_sections.assessment_id;

UPDATE assessment_administrations
SET cycle_assessment_id = (
    SELECT pca.cycle_assessment_id
    FROM plc_cycle_assessments AS pca
    WHERE pca.assessment_id = assessment_administrations.assessment_id
    ORDER BY pca.cycle_assessment_id
    LIMIT 1
)
WHERE cycle_assessment_id IS NULL;


-- The current regenerated seed uses 'Complete', while the application
-- consistently treats 'Submitted' as decision-ready evidence.
UPDATE assessment_administrations
SET status = 'Submitted'
WHERE status = 'Complete';

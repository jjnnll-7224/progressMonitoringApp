# PLC Intelligence

Streamlit prototype for standards-centered PLC cycles, CFA analysis, student grouping, intervention planning, and reassessment growth.

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
streamlit run app.py
```

The first run creates `data/plc_demo.db` and seeds an 8th Grade ELA `RI.8.2` scenario.

## Test

```powershell
pytest -q
```

## Current checkpoint

- Multipage navigation and enterprise visual theme
- Normalized demo schema with question-level scores
- Seeded PLC cycle, assessment, students, intervention, and commitment
- Shared mastery scoring and growth service
- Dashboard and PLC Cycle starter pages
- Boundary tests for scoring rules

Next: assessment creation and CFA Data Entry.

# Review pass on patient_requests_all.csv

An independent review of the dataset's labels and sentences against the written rubric.
`patient_requests_all.csv` is untouched. The reviewed version is `patient_requests_fixed.csv`;
every change is listed in `patient_requests_changelog.csv` (request_id, field, old, new, reason).

- 1067 rows in, 1064 out. Deleted only exact copies: 172 (same text as 69), 375 and 889 (blank, same as 16).
- 359 changes: labels (category, department, urgency, clinical_review, more_info_needed),
  30 patient_context rewrites into filed-chart-fact form, and 8 light patient_request edits.
- Rows 334, 448 and 752 (neutropenic patients) were deliberately left as originally labelled.
- Seed rows (ids 1-67) were changed here, unlike the policy in data/README.md. The distribution
  figures in data/README.md describe the original file, not the fixed one.
- The fixed file passes a check that every cell is an allowed value and that department/category
  combinations obey the routing rules. That does not prove each label is correct.

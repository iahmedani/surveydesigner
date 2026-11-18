# Survey generation, validation, preview & publishing

The heart of the product. Highest-risk code: a bug here ships broken forms to the field.

## Pipeline

```
POST /api/generate/  {name, submodules, submodules_order, sub_questions,
                      languages, indicators, survey_type}  + org header
  │  modules/serializers.GenerateXLSFormSerializer
  ▼
modules/views.prepare_validated_artifact()
  ├─ validate_generation_scope()            org scope of every requested id
  ├─ modules/services.SubmoduleCompositionValidator
  │     shared questions across submodules, expression deps not in selection
  ├─ get_xlsx_from_data(as_wb=True) → questions/services/xls_form.XLSForm
  ├─ build_generated_artifact() → GeneratedSurveyArtifact
  │     xlsx bytes, external choice CSVs, sha256 artifact_hash, row_source_map
  └─ questions/services/form_validation.validate_generated_artifact()
        1. validate_codebook_integrity   CODEBOOK_* (choices, names, refs, suffixes…)
        2. pyxform 4.5.0 xls2xform convert (validate=False)   (xml_conversion.py)
        3. validate_xml_compatibility    XML_* / XPATH_* (lxml)
  ▼
ValidationResult {valid, artifact_hash, errors[], warnings[], validator{pyxform, compatibility}}
  invalid → 400 ; infrastructure failure → 503 ; ok → file
```

- **Generate** returns `survey.xlsx`, or `survey.zip` (xlsx + CSVs) when external
  choice files are used. Headers: `X-Survey-Validation-Warnings`, `X-Survey-Artifact-Hash`
  (exposed via CORS).
- **Preview** stores XML (+ CSVs, URLs rewritten) under `previews/<uuid>/` →
  Enketo URL (`settings.ENKETO_PREVIEW_URL`). In DEBUG returns the raw file URL.
- **Upload** publishes the *same* artifact: Kobo (create asset + import — URLs
  hardcoded to `kobo.humanitarianresponse.info`), ONA (project forms endpoint),
  MoDa (also uploads CSVs as metadata). Uses the user's decrypted `UserAPIKey`.
- **Word codebook**: `POST /api/generate-doc/` enqueues `generate_docx` on RQ
  `generate-doc` → `questions/services/doc_conversion.DocConversion` → `Document`;
  client polls `GET ?jobId=`.

**Rule:** generate, preview and upload must all go through
`prepare_validated_artifact()` — never build a workbook and ship it unvalidated.

## XLSForm builder (`questions/services/xls_form.py`)
- Sheets: `survey`, `choices`, `settings`.
- Each module → `begin_group <module>_module`; each submodule →
  `begin_group <submodule>_submodule`; questions by `BaseQuestion.order`;
  repeat sections → `begin_repeat`.
- Sub-questions emitted when selected; `_oth` suffix sub-questions always; suffix_2
  `_oth` when parent suffix selected.
- Appends metadata (start, end, today, deviceid) and hidden reserved
  `sd_metadata_*` fields (generated_by, export_id, exported_at, generator_version).
- Settings: `form_title`, `form_id = surveydesigner_v<ver>_<random>`, `version`.
- Only used choice groups and active choices are emitted.
- `SurveyType.password_protected` → sheets protected with a random password.
- **Translations**: with languages selected, columns become
  `label::<English name> (<code>)` (also hint, constraint_message, required_message);
  English from base fields, others from `*Translation` models. No languages → plain columns.

## Per-submodule question configuration (`questions/services/question_config.py`)
Design: `docs/kb/design/submodule-question-config.md`. `QuestionConfigResolver(submodule_ids,
languages)` bulk-loads `SubmoduleQuestionConfig`s (3 queries) and `resolve(question,
submodule_id)` returns an `EffectiveQuestion` (rule 1: config; rule 2: sub-question with
root-inherited choices uses the root config's list/file; rule 3: shared). Used by:
- `xls_form.py`: every root/sub/repeat-member row (selected and indicator submodules) is
  emitted with effective values; the submodule is passed down explicitly. Choices sheet
  and `external_files` follow the effective list/file. `row_source_map` rows carry
  `"config": {"model": "SubmoduleQuestionConfig", "id", "submodule"}`; form_validation
  copies it into `issue.owner["config"]` and appends a "submodule-specific configuration"
  note to the message (`form_validation.owner_config_note`).
- `modules/services.SubmoduleCompositionValidator`: reads expressions from the effective
  config of the owner's submodule (no extra queries when nothing in scope is customised).
- `/api/submodules/`: `RootQuestionSerializer`/`SubQuestionSerializer` emit effective
  label, choices/choices_file and translations (same JSON shape). Cached 1h like before.
- Health scan model `submodule_question_config` (linted like its shared question).
- Bulk sheet `submodule_question_configs` (export + template + `DataImport`, optional on
  import): one full row per config; blank cell = blank value; translation columns use the
  survey-sheet `field::Language (code)` convention (label, hint, constraint_message).
- Doc codebook builds from the generated XLSX, so it follows automatically.

## Expression linting (`questions/services/expression_validation.py`, `expression_scope.py`)
Hand-written XPath 1.0 parser + `${ref}` resolution: unknown or case-mismatched refs,
repeat context, result types, dependency cycles across calculation/relevant/required/read_only.
Used by admin forms (`ExpressionValidationFormMixin`), bulk edit, bulk import and
the final gate. It also syncs the `*_dependencies` M2Ms on questions.

## Admin survey health (`questions/services/health.py`, `/admin/survey-health/`)
Superuser/Global Admin only. Read-only transaction scanning RootQuestion, SubQuestion,
RepeatSection, Calculation, SubmoduleQuestionConfig in batches (`MAX_BATCH_SIZE=25`, ~2.7s budget), driven by
`questions/static/js/admin/survey_health.js`. Findings have codes, suggestions, edit links.

## Bulk import (`questions/services/questions_import/`)
`DataImport` reads sheets `survey`, `choices`, `suffixes`, `recall_periods`,
`required_groups`, `indicators` and (optional) `submodule_question_configs`; used by `/admin/questions/import/` and change
requests (dry run = `transaction.set_rollback(True)`). Template/exports:
`questions/services/questions_export.py`.

## Changing this code safely
- **pyxform is pinned twice**: `pyproject.toml` and `form_validation.PYXFORM_VERSION`.
  Bump both together and run the full `questions` + `modules` test suites.
- Add a new validation as an issue **code** with layer/severity/owner fields; add a
  test in `apps/questions/tests/test_form_validation.py` that asserts the code.
- Adding an XLSForm column: update `xls_form.py`, translations handling, the export
  template (`questions_export.py`), the importer, and `doc_conversion.py` if it
  should appear in the codebook.
- Tests to run: `poetry run pytest survey_designer/apps/questions survey_designer/apps/modules`.

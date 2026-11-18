# Design: per-submodule question configuration

Status: implemented on branch `feat/submodule-question-config` (2026-11-17), not yet merged.

## Problem
`RootQuestion.submodule` is already M2M, so one question can belong to many
submodules, but its configuration (label, hint, logic, choices, translations) is
global. Content managers need to reuse a question (same unique **name**, same
**type**) in several modules while adapting everything else per submodule.

## Decisions (confirmed with product owner)
| Topic | Decision |
|---|---|
| Scope | Root questions **and** sub-questions |
| Semantics | **Full copy on first edit**: creating a config copies every field + translations from the shared question; afterwards it is independent (later edits to the shared question do not flow into it). Deleting the config reverts the submodule to the shared question. |
| Overridable | Everything except `name` and `type`: label, hint, relevant, constraint, constraint_message, appearance, required, parameters, disabled, read_only, default, choice_filter, calculation, **choice list / choice file**, **label/hint/constraint-message translations** |
| Not overridable | `name`, `type` (incl. a sub-question's suffix/recall period, which define its name and type) |
| Validation | Same rules as the shared question: choice/type consistency (exactly `RootQuestion.clean`'s select / select-from-file rules on the effective type; a non-select type may carry a list, as on the shared question), expression linting (`validate_expression_fields`), and the final generation gate (which validates the generated workbook, so overrides are covered automatically) |
| Who | Anyone allowed to change the **submodule** (org Admin for content exclusive to their org, Global Admin/superuser for any). Read Only: view only. |
| Where | Django admin + bulk import/export (and therefore change requests, which use `DataImport`) |
| One survey | Unchanged: a survey still can't select two submodules that share a question (`SELECTED_SCOPE_SUBMODULE_CONFLICT`), so exactly one configuration applies per emitted question. |

## Data model (`apps/questions/models.py`, migration `0052_submodule_question_config`)
- `SubmoduleQuestionConfig(UserTrackingMixin, TimestampMixin, QuestionFieldsMixin)`:
  `submodule` FK → `modules.Submodule` (`related_name="question_configs"`),
  `base_question` FK → `BaseQuestion` (`related_name="submodule_configs"`, root or sub only),
  `label`, `choices` FK ChoiceGroup (PROTECT), `choices_file` FK ChoiceGroupFile (PROTECT).
  Unique `(submodule, base_question)`. Properties `question`, `question_name`, `type`;
  `clean()` (question belongs to the submodule via its root's `submodule` M2M; choice
  rules on the effective type); `get_xls_type()`; `COPIED_FIELDS`;
  `create_from_question(submodule, base_question, user=None)` = the full copy.
- `SubmoduleQuestionConfigTranslation` (`config.translations`: language, label, hint).
- `SubmoduleQuestionConfigConstraintMessageTranslation` (`config.constraint_translations`).
  The related names deliberately mirror `RootQuestion`/`SubQuestion`.

## Resolution rules (generation, API, validation)
For a question emitted inside submodule **S** (incl. inside a repeat section of S):
1. If a config exists for `(S, question.base_question)` → use the config's fields,
   choices/choices_file and translations; `name` and `type` from the shared question.
2. Else, for a **sub-question** whose choices are inherited from the root (no suffix /
   suffix_2 choice list) and the **root** has a config in S → use the root config's
   choice list (so a sub-question never mixes the root's shared list with a
   customised root). All other fields from the shared sub-question.
3. Else → the shared question, exactly as today.
Indicator-driven submodules follow the same rules (keyed by the submodule being emitted).

## Implementation notes
- Import/export sheet `submodule_question_configs` (separate `choice_list` and `choice_file`
  columns; translation columns like the survey sheet). Every column **present** sets its
  field (blank cell = blank value); an absent column leaves the field as it is (on creation:
  the full copy of the shared question). Translation rows set `created_by` only on creation.
- **Same-workbook limitation**: a config row is validated against the database before the
  workbook's survey sheet is saved, so it can't reference a question that the same workbook
  creates, or a question the same workbook adds to that submodule. The row fails with
  "does not exist" / "is not part of submodule". Upload the question first, then the
  config in a second upload. For change requests this means **two rounds**: one request
  that creates or attaches the question, approved, then a second request with the config
  rows. (A choice list created by the same workbook's choices sheet *can* be referenced.)
- Import authorisation for the config sheet is the same as for the survey sheet
  (`QuestionsImport._validate_organization_permissions`): the submodule's module must
  belong exactly to the declared organizations. There's no per-user object check, so a
  Change Requests (non-Admin) user can submit config rows, and an approver can apply them.
  Who may declare which organizations is decided by the caller: the bulk upload form pins
  an org Admin to their own organization, and change requests are approved by their
  approver.
- Cache: config/translation save/delete invalidate the modules API cache through
  `modules.signals.invalidate_module_cache_on_commit()`. It runs on commit and only
  once per transaction, so a bulk import does one SCAN.
- Validation messages for rows coming from a config name the config and its submodule
  (`row_source_map[...]["config"]`); no new issue codes.
- Org ownership of a ChoiceGroup / ChoiceGroupFile includes configs: the organizations
  of every submodule whose config uses the list count, as well as those of shared questions
  (`organization.utils.get_organizations`). So an org Admin can't edit a list another
  org's survey emits through a config, and can edit a list used only by their own
  configs. Deleting a list used by a config is blocked (`PROTECT`; the admin shows its
  standard "cannot delete" page).

## Implementation split (as built)
- **Admin / permissions / signals** (`questions/admin.py`, `questions/forms.py`,
  `questions/signals.py`, `modules/admin.py`, `organization/*`, `core/permissions.py`).
- **Engine** (`questions/services/question_config.py` [new], `xls_form.py`,
  `modules/services.py`, `modules/serializers.py`/views for `/api/submodules/`,
  `health.py`, `questions_export.py`, `questions_import/`).
- No frontend change expected: `/api/submodules/` returns effective labels/choices.

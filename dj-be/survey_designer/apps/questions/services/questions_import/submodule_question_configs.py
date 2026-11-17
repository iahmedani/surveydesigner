"""Bulk import of per-submodule question configurations.

Sheet ``submodule_question_configs``: one row per ``(submodule, question)``.
A row is a *full* configuration: every column present in the sheet sets its
field, and a blank cell sets a blank value. Translation columns use the same
``<field>::<Language> (<code>)`` convention as the survey sheet; languages
without a column are left untouched (on creation they are copied from the
shared question, see ``SubmoduleQuestionConfig.create_from_question``).

The sheet is optional so older templates keep importing.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from modules.models import Module, Submodule
from questions.models import (
    BaseQuestion,
    ChoiceGroup,
    ChoiceGroupFile,
    RootQuestion,
    SubmoduleQuestionConfig,
    SubmoduleQuestionConfigConstraintMessageTranslation,
    SubmoduleQuestionConfigTranslation,
)
from questions.services.expression_scope import (
    ReferenceScope,
    expression_references,
    load_reference_scope,
    validate_expression_fields,
)
from questions.services.expression_validation import QUESTION_EXPRESSION_FIELDS
from questions.services.questions_import.base import (
    ENGLISH_LANGUAGE_CODE,
    BaseImport,
    QuestionImportException,
)

SHEET_NAME = "submodule_question_configs"
SUBMODULE_COLUMN = "submodule_name"
QUESTION_COLUMN = "question_name"
CHOICE_LIST_COLUMN = "choice_list"
CHOICE_FILE_COLUMN = "choice_file"
# Columns that may carry per-language variants (``label::French (fr)``).
LANGUAGE_FIELDS = ("label", "hint", "constraint_message")
# Plain overridable fields, in sheet order.
PLAIN_FIELDS = (
    "relevant",
    "constraint",
    "appearance",
    "required",
    "parameters",
    "disabled",
    "read_only",
    "default",
    "choice_filter",
    "calculation",
)
REQUIRED_COLUMNS = (SUBMODULE_COLUMN, QUESTION_COLUMN)


def sheet_columns(languages) -> list[str]:
    """Header row of the sheet for ``languages`` [(code, display), ...]."""

    columns = [SUBMODULE_COLUMN, QUESTION_COLUMN]
    for field in LANGUAGE_FIELDS:
        if languages:
            columns.extend(
                f"{field}::{display} ({code})" for code, display in languages
            )
        else:
            columns.append(field)
    columns.extend([CHOICE_LIST_COLUMN, CHOICE_FILE_COLUMN, *PLAIN_FIELDS])
    return columns


class SubmoduleQuestionConfigImport(BaseImport):
    def __init__(self, work_sheet, languages_dict, user, organizations, **kwargs):
        super().__init__(user, **kwargs)
        self.work_sheet = work_sheet
        self.languages_dict = languages_dict
        self.organizations = organizations
        self.columns: dict[str, int] = {}
        # {field: {language_code: column index}}
        self.language_columns: dict[str, dict[str, int]] = {}
        self.cleaned_data: list[dict] = []

    # Columns -----------------------------------------------------------------

    def _language_code(self, header: str, prefix: str, inv_languages: dict) -> str:
        parts = header[len(prefix) :].replace(")", "").split("(")
        parts.append("")
        display, code, *_ = parts
        display, code = display.strip(), code.strip()
        if code in self.languages_dict:
            return code
        if display in inv_languages:
            return inv_languages[display]
        raise QuestionImportException(
            f"Submodule question configurations: language not found for column: {header}"
        )

    def validate_and_set_columns(self):
        inv_languages = {v: k for k, v in self.languages_dict.items()}
        plain = {SUBMODULE_COLUMN, QUESTION_COLUMN, CHOICE_LIST_COLUMN}
        plain.update({CHOICE_FILE_COLUMN, *PLAIN_FIELDS})
        for index, cell in enumerate(self.work_sheet[1]):
            if not cell.value or not isinstance(cell.value, str):
                continue
            header = cell.value.strip()
            lowered = header.lower()
            if lowered in plain:
                self.columns[lowered] = index
                continue
            for field in LANGUAGE_FIELDS:
                prefix = f"{field}::"
                if lowered == field:
                    self.language_columns.setdefault(field, {})[
                        ENGLISH_LANGUAGE_CODE
                    ] = index
                    break
                if lowered.startswith(prefix):
                    code = self._language_code(header, prefix, inv_languages)
                    self.language_columns.setdefault(field, {})[code] = index
                    break
        missing = [column for column in REQUIRED_COLUMNS if column not in self.columns]
        if missing:
            raise QuestionImportException(
                "Incorrect submodule question configurations spreadsheet. "
                f"Missing columns: {', '.join(missing)}."
            )

    def _cell(self, row, index):
        if index is None or index >= len(row):
            return ""
        value = row[index].value
        if value is None:
            return ""
        return str(value).strip()

    # Validation --------------------------------------------------------------

    def _add_error(self, row_number, column, message):
        self.errors.setdefault(row_number, {}).setdefault(column, []).append(
            str(message)
        )

    def _restricted_module_ids(self):
        if not hasattr(self, "_restricted_ids"):
            organization_ids = list(self.organizations.values_list("id", flat=True))
            self._restricted_ids = set(
                Module.objects.exclude_by_organization_ids(
                    organization_ids
                ).values_list("id", flat=True)
            )
        return self._restricted_ids

    def _check_submodule_permission(self, submodule):
        """Same authority as the survey sheet uploading to the submodule's module.

        Mirrors ``QuestionsImport._validate_organization_permissions``: the
        module must belong exactly to the declared organizations. Who may
        declare them is decided by the caller (the bulk upload form pins an
        org Admin to their own organization; a change request is authorised
        by its approver), so no per-user object check is made here.
        """

        if submodule.module_id in self._restricted_module_ids():
            return (
                f"User has no permission to upload content to module "
                f"({submodule.module.name}), because module belongs to other "
                "organization, or more than one organization."
            )
        return None

    def _resolve_choices(self, row_number, data, spreadsheet_choices):
        """Resolve the choice columns present in the sheet.

        ``data["choices"]`` / ``data["choices_file"]`` are only set when the
        corresponding column exists, so an absent column leaves the field as
        it is (copied from the shared question on creation).
        """
        choice_list = data["choice_list_name"]
        choice_file = data["choice_file_name"]
        if CHOICE_LIST_COLUMN in self.columns:
            data["choices"] = None
        if CHOICE_FILE_COLUMN in self.columns:
            data["choices_file"] = None
        if choice_list:
            choice_group = ChoiceGroup.objects.filter(name__iexact=choice_list).first()
            if choice_group is None and choice_list in spreadsheet_choices:
                # Created by the choices sheet of the same upload.
                choice_group = ChoiceGroup(name=choice_list)
            if choice_group is None:
                self._add_error(
                    row_number,
                    CHOICE_LIST_COLUMN,
                    f"Choice list '{choice_list}' does not exist.",
                )
                return False
            data["choices"] = choice_group
        if choice_file:
            choices_file = ChoiceGroupFile.objects.filter(
                name__iexact=choice_file
            ).first()
            if choices_file is None:
                self._add_error(
                    row_number,
                    CHOICE_FILE_COLUMN,
                    f"Choice list with external file '{choice_file}' does not exist.",
                )
                return False
            data["choices_file"] = choices_file
        return True

    def _build_config(self, data, submodule, base_question):
        config = SubmoduleQuestionConfig.objects.filter(
            submodule=submodule, base_question=base_question
        ).first() or SubmoduleQuestionConfig(
            submodule=submodule, base_question=base_question
        )
        if config.pk is None:
            # Validate against what creation will produce: the full copy,
            # then the row's values on top.
            question = base_question.root_question or base_question.sub_question
            if question is not None:
                config.fill_from_question(question)
        self._apply_row(config, data, data.get("choices"))
        return config

    @staticmethod
    def _apply_row(config, data, choices):
        """Set the fields whose columns are present in the sheet."""
        for field, value in data["values"].items():
            setattr(config, field, value)
        if "choices" in data:
            config.choices = choices
        if "choices_file" in data:
            config.choices_file = data["choices_file"]

    def _validate_model(self, row_number, config):
        unsaved_choices = config.choices is not None and config.choices.pk is None
        exclude = ["choices"] if unsaved_choices else None
        try:
            config.full_clean(exclude=exclude)
        except ValidationError as error:
            for field, messages in error.message_dict.items():
                column = {
                    "choices": CHOICE_LIST_COLUMN,
                    "choices_file": CHOICE_FILE_COLUMN,
                    "base_question": QUESTION_COLUMN,
                    "submodule": SUBMODULE_COLUMN,
                    "__all__": QUESTION_COLUMN,
                }.get(field, field)
                for message in messages:
                    self._add_error(row_number, column, message)
            return False
        return True

    def _validate_translations(self, row_number, data):
        valid = True
        for code, (label, hint) in data["translations"].items():
            if hint and not label:
                display = self.languages_dict.get(code, code)
                self._add_error(
                    row_number,
                    f"label::{display} ({code})",
                    "A translated hint needs a translated label in the same language.",
                )
                valid = False
        return valid

    def _validate_expressions(self, rows, spreadsheet_names):
        values_by_row = [
            (
                row_number,
                data,
                {f: data["values"].get(f) or "" for f in QUESTION_EXPRESSION_FIELDS},
            )
            for row_number, data in rows
        ]
        names = set().union(
            *(expression_references(values) for _, _, values in values_by_row)
        )
        scope = load_reference_scope(names)
        # Names created by the survey sheet of the same upload.
        sheet_scope = ReferenceScope()
        sheet_scope.names.update(name for name in spreadsheet_names if name)
        scope.update(sheet_scope)
        for row_number, data, values in values_by_row:
            question = data["question"]
            root = (
                question
                if isinstance(question, RootQuestion)
                else question.root_question
            )
            repeats = set(
                root.base_question.repeat_sections.values_list("name", flat=True)
            )
            issues = validate_expression_fields(
                {"model": "SubmoduleQuestionConfig", "name": question.name},
                values,
                question_type=question.type,
                repeats=repeats,
                scope=scope,
            )
            for issue in issues:
                self._add_error(row_number, issue.field, issue.message)

    def process(self, available_spreadsheet_choices=(), available_spreadsheet_names=()):
        if self.work_sheet is None:
            return
        try:
            self.validate_and_set_columns()
        except QuestionImportException as error:
            self.non_form_errors.append(str(error))
            return

        spreadsheet_choices = set(available_spreadsheet_choices or ())
        seen: dict[tuple[int, int], int] = {}
        valid_rows = []
        for row_number, row in enumerate(self.work_sheet.iter_rows(min_row=2), 2):
            submodule_name = self._cell(row, self.columns.get(SUBMODULE_COLUMN))
            question_name = self._cell(row, self.columns.get(QUESTION_COLUMN))
            if not submodule_name and not question_name:
                continue
            if not submodule_name:
                self._add_error(row_number, SUBMODULE_COLUMN, "This field is required.")
            if not question_name:
                self._add_error(row_number, QUESTION_COLUMN, "This field is required.")
            if not submodule_name or not question_name:
                continue

            submodule = (
                Submodule.objects.select_related("module")
                .filter(name__iexact=submodule_name)
                .first()
            )
            if submodule is None:
                self._add_error(
                    row_number,
                    SUBMODULE_COLUMN,
                    f"Submodule '{submodule_name}' does not exist.",
                )
            base_question = (
                BaseQuestion.objects.filter_by_names([question_name])
                .select_related("root_question", "sub_question")
                .first()
            )
            if base_question is None:
                self._add_error(
                    row_number,
                    QUESTION_COLUMN,
                    f"Question '{question_name}' does not exist. Create the question "
                    "before configuring it for a submodule.",
                )
            if submodule is None or base_question is None:
                continue

            permission_error = self._check_submodule_permission(submodule)
            if permission_error:
                self._add_error(row_number, SUBMODULE_COLUMN, permission_error)
                continue

            key = (submodule.id, base_question.id)
            if key in seen:
                self._add_error(
                    row_number,
                    QUESTION_COLUMN,
                    f"Duplicate configuration for question '{question_name}' in "
                    f"submodule '{submodule_name}' (first at row {seen[key]}).",
                )
                continue
            seen[key] = row_number

            question = base_question.instance
            data = {
                "submodule": submodule,
                "base_question": base_question,
                "question": question,
                "choice_list_name": self._cell(
                    row, self.columns.get(CHOICE_LIST_COLUMN)
                ),
                "choice_file_name": self._cell(
                    row, self.columns.get(CHOICE_FILE_COLUMN)
                ),
                "values": {},
                "translations": {},
                "constraint_translations": {},
            }
            for field in PLAIN_FIELDS:
                if field in self.columns:
                    data["values"][field] = self._cell(row, self.columns[field])
            for field in LANGUAGE_FIELDS:
                for code, index in self.language_columns.get(field, {}).items():
                    value = self._cell(row, index)
                    if code == ENGLISH_LANGUAGE_CODE:
                        data["values"][field] = value
                    elif field == "constraint_message":
                        data["constraint_translations"][code] = value
                    else:
                        label, hint = data["translations"].get(code, (None, None))
                        if field == "label":
                            label = value
                        else:
                            hint = value
                        data["translations"][code] = (label, hint)
            # Keep (label, hint) pairs complete for languages with one column.
            data["translations"] = {
                code: (label or "", hint or "")
                for code, (label, hint) in data["translations"].items()
            }

            ok = self._resolve_choices(row_number, data, spreadsheet_choices)
            ok = self._validate_translations(row_number, data) and ok
            if ok:
                config = self._build_config(data, submodule, base_question)
                if not self._validate_model(row_number, config):
                    continue
                data["exists"] = config.pk is not None
                valid_rows.append((row_number, data))

        self._validate_expressions(valid_rows, available_spreadsheet_names)
        self.cleaned_data = [
            data for row_number, data in valid_rows if row_number not in self.errors
        ]

    # Saving ------------------------------------------------------------------

    @staticmethod
    def _change_name(data):
        return f"{data['submodule'].name} / {data['question'].name}"

    def _apply_translations(self, config, data):
        for code, (label, hint) in data["translations"].items():
            if not label:
                config.translations.filter(language=code).delete()
                continue
            translation, _ = (
                SubmoduleQuestionConfigTranslation.objects.update_or_create(
                    config=config,
                    language=code,
                    defaults={
                        "label": label,
                        "hint": hint,
                        "updated_by": self.user,
                    },
                    create_defaults={
                        "label": label,
                        "hint": hint,
                        **self.user_tracking_kwargs,
                    },
                )
            )
            self.log_addition(translation)
        for code, label in data["constraint_translations"].items():
            if not label:
                config.constraint_translations.filter(language=code).delete()
                continue
            (
                translation,
                _,
            ) = SubmoduleQuestionConfigConstraintMessageTranslation.objects.update_or_create(
                config=config,
                language=code,
                defaults={"label": label, "updated_by": self.user},
                create_defaults={"label": label, **self.user_tracking_kwargs},
            )
            self.log_addition(translation)

    def create(self, created_choices=None, skip_saving=False):
        created_choices = created_choices or {}
        saved = []
        for data in self.cleaned_data:
            name = self._change_name(data)
            if skip_saving:
                self.log_change(
                    SubmoduleQuestionConfig, name, create=not data["exists"]
                )
                continue

            choices = data.get("choices")
            if choices is not None and choices.pk is None:
                choices = created_choices.get(choices.name) or ChoiceGroup.objects.get(
                    name__iexact=choices.name
                )

            config = SubmoduleQuestionConfig.objects.filter(
                submodule=data["submodule"], base_question=data["base_question"]
            ).first()
            created = config is None
            if created:
                # Full copy (fields + translations) first; the row then
                # overrides what it states.
                config = SubmoduleQuestionConfig.create_from_question(
                    data["submodule"], data["base_question"], user=self.user
                )
            self._apply_row(config, data, choices)
            config.updated_by = self.user
            config.full_clean()
            config.save()
            self._apply_translations(config, data)
            self.log_addition(config)
            saved.append(config)
        return saved

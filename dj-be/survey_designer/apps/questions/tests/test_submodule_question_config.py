"""Engine behaviour of per-submodule question configurations.

See docs/kb/design/submodule-question-config.md ("Resolution rules").
"""

import os
import uuid
from io import BytesIO

import pytest
from accounts.const import PermissionGroups
from change_requests.const import StatusType
from change_requests.models import ChangeRequest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from modules.models import Module, Submodule
from openpyxl import load_workbook
from organization.models import Organization
from questions.const import QuestionType
from questions.models import (
    Choice,
    ChoiceGroup,
    ChoiceGroupFile,
    RepeatSection,
    RootQuestion,
    RootQuestionTranslation,
    SubmoduleQuestionConfig,
    SubmoduleQuestionConfigConstraintMessageTranslation,
    SubmoduleQuestionConfigTranslation,
    SubQuestion,
)
from questions.services import (
    DataImport,
    QuestionsExport,
    XLSForm,
    validate_generated_artifact,
)
from questions.services.form_validation import build_generated_artifact
from questions.services.health import MODEL_SPECS, scan_batch
from questions.services.question_config import QuestionConfigResolver
from questions.services.questions_import.submodule_question_configs import SHEET_NAME
from questions.services.workbook import save_virtual_workbook


def _survey_rows(xlsx_bytes):
    workbook = load_workbook(BytesIO(xlsx_bytes))
    sheet = workbook["survey"]
    header = [cell.value for cell in sheet[1]]
    rows = {}
    for values in sheet.iter_rows(min_row=2, values_only=True):
        row = dict(zip(header, values))
        if row.get("name"):
            rows[row["name"]] = row
    return rows


def _choice_lists(xlsx_bytes):
    sheet = load_workbook(BytesIO(xlsx_bytes))["choices"]
    return {row[0] for row in sheet.iter_rows(min_row=2, values_only=True) if row[0]}


def _form(submodule_ids, sub_question_ids=(), languages=None, **kwargs):
    return XLSForm(
        name="config test",
        submodule_ids=list(submodule_ids),
        sub_question_ids=list(sub_question_ids),
        submodules_order=list(submodule_ids),
        languages=languages or [],
        **kwargs,
    )


def _generate(submodule_ids, sub_question_ids=(), languages=None, **kwargs):
    form = _form(submodule_ids, sub_question_ids, languages, **kwargs)
    return form, form.generate()


def _choice_group(name, *codes):
    group = ChoiceGroup.objects.create(name=name)
    for order, code in enumerate(codes, 1):
        Choice.objects.create(
            choice_group=group, name=code, label=f"{name} {code}", order=order
        )
    return group


@pytest.fixture()
def shared_select(submodule_1, submodule_2, choices_1):
    """A select_one question shared by submodule_1 and submodule_2."""

    question = RootQuestion.objects.create(
        name="SharedSelect",
        label="Shared label",
        hint="Shared hint",
        type=QuestionType.SELECT_ONE,
        choices=choices_1,
        constraint_message="Shared constraint message",
    )
    question.submodule.add(submodule_1, submodule_2)
    RootQuestionTranslation.objects.create(
        root_question=question, language="fr", label="Shared FR", hint="Hint FR"
    )
    return question


@pytest.fixture()
def config_choices():
    return _choice_group("ConfigList", "a", "b")


@pytest.fixture()
def shared_config(shared_select, submodule_1, config_choices):
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, shared_select.base_question
    )
    config.label = "Configured label"
    config.hint = "Configured hint"
    config.relevant = "${TestQuestionSub1} > 2"
    config.constraint_message = "Configured constraint message"
    config.choices = config_choices
    config.save()
    config.translations.filter(language="fr").update(
        label="Configured FR", hint="Configured hint FR"
    )
    SubmoduleQuestionConfigConstraintMessageTranslation.objects.create(
        config=config, language="fr", label="Configured CM FR"
    )
    return config


# Resolver -------------------------------------------------------------------


def test_create_from_question_copies_fields_and_translations(
    shared_select, submodule_1
):
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, shared_select.base_question
    )
    assert config.label == "Shared label"
    assert config.choices == shared_select.choices
    assert [(t.language, t.label) for t in config.translations.all()] == [
        ("fr", "Shared FR")
    ]


def test_resolver_bulk_loads_without_per_question_queries(
    shared_config, shared_select, submodule_1, submodule_2
):
    resolver = QuestionConfigResolver([submodule_1.id, submodule_2.id], ["fr"])
    with CaptureQueriesContext(connection) as queries:
        configured = resolver.resolve(shared_select, submodule_1.id)
        shared = resolver.resolve(shared_select, submodule_2.id)
        assert configured.label == "Configured label"
        assert configured.get_xls_type() == "select_one ConfigList"
        assert [t.label for t in configured.translations] == ["Configured FR"]
        assert shared.config is None
    # The configured side is fully prefetched; the shared side reads its own
    # (non-prefetched here) choices FK and translations only.
    assert len(queries) <= 2
    assert configured.name == shared_select.name
    assert configured.type == shared_select.type


# Generation -----------------------------------------------------------------


def test_config_overrides_row_single_language(shared_config, submodule_1):
    form, xlsx = _generate([submodule_1.id])
    row = _survey_rows(xlsx)["SharedSelect"]
    assert row["type"] == "select_one ConfigList"
    assert row["label"] == "Configured label"
    assert row["hint"] == "Configured hint"
    assert row["relevant"] == "${TestQuestionSub1} > 2"
    assert row["constraint_message"] == "Configured constraint message"
    assert "ConfigList" in _choice_lists(xlsx)
    # The shared list is not used by any emitted row any more.
    assert "ChoiceGroup1" not in _choice_lists(xlsx)

    source = next(
        source
        for source in form.row_source_map.values()
        if source and source.get("name") == "SharedSelect"
    )
    assert source["model"] == "RootQuestion"
    assert source["config"]["model"] == "SubmoduleQuestionConfig"
    assert source["config"]["id"] == shared_config.id
    assert source["config"]["submodule"]["name"] == submodule_1.name


def test_config_overrides_row_multi_language(shared_config, submodule_1):
    _, xlsx = _generate([submodule_1.id], languages=["en", "fr"])
    row = _survey_rows(xlsx)["SharedSelect"]
    assert row["label::English (en)"] == "Configured label"
    assert row["hint::English (en)"] == "Configured hint"
    assert row["constraint_message::English (en)"] == "Configured constraint message"
    assert row["label::French (fr)"] == "Configured FR"
    assert row["hint::French (fr)"] == "Configured hint FR"
    assert row["constraint_message::French (fr)"] == "Configured CM FR"
    assert row["type"] == "select_one ConfigList"


def test_same_question_differs_by_selected_submodule(
    shared_config, submodule_1, submodule_2
):
    _, configured = _generate([submodule_1.id])
    _, shared = _generate([submodule_2.id], languages=["en", "fr"])
    assert _survey_rows(configured)["SharedSelect"]["label"] == "Configured label"
    shared_row = _survey_rows(shared)["SharedSelect"]
    assert shared_row["label::English (en)"] == "Shared label"
    assert shared_row["label::French (fr)"] == "Shared FR"
    assert shared_row["type"] == "select_one ChoiceGroup1"
    assert shared_row["relevant"] in (None, "")


def test_sub_question_inherits_root_config_choices_rule_2(
    shared_config, shared_select, submodule_1, submodule_2, recall_period_1
):
    sub_question = SubQuestion.objects.create(
        root_question=shared_select,
        recall_period=recall_period_1,
        label="Sub label",
    )
    _, configured = _generate([submodule_1.id], [sub_question.id])
    row = _survey_rows(configured)[sub_question.name]
    # Rule 2: choices from the root config, everything else shared.
    assert row["type"] == "select_one ConfigList"
    assert row["label"] == "Sub label"

    _, shared = _generate([submodule_2.id], [sub_question.id])
    assert _survey_rows(shared)[sub_question.name]["type"] == "select_one ChoiceGroup1"


def test_sub_question_config_rule_1_wins(
    shared_config, shared_select, submodule_1, recall_period_1
):
    sub_question = SubQuestion.objects.create(
        root_question=shared_select, recall_period=recall_period_1, label="Sub"
    )
    other = _choice_group("SubList", "x")
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, sub_question.base_question
    )
    config.label = "Sub configured"
    config.choices = other
    config.save()
    _, xlsx = _generate([submodule_1.id], [sub_question.id])
    row = _survey_rows(xlsx)[sub_question.name]
    assert row["label"] == "Sub configured"
    assert row["type"] == "select_one SubList"
    assert {"SubList", "ConfigList"} <= _choice_lists(xlsx)


def test_repeat_section_question_uses_submodule_config(
    submodule_1, submodule_2, root_question_4
):
    repeat = RepeatSection.objects.create(
        name="SharedRepeat", label="Shared repeat", repeat_count="2"
    )
    repeat.submodule.add(submodule_1, submodule_2)
    root_question_4.submodule.add(submodule_1)
    repeat.questions.add(root_question_4.base_question)
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_4.base_question
    )
    config.label = "Repeat member configured"
    config.hint = "inside repeat"
    config.save()

    _, configured = _generate([submodule_1.id])
    row = _survey_rows(configured)[root_question_4.name]
    assert row["label"] == "Repeat member configured"
    assert row["hint"] == "inside repeat"

    _, shared = _generate([submodule_2.id])
    assert _survey_rows(shared)[root_question_4.name]["label"] == "Test Question 4"


def test_indicator_submodule_uses_config(
    submodule_1, submodule_2, root_question_3, submodule_3
):
    from modules.models import Indicator

    indicator = Indicator.objects.create(name="CfgIndicator", label="Cfg Indicator")
    indicator.questions.add(root_question_3.base_question)
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_2, root_question_3.base_question
    )
    config.label = "Indicator configured"
    config.save()
    form = XLSForm(
        name="indicator",
        submodule_ids=[submodule_1.id],
        sub_question_ids=[],
        submodules_order=[submodule_1.id, submodule_2.id],
        indicators=[indicator.id],
    )
    rows = _survey_rows(form.generate())
    assert rows[root_question_3.name]["label"] == "Indicator configured"


def test_external_choice_file_override_is_tracked(tmp_path, settings, submodule_1):
    settings.MEDIA_ROOT = tmp_path
    shared_file = ChoiceGroupFile.objects.create(
        name=f"SharedFile_{uuid.uuid4().hex}",
        csv_file=ContentFile(b"name,label\na,A\n", name="shared_cfg.csv"),
    )
    config_file = ChoiceGroupFile.objects.create(
        name=f"ConfigFile_{uuid.uuid4().hex}",
        csv_file=ContentFile(b"name,label\nb,B\n", name="config_cfg.csv"),
    )
    question = RootQuestion.objects.create(
        name="FileQuestion",
        label="File question",
        type=QuestionType.SELECT_ONE_FROM_FILE,
        choices_file=shared_file,
    )
    question.submodule.add(submodule_1)
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, question.base_question
    )
    config.choices_file = config_file
    config.save()

    form, xlsx = _generate([submodule_1.id])
    config_name = os.path.basename(config_file.csv_file.name)
    assert _survey_rows(xlsx)["FileQuestion"]["type"] == (
        f"select_one_from_file {config_name}"
    )
    assert list(form.external_files) == [config_name]
    source = form.external_file_sources[0]
    assert source.file_owner["id"] == config_file.id
    assert source.owner["config"]["id"] == config.id


def test_invalid_override_expression_blocks_final_gate_with_config_owner(
    shared_config, submodule_1
):
    SubmoduleQuestionConfig.objects.filter(pk=shared_config.pk).update(
        relevant="${missing_question} = 1"
    )
    form = _form([submodule_1.id])
    result = validate_generated_artifact(build_generated_artifact(form))
    assert not result.valid
    issue = next(
        issue
        for issue in result.errors
        if issue.code == "CODEBOOK_REFERENCE_NOT_EMITTED"
    )
    assert issue.owner["name"] == "SharedSelect"
    assert issue.owner["config"]["id"] == shared_config.id
    assert "submodule-specific configuration" in issue.message
    assert submodule_1.name in issue.message


def test_generated_config_form_converts_with_pyxform(shared_config, submodule_1):
    SubmoduleQuestionConfig.objects.filter(pk=shared_config.pk).update(relevant="")
    form = _form([submodule_1.id], languages=["en", "fr"])
    artifact = build_generated_artifact(form)
    from questions.services import XMLConversion

    result = validate_generated_artifact(artifact, converter_cls=XMLConversion)
    assert result.valid, [issue.as_dict() for issue in result.errors]


# Health scan ----------------------------------------------------------------


def test_health_scan_lints_configs(shared_config):
    assert "submodule_question_config" in MODEL_SPECS
    SubmoduleQuestionConfig.objects.filter(pk=shared_config.pk).update(
        relevant="${does_not_exist} = 1 and"
    )
    payload = scan_batch("submodule_question_config")
    assert payload["checked_objects"] == 1
    assert payload["items"] == [
        {"id": shared_config.id, "name": "SharedSelect", "is_active": True}
    ]
    codes = {issue["code"] for issue in payload["issues"]}
    assert codes & {"EXPRESSION_SYNTAX_INVALID", "EXPRESSION_INCOMPLETE"}
    assert all(
        issue["model"] == "submodule_question_config" for issue in payload["issues"]
    )
    assert payload["issues"][0]["submodule"] == shared_config.submodule.name


def test_health_scan_clean_config(shared_config):
    payload = scan_batch("submodule_question_config")
    assert payload["issues"] == []
    assert payload["clean"] is True


# Export / import -------------------------------------------------------------


def _config_sheet_rows(xlsx_bytes):
    sheet = load_workbook(BytesIO(xlsx_bytes))[SHEET_NAME]
    header = [cell.value for cell in sheet[1]]
    return header, [
        dict(zip(header, row)) for row in sheet.iter_rows(min_row=2, values_only=True)
    ]


def test_template_has_config_sheet():
    header, rows = _config_sheet_rows(
        QuestionsExport([("en", "English"), ("fr", "French")]).generate_template()
    )
    assert header[:2] == ["submodule_name", "question_name"]
    assert "label::French (fr)" in header
    assert "constraint_message::French (fr)" in header
    assert {"choice_list", "choice_file", "relevant", "calculation"} <= set(header)
    assert rows == []


def test_export_includes_config_rows(shared_config, submodule_1):
    xlsx = QuestionsExport().generate_from_submodules(
        Submodule.objects.filter(id=submodule_1.id)
    )
    header, rows = _config_sheet_rows(xlsx)
    assert len(rows) == 1
    row = rows[0]
    assert row["submodule_name"] == submodule_1.name
    assert row["question_name"] == "SharedSelect"
    assert row["label::English (en)"] == "Configured label"
    assert row["label::French (fr)"] == "Configured FR"
    assert row["hint::French (fr)"] == "Configured hint FR"
    assert row["constraint_message::French (fr)"] == "Configured CM FR"
    assert row["choice_list"] == "ConfigList"
    assert row["relevant"] == "${TestQuestionSub1} > 2"
    # The config's choice list is exported too, so the file round-trips.
    choices = load_workbook(BytesIO(xlsx))["choices"]
    assert "ConfigList" in {
        r[0] for r in choices.iter_rows(min_row=2, values_only=True)
    }


def test_export_scoped_to_exported_submodules(shared_config, submodule_2):
    xlsx = QuestionsExport().generate_from_submodules(
        Submodule.objects.filter(id=submodule_2.id)
    )
    assert _config_sheet_rows(xlsx)[1] == []


def _import_workbook(rows, languages=(("en", "English"), ("fr", "French"))):
    workbook = load_workbook(
        BytesIO(QuestionsExport(list(languages)).generate_template())
    )
    sheet = workbook[SHEET_NAME]
    header = [cell.value for cell in sheet[1]]
    for row_number, values in enumerate(rows, 2):
        for key, value in values.items():
            sheet.cell(row=row_number, column=header.index(key) + 1, value=value)
    return BytesIO(save_virtual_workbook(workbook))


def _data_import(file, user, organizations=None):
    if organizations is None:
        organizations = Organization.objects.filter(
            id__in=Module.objects.values_list("organizations", flat=True)
        )
    data_import = DataImport(file, user, organizations)
    data_import.process()
    return data_import


@pytest.fixture()
def org1_module(submodule_1, organization_1):
    return submodule_1.module


def test_import_creates_config_from_full_copy(
    global_admin, shared_select, submodule_1, config_choices, organization_1
):
    file = _import_workbook(
        [
            {
                "submodule_name": submodule_1.name,
                "question_name": "SharedSelect",
                "label::English (en)": "Imported label",
                "label::French (fr)": "Imported FR",
                "choice_list": "ConfigList",
                "relevant": "${TestQuestionSub1} = 1",
            }
        ]
    )
    data_import = _data_import(
        file, global_admin, Organization.objects.filter(id=organization_1.id)
    )
    assert data_import.is_valid(), data_import.get_errors()
    data_import.create()
    config = SubmoduleQuestionConfig.objects.get(
        submodule=submodule_1, base_question=shared_select.base_question
    )
    assert config.label == "Imported label"
    # Full row: blank cells are blank values.
    assert config.hint == ""
    assert config.constraint_message == ""
    assert config.choices == config_choices
    assert config.relevant == "${TestQuestionSub1} = 1"
    assert {t.language: t.label for t in config.translations.all()} == {
        "fr": "Imported FR"
    }


def test_import_updates_existing_config(
    global_admin, shared_config, submodule_1, organization_1
):
    file = _import_workbook(
        [
            {
                "submodule_name": submodule_1.name,
                "question_name": "SharedSelect",
                "label::English (en)": "Updated",
                "choice_list": "ConfigList",
            }
        ]
    )
    data_import = _data_import(
        file, global_admin, Organization.objects.filter(id=organization_1.id)
    )
    assert data_import.is_valid(), data_import.get_errors()
    data_import.create()
    shared_config.refresh_from_db()
    assert shared_config.label == "Updated"
    assert shared_config.relevant == ""
    # Blank French label removes that translation.
    assert not shared_config.translations.filter(language="fr").exists()
    assert SubmoduleQuestionConfig.objects.count() == 1


def test_import_reports_row_errors(
    global_admin, shared_select, submodule_1, root_question_4, organization_1
):
    file = _import_workbook(
        [
            {"submodule_name": "nope", "question_name": "SharedSelect"},
            {"submodule_name": submodule_1.name, "question_name": "unknown_q"},
            {
                "submodule_name": submodule_1.name,
                "question_name": "SharedSelect",
                "label::English (en)": "x",
                "choice_list": "ChoiceGroup1",
                "relevant": "${unknown_ref} = 1",
            },
            {
                # Question exists but is not in this submodule.
                "submodule_name": submodule_1.name,
                "question_name": root_question_4.name,
                "label::English (en)": "x",
            },
            {
                # select_one without a choice list.
                "submodule_name": submodule_1.name,
                "question_name": "SharedSelect",
                "label::English (en)": "x",
            },
        ]
    )
    data_import = _data_import(
        file, global_admin, Organization.objects.filter(id=organization_1.id)
    )
    assert not data_import.is_valid()
    errors = data_import.get_errors()["Submodule Question Configurations Spreadsheet"][
        "errors"
    ]
    assert "does not exist" in errors[2]["submodule_name"][0]
    assert "does not exist" in errors[3]["question_name"][0]
    assert "unknown_ref" in errors[4]["relevant"][0]
    assert "is not part of submodule" in errors[5]["question_name"][0]
    assert "Duplicate" in errors[6]["question_name"][0]


def test_import_select_without_choice_list_is_rejected(
    global_admin, shared_select, submodule_1, organization_1
):
    file = _import_workbook(
        [
            {
                "submodule_name": submodule_1.name,
                "question_name": "SharedSelect",
                "label::English (en)": "x",
            }
        ]
    )
    data_import = _data_import(
        file, global_admin, Organization.objects.filter(id=organization_1.id)
    )
    errors = data_import.get_errors()["Submodule Question Configurations Spreadsheet"][
        "errors"
    ]
    assert "choices is required" in errors[2]["choice_list"][0]


def test_import_requires_permission_on_submodule(
    shared_select, submodule_2, organization_1, config_choices
):
    from accounts.factories import AdminFactory

    # submodule_2's module is shared by two organizations: an org admin
    # cannot mutate it.
    org_admin = AdminFactory(
        email=f"org-admin-{uuid.uuid4().hex}@example.com", organization=organization_1
    )
    file = _import_workbook(
        [
            {
                "submodule_name": submodule_2.name,
                "question_name": "SharedSelect",
                "label::English (en)": "x",
                "choice_list": "ConfigList",
            }
        ]
    )
    data_import = _data_import(
        file, org_admin, Organization.objects.filter(id=organization_1.id)
    )
    assert not data_import.is_valid()
    errors = data_import.get_errors()["Submodule Question Configurations Spreadsheet"][
        "errors"
    ]
    assert "no permission" in errors[2]["submodule_name"][0]
    assert not SubmoduleQuestionConfig.objects.exists()


def test_import_dry_run_rolls_back(
    global_admin, shared_select, submodule_1, organization_1
):
    file = _import_workbook(
        [
            {
                "submodule_name": submodule_1.name,
                "question_name": "SharedSelect",
                "label::English (en)": "Dry",
                "choice_list": "ChoiceGroup1",
            }
        ]
    )
    with transaction.atomic():
        data_import = _data_import(
            file, global_admin, Organization.objects.filter(id=organization_1.id)
        )
        assert data_import.is_valid(), data_import.get_errors()
        data_import.create(skip_saving=True)
        updates = data_import.get_updates()
        transaction.set_rollback(True)
    assert updates["Submodule Question Configurations Spreadsheet"]["To Create"] == {
        "Submodule Question Configuration": [f"{submodule_1.name} / SharedSelect"]
    }
    assert not SubmoduleQuestionConfig.objects.exists()


def test_import_without_config_sheet_still_works(admin):
    data_import = DataImport(
        "./survey_designer/apps/questions/tests/files/questions.xlsx",
        admin,
        Organization.objects.filter(id=admin.organization.id),
    )
    data_import.process()
    assert data_import.is_valid(), data_import.get_errors()


def test_export_import_round_trip(global_admin, shared_config, submodule_1):
    exported = QuestionsExport().generate_from_submodules(
        Submodule.objects.filter(id=submodule_1.id)
    )
    original = {
        field: getattr(shared_config, field)
        for field in SubmoduleQuestionConfig.COPIED_FIELDS
    }
    original_translations = {
        t.language: (t.label, t.hint) for t in shared_config.translations.all()
    }
    # Change the DB, then re-import the export: it restores the config.
    SubmoduleQuestionConfig.objects.filter(pk=shared_config.pk).update(
        label="changed", relevant=""
    )
    SubmoduleQuestionConfigTranslation.objects.filter(config=shared_config).update(
        label="changed fr"
    )

    workbook = load_workbook(BytesIO(exported))
    data_import = DataImport(
        BytesIO(save_virtual_workbook(workbook)),
        global_admin,
        Organization.objects.filter(
            id__in=submodule_1.module.organizations.values("id")
        ),
    )
    data_import.process()
    config_errors = data_import.get_errors().get(
        "Submodule Question Configurations Spreadsheet"
    )
    assert not config_errors, config_errors
    data_import.submodule_question_configs_import.create({})

    shared_config.refresh_from_db()
    assert {
        field: getattr(shared_config, field)
        for field in SubmoduleQuestionConfig.COPIED_FIELDS
    } == original
    assert {
        t.language: (t.label, t.hint) for t in shared_config.translations.all()
    } == original_translations
    assert shared_config.choices.name == "ConfigList"


# Import: partial sheets, user tracking, change requests ---------------------


def _import_workbook_without(columns, rows):
    """Template workbook whose config sheet lacks ``columns``."""

    workbook = load_workbook(
        BytesIO(
            QuestionsExport([("en", "English"), ("fr", "French")]).generate_template()
        )
    )
    sheet = workbook[SHEET_NAME]
    header = [cell.value for cell in sheet[1]]
    for index in sorted((header.index(c) + 1 for c in columns), reverse=True):
        sheet.delete_cols(index)
    header = [cell.value for cell in sheet[1]]
    assert not set(columns) & set(header)
    for row_number, values in enumerate(rows, 2):
        for key, value in values.items():
            sheet.cell(row=row_number, column=header.index(key) + 1, value=value)
    return BytesIO(save_virtual_workbook(workbook))


def test_import_without_choice_columns_keeps_existing_choices(
    global_admin, shared_config, config_choices, submodule_1, organization_1
):
    file = _import_workbook_without(
        ["choice_list", "choice_file"],
        [
            {
                "submodule_name": submodule_1.name,
                "question_name": "SharedSelect",
                "label::English (en)": "No choice columns",
            }
        ],
    )
    data_import = _data_import(
        file, global_admin, Organization.objects.filter(id=organization_1.id)
    )
    assert data_import.is_valid(), data_import.get_errors()
    data_import.create()
    shared_config.refresh_from_db()
    assert shared_config.label == "No choice columns"
    assert shared_config.choices == config_choices
    assert shared_config.choices_file is None


def test_import_without_choice_columns_creates_config_with_copied_choices(
    global_admin, shared_select, choices_1, submodule_1, organization_1
):
    file = _import_workbook_without(
        [
            "choice_list",
            "choice_file",
            "label::French (fr)",
            "hint::French (fr)",
            "hint::English (en)",
        ],
        [
            {
                "submodule_name": submodule_1.name,
                "question_name": "SharedSelect",
                "label::English (en)": "Created without choice columns",
            }
        ],
    )
    data_import = _data_import(
        file, global_admin, Organization.objects.filter(id=organization_1.id)
    )
    assert data_import.is_valid(), data_import.get_errors()
    data_import.create()
    config = SubmoduleQuestionConfig.objects.get(
        submodule=submodule_1, base_question=shared_select.base_question
    )
    assert config.label == "Created without choice columns"
    # Absent columns keep the full copy of the shared question.
    assert config.choices == choices_1
    assert config.hint == "Shared hint"
    assert config.translations.get(language="fr").label == "Shared FR"


def test_import_update_keeps_translation_created_by(
    admin, global_admin, shared_config, submodule_1, organization_1
):
    shared_config.created_by = admin
    shared_config.save()
    shared_config.translations.update(created_by=admin, updated_by=admin)
    file = _import_workbook(
        [
            {
                "submodule_name": submodule_1.name,
                "question_name": "SharedSelect",
                "label::English (en)": "Updated",
                "label::French (fr)": "Mis a jour",
                "constraint_message::French (fr)": "Nouveau message",
                "choice_list": "ConfigList",
            }
        ]
    )
    data_import = _data_import(
        file, global_admin, Organization.objects.filter(id=organization_1.id)
    )
    assert data_import.is_valid(), data_import.get_errors()
    data_import.create()

    shared_config.refresh_from_db()
    assert shared_config.created_by == admin
    assert shared_config.updated_by == global_admin
    translation = shared_config.translations.get(language="fr")
    assert translation.label == "Mis a jour"
    assert translation.created_by == admin
    assert translation.updated_by == global_admin

    # A translation the row creates records its creator.
    file = _import_workbook(
        [
            {
                "submodule_name": submodule_1.name,
                "question_name": "SharedSelect",
                "label::English (en)": "Updated",
                "label::French (fr)": "Mis a jour",
                "label::Spanish (es)": "Actualizado",
                "choice_list": "ConfigList",
            }
        ],
        languages=(("en", "English"), ("fr", "French"), ("es", "Spanish")),
    )
    data_import = _data_import(
        file, global_admin, Organization.objects.filter(id=organization_1.id)
    )
    assert data_import.is_valid(), data_import.get_errors()
    data_import.create()
    created = shared_config.translations.get(language="es")
    assert created.created_by == global_admin
    assert created.updated_by == global_admin


def _group_user(email, organization, *group_names):
    user = get_user_model().objects.create_user(
        email=email, password="password", is_staff=True
    )
    user.organization = organization
    user.save()
    for group_name in group_names:
        user.groups.add(Group.objects.get_or_create(name=group_name)[0])
    return user


def _config_change_request_file(submodule):
    file = _import_workbook(
        [
            {
                "submodule_name": submodule.name,
                "question_name": "SharedSelect",
                "label::English (en)": "Requested label",
                "choice_list": "ConfigList",
            }
        ]
    )
    return SimpleUploadedFile(
        "config_change_request.xlsx",
        file.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )


def test_change_request_with_config_sheet_by_non_admin_is_submitted_and_applied(
    django_client, shared_select, config_choices, submodule_1, organization_1
):
    submitter = _group_user(
        "cr-submitter@example.invalid",
        organization_1,
        PermissionGroups.CHANGE_REQUESTS,
    )
    assert not submitter.is_admins_member
    django_client.force_login(submitter)
    response = django_client.post(
        reverse("submit_change_request"),
        {
            "file": _config_change_request_file(submodule_1),
            "description": "Configure SharedSelect",
            "organizations": [organization_1.id],
        },
    )
    assert response.status_code == 302, response.context.get("other_errors")
    change_request = ChangeRequest.objects.get(description="Configure SharedSelect")
    assert change_request.created_by == submitter
    # Submission validates only; nothing is applied yet.
    assert not SubmoduleQuestionConfig.objects.exists()

    approver = _group_user(
        "cr-approver@example.invalid",
        organization_1,
        PermissionGroups.CHANGE_REQUESTS,
    )
    assert not approver.is_admins_member
    assert change_request.user_can_approve(approver)
    django_client.force_login(approver)
    response = django_client.post(
        reverse("approve_change_request", args=[change_request.id]),
        {"response": "ok"},
    )
    assert response.status_code == 302, response.context.get("other_errors")
    change_request.refresh_from_db()
    assert change_request.status == StatusType.APPROVED
    config = SubmoduleQuestionConfig.objects.get(
        submodule=submodule_1, base_question=shared_select.base_question
    )
    assert config.label == "Requested label"
    assert config.choices == config_choices
    assert config.created_by == approver


def test_bulk_upload_by_org_admin_on_shared_submodule_is_rejected(
    shared_select, submodule_2, organization_1, config_choices
):

    # submodule_2's module belongs to organization_1 and organization_2; the
    # upload form pins an org Admin to their own organization.
    org_admin = _group_user(
        "bulk-org-admin@example.invalid",
        organization_1,
        PermissionGroups.ADMINS,
        PermissionGroups.BULK_UPLOAD,
    )
    file = _import_workbook(
        [
            {
                "submodule_name": submodule_2.name,
                "question_name": "SharedSelect",
                "label::English (en)": "x",
                "choice_list": "ConfigList",
            }
        ]
    )
    data_import = _data_import(
        file, org_admin, Organization.objects.filter(id=organization_1.id)
    )
    assert not data_import.is_valid()
    errors = data_import.get_errors()["Submodule Question Configurations Spreadsheet"][
        "errors"
    ]
    assert "no permission to upload content to module" in errors[2]["submodule_name"][0]
    assert not SubmoduleQuestionConfig.objects.exists()

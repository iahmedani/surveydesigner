"""API/composition behaviour of per-submodule question configurations."""

import io
import json
import os
import uuid
import zipfile

import pytest
from django.core.cache import cache
from django.core.files.base import ContentFile
from modules.services import SubmoduleCompositionValidator
from questions.const import QuestionType
from questions.models import (
    ChoiceGroupFile,
    RootQuestion,
    SubmoduleQuestionConfig,
    SubmoduleQuestionConfigTranslation,
)
from rest_framework import status


@pytest.fixture(autouse=True)
def selected_organization_header(
    logged_admin_client, api_client_authenticated_admin, organization_1
):
    header_value = str(organization_1.pk)
    logged_admin_client.defaults["HTTP_SURVEY_DESIGNER_ORGANIZATIONS"] = header_value
    api_client_authenticated_admin.credentials(
        HTTP_SURVEY_DESIGNER_ORGANIZATIONS=header_value
    )


def _payload(submodule_ids, **extra):
    return {
        "name": "Config survey",
        "submodules": submodule_ids,
        "submodules_order": submodule_ids,
        "sub_questions": [],
        "languages": [],
        **extra,
    }


@pytest.fixture()
def configured_question(submodule_1, submodule_2):
    question = RootQuestion.objects.create(
        name="ApiShared", label="Shared API label", type=QuestionType.TEXT
    )
    question.submodule.add(submodule_1, submodule_2)
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, question.base_question
    )
    config.label = "Configured API label"
    config.save()
    SubmoduleQuestionConfigTranslation.objects.create(
        config=config, language="es", label="Etiqueta"
    )
    return question, config


def test_generate_blocks_invalid_override_with_config_source(
    logged_admin_client, submodule_1, configured_question
):
    question, config = configured_question
    SubmoduleQuestionConfig.objects.filter(pk=config.pk).update(
        relevant="${not_in_survey} = 1"
    )

    response = logged_admin_client.post(
        "/api/generate/",
        json.dumps(_payload([submodule_1.id])),
        content_type="application/json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    body = response.json()
    issue = next(
        error for error in body["errors"] if error["owner"].get("name") == question.name
    )
    assert issue["code"] in {
        "SELECTED_SCOPE_DEPENDENCY_UNRESOLVED",
        "CODEBOOK_REFERENCE_NOT_EMITTED",
    }
    assert issue["owner"]["config"]["model"] == "SubmoduleQuestionConfig"
    assert issue["owner"]["config"]["id"] == config.id
    assert "submodule-specific configuration" in issue["message"]


def test_generate_uses_config_for_selected_submodule_only(
    logged_admin_client, submodule_1, configured_question
):
    from modules.models import Submodule
    from openpyxl import load_workbook

    # The shared fixture submodule_2 has a space in its name (not a valid
    # group name), so use a second valid submodule sharing the question.
    second = Submodule.objects.create(
        name="ConfigSecond", label="Config second", module=submodule_1.module
    )
    configured_question[0].submodule.add(second)

    def labels(submodule):
        response = logged_admin_client.post(
            "/api/generate/",
            json.dumps(_payload([submodule.id])),
            content_type="application/json",
        )
        assert response.status_code == 200, response.content
        sheet = load_workbook(io.BytesIO(response.content))["survey"]
        header = [cell.value for cell in sheet[1]]
        return {
            row[header.index("name")]: row[header.index("label")]
            for row in sheet.iter_rows(min_row=2, values_only=True)
        }

    assert labels(submodule_1)["ApiShared"] == "Configured API label"
    assert labels(second)["ApiShared"] == "Shared API label"


def test_generate_zip_contains_config_choice_file(
    logged_admin_client, submodule_1, tmp_path, settings
):
    settings.MEDIA_ROOT = tmp_path
    shared_file = ChoiceGroupFile.objects.create(
        name=f"ApiShared_{uuid.uuid4().hex}",
        csv_file=ContentFile(b"name,label\na,A\n", name="api_shared.csv"),
    )
    config_file = ChoiceGroupFile.objects.create(
        name=f"ApiConfig_{uuid.uuid4().hex}",
        csv_file=ContentFile(b"name,label\nb,B\n", name="api_config.csv"),
    )
    question = RootQuestion.objects.create(
        name="ApiFile",
        label="API file",
        type=QuestionType.SELECT_ONE_FROM_FILE,
        choices_file=shared_file,
    )
    question.submodule.add(submodule_1)
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, question.base_question
    )
    config.choices_file = config_file
    config.save()

    response = logged_admin_client.post(
        "/api/generate/",
        json.dumps(_payload([submodule_1.id])),
        content_type="application/json",
    )

    assert response.status_code == 200, response.content
    archive = zipfile.ZipFile(
        io.BytesIO(b"".join(response.streaming_content))
        if response.streaming
        else io.BytesIO(response.content)
    )
    names = {os.path.basename(name) for name in archive.namelist()}
    assert os.path.basename(config_file.csv_file.name) in names
    assert os.path.basename(shared_file.csv_file.name) not in names


def test_composition_validator_reads_config_references(
    submodule_1, submodule_2, configured_question, root_question_4
):
    _, config = configured_question
    config.relevant = f"${{{root_question_4.name}}} > 1"
    config.save()

    issues = SubmoduleCompositionValidator(
        [submodule_1.id], [], [submodule_1.id, submodule_2.id]
    ).validate()
    issue = next(
        issue
        for issue in issues
        if issue.code == "SELECTED_SCOPE_DEPENDENCY_NOT_EMITTED"
    )
    assert issue.owner["name"] == "ApiShared"
    assert issue.owner["config"]["id"] == config.id
    assert issue.dependency["name"] == root_question_4.name

    # The shared question (used by submodule_2) has no such reference.
    shared_issues = SubmoduleCompositionValidator(
        [submodule_2.id], [], [submodule_1.id, submodule_2.id]
    ).validate()
    assert not [
        issue for issue in shared_issues if issue.owner.get("name") == "ApiShared"
    ]


def test_composition_validator_config_can_remove_reference(
    submodule_1, submodule_2, configured_question, root_question_4
):
    question, config = configured_question
    question.relevant = f"${{{root_question_4.name}}} > 1"
    question.save()
    config.relevant = ""
    config.save()

    issues = SubmoduleCompositionValidator(
        [submodule_1.id], [], [submodule_1.id, submodule_2.id]
    ).validate()
    assert not [issue for issue in issues if issue.owner.get("name") == "ApiShared"]


def test_composition_conflict_unchanged(submodule_1, submodule_2, configured_question):
    issues = SubmoduleCompositionValidator(
        [submodule_1.id, submodule_2.id], [], [submodule_1.id, submodule_2.id]
    ).validate()
    assert "SELECTED_SCOPE_SUBMODULE_CONFLICT" in {issue.code for issue in issues}


def test_submodules_api_returns_effective_values(
    api_client_authenticated_admin,
    submodule_1,
    submodule_2,
    configured_question,
    organization_1,
):
    cache.clear()
    response = api_client_authenticated_admin.get(
        f"/api/submodules/?submodule_ids={submodule_1.id},{submodule_2.id}"
    )
    assert response.status_code == 200
    by_submodule = {
        item["id"]: {q["name"]: q for q in item["root_questions"]}
        for item in response.json()
    }
    configured = by_submodule[submodule_1.id]["ApiShared"]
    shared = by_submodule[submodule_2.id]["ApiShared"]
    assert configured["label"] == "Configured API label"
    assert [t["language"] for t in configured["translations"]] == ["es"]
    assert shared["label"] == "Shared API label"
    assert shared["translations"] == []
    # Identical JSON shape.
    assert set(configured) == set(shared)

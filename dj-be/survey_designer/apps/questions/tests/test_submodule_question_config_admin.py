"""Per-submodule question configuration: model copy, admin and permissions."""

import uuid

import pytest
from accounts.const import PermissionGroups
from core.permissions import AdminPermissions, ReadOnlyPermissions
from core.utils import get_model_admin_base_url
from django.contrib.admin import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import ProtectedError
from django.urls import reverse
from modules.models import Module, Submodule
from organization.permissions import can_mutate_object
from organization.utils import get_organizations
from questions.admin import SubmoduleQuestionConfigAdmin
from questions.const import QuestionType
from questions.models import (
    ChoiceGroup,
    ChoiceGroupFile,
    RootQuestion,
    RootQuestionConstraintMessageTranslation,
    SubmoduleQuestionConfig,
    SubmoduleQuestionConfigConstraintMessageTranslation,
    SubmoduleQuestionConfigTranslation,
    SubQuestion,
    SubQuestionTranslation,
)

pytestmark = pytest.mark.django_db

CONFIG_FIELDS = (
    "label",
    "hint",
    "relevant",
    "constraint",
    "constraint_message",
    "appearance",
    "required",
    "disabled",
    "read_only",
    "default",
    "choice_filter",
    "calculation",
    "parameters",
)


def _user(email, group_name, permissions_cls, organization=None):
    group, _ = Group.objects.get_or_create(name=group_name)
    permissions_cls().set_permissions(group)
    user = get_user_model().objects.create_user(
        email=email, password="password", is_staff=True
    )
    user.organization = organization
    user.save()
    user.groups.add(group)
    return user


@pytest.fixture
def org_admin(organization_1):
    return _user(
        "org-admin@example.invalid",
        PermissionGroups.ADMINS,
        AdminPermissions,
        organization_1,
    )


@pytest.fixture
def read_only_user(organization_1):
    return _user(
        "read-only@example.invalid",
        PermissionGroups.READ_ONLY,
        ReadOnlyPermissions,
        organization_1,
    )


@pytest.fixture
def foreign_submodule(organization_2):
    module = Module.objects.create(name="ForeignModule", label="Foreign module")
    module.organizations.set([organization_2])
    return Submodule.objects.create(
        name="ForeignSubmodule", label="Foreign submodule", module=module
    )


@pytest.fixture
def choice_file():
    return ChoiceGroupFile.objects.create(
        name="ConfigChoiceFile",
        csv_file=SimpleUploadedFile(
            "config_choice_file.csv", b"name,label\none,One\n", content_type="text/csv"
        ),
    )


def _config_payload(config, **overrides):
    payload = {field: getattr(config, field) or "" for field in CONFIG_FIELDS}
    payload["choices"] = str(config.choices_id or "")
    payload["choices_file"] = str(config.choices_file_id or "")
    for prefix in ("translations", "constraint_translations"):
        payload.update(
            {
                f"{prefix}-TOTAL_FORMS": "0",
                f"{prefix}-INITIAL_FORMS": "0",
                f"{prefix}-MIN_NUM_FORMS": "0",
                f"{prefix}-MAX_NUM_FORMS": "1000",
            }
        )
    payload.update(overrides)
    return payload


# --- create_from_question ---------------------------------------------------


def test_create_from_root_question_copies_fields_and_translations(
    submodule_1, root_question_1, admin
):
    root_question_1.hint = "Hint ${TestQuestionSub1}"
    root_question_1.relevant = "${TestQuestionSub1} > 0"
    root_question_1.constraint = ". > 0"
    root_question_1.constraint_message = "Must be positive"
    root_question_1.save()
    RootQuestionConstraintMessageTranslation.objects.create(
        root_question=root_question_1, language="fr", label="Positif"
    )

    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_1.base_question, user=admin
    )

    for field in SubmoduleQuestionConfig.COPIED_FIELDS:
        assert getattr(config, field) == getattr(root_question_1, field)
    assert config.question == root_question_1
    assert config.question_name == "TestQuestion1"
    assert config.type == QuestionType.INTEGER
    assert config.created_by == admin
    assert list(config.translations.values_list("language", "label")) == [
        ("fr", "Test Question 1 PL")
    ]
    assert list(config.constraint_translations.values_list("language", "label")) == [
        ("fr", "Positif")
    ]

    # Independent afterwards.
    root_question_1.label = "Changed shared label"
    root_question_1.save()
    config.refresh_from_db()
    assert config.label == "Test Question 1"


def test_create_from_sub_question_copies_choices_inherited_from_root(
    submodule_1, root_question_2, recall_period_1, choices_1
):
    sub_question = SubQuestion.objects.create(
        root_question=root_question_2, recall_period=recall_period_1, label="Sub"
    )
    SubQuestionTranslation.objects.create(
        sub_question=sub_question, language="fr", label="Sous"
    )

    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, sub_question.base_question
    )

    assert config.type == QuestionType.SELECT_MULTIPLE
    assert config.choices == choices_1
    assert config.choices_file is None
    assert config.label == "Sub"
    assert list(config.translations.values_list("label", flat=True)) == ["Sous"]


def test_create_from_file_question_copies_choice_file(submodule_1, choice_file):
    question = RootQuestion.objects.create(
        name="FileQuestion",
        label="File question",
        type=QuestionType.SELECT_ONE_FROM_FILE,
        choices_file=choice_file,
    )
    question.submodule.add(submodule_1)

    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, question.base_question
    )

    assert config.choices_file == choice_file
    assert config.choices is None
    assert config.get_xls_type() == (
        f"select_one_from_file {config.choices_file.csv_file.name.split('/')[-1]}"
    )


# --- clean() rules ----------------------------------------------------------


def test_clean_rejects_question_outside_submodule(submodule_2, root_question_1):
    with pytest.raises(ValidationError) as exc:
        SubmoduleQuestionConfig.create_from_question(
            submodule_2, root_question_1.base_question
        )
    assert "base_question" in exc.value.message_dict


def test_clean_rejects_repeat_section_base_question(submodule_1, repeat_section_1):
    config = SubmoduleQuestionConfig(
        submodule=submodule_1,
        base_question=repeat_section_1.base_question,
        label="x",
    )
    with pytest.raises(ValidationError) as exc:
        config.full_clean()
    assert "base_question" in exc.value.message_dict
    with pytest.raises(ValidationError):
        SubmoduleQuestionConfig.create_from_question(
            submodule_1, repeat_section_1.base_question
        )


def test_clean_choice_type_rules(submodule_1, root_question_1, root_question_2):
    select_config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_2.base_question
    )
    select_config.choices = None
    with pytest.raises(ValidationError) as exc:
        select_config.full_clean()
    assert "choices" in exc.value.message_dict

    # Like RootQuestion.clean(), a non-select type may carry a choice list.
    integer_config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_1.base_question
    )
    integer_config.choices = root_question_2.choices
    integer_config.full_clean()


def test_non_select_question_with_choices_can_be_customised(submodule_1, choices_1):
    # A valid shared question: RootQuestion.clean() accepts choices on rank.
    question = RootQuestion(
        name="RankQuestion", label="Rank", type=QuestionType.RANK, choices=choices_1
    )
    question.full_clean()
    question.save()
    question.submodule.add(submodule_1)

    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, question.base_question
    )

    assert config.choices == choices_1
    config.label = "Customised rank"
    config.full_clean()


def test_clean_rejects_choices_and_file_together(
    submodule_1, root_question_2, choice_file
):
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_2.base_question
    )
    config.choices_file = choice_file
    with pytest.raises(ValidationError) as exc:
        config.full_clean()
    assert "choices_file" in exc.value.message_dict


# --- organizations / permissions -------------------------------------------


def test_get_organizations_resolves_to_submodule_organizations(
    submodule_1,
    submodule_2,
    root_question_1,
    root_question_3,
    organization_1,
    organization_2,
):
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_1.base_question
    )
    assert list(get_organizations(config)) == [organization_1]
    translation = config.translations.get()
    assert list(get_organizations(translation)) == [organization_1]

    shared = SubmoduleQuestionConfig.create_from_question(
        submodule_2, root_question_3.base_question
    )
    assert set(get_organizations(shared)) == {organization_1, organization_2}
    assert get_organizations(SubmoduleQuestionConfig()).count() == 0


def test_org_admin_permissions_follow_submodule_ownership(
    org_admin,
    submodule_1,
    submodule_2,
    foreign_submodule,
    root_question_1,
    root_question_3,
    request_factory,
):
    foreign_question = RootQuestion.objects.create(
        name="ForeignQuestion", label="Foreign", type=QuestionType.INTEGER
    )
    foreign_question.submodule.add(foreign_submodule)
    own = SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_1.base_question
    )
    shared = SubmoduleQuestionConfig.create_from_question(
        submodule_2, root_question_3.base_question
    )
    foreign = SubmoduleQuestionConfig.create_from_question(
        foreign_submodule, foreign_question.base_question
    )

    assert can_mutate_object(org_admin, own)
    assert not can_mutate_object(org_admin, shared)
    assert not can_mutate_object(org_admin, foreign)
    assert org_admin.has_perm("questions.change_submodulequestionconfig", own)
    assert not org_admin.has_perm("questions.change_submodulequestionconfig", shared)
    assert not org_admin.has_perm("questions.delete_submodulequestionconfig", foreign)

    model_admin = SubmoduleQuestionConfigAdmin(SubmoduleQuestionConfig, AdminSite())
    request = request_factory.get("/")
    request.user = org_admin
    assert model_admin.has_add_permission(request)
    assert model_admin.has_view_permission(request, shared)
    assert model_admin.has_change_permission(request, own)
    assert model_admin.has_delete_permission(request, own)
    assert not model_admin.has_change_permission(request, shared)
    assert not model_admin.has_delete_permission(request, foreign)


def test_global_admin_and_read_only_permissions(
    global_admin, read_only_user, submodule_2, root_question_3, request_factory
):
    shared = SubmoduleQuestionConfig.create_from_question(
        submodule_2, root_question_3.base_question
    )
    model_admin = SubmoduleQuestionConfigAdmin(SubmoduleQuestionConfig, AdminSite())

    request = request_factory.get("/")
    request.user = global_admin
    assert model_admin.has_change_permission(request, shared)
    assert model_admin.has_delete_permission(request, shared)

    request.user = read_only_user
    assert model_admin.has_view_permission(request, shared)
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request, shared)
    assert not model_admin.has_delete_permission(request, shared)


def test_group_permission_maps_include_config_models():
    for permissions_cls, actions in (
        (AdminPermissions, {"add", "change", "delete", "view"}),
        (ReadOnlyPermissions, {"view"}),
    ):
        codenames = {p.codename for p in permissions_cls().get_permissions()}
        for model in (
            "submodulequestionconfig",
            "submodulequestionconfigtranslation",
            "submodulequestionconfigconstraintmessagetranslation",
        ):
            assert {f"{action}_{model}" for action in actions} <= codenames
        if permissions_cls is ReadOnlyPermissions:
            assert "change_submodulequestionconfig" not in codenames


# --- admin views ------------------------------------------------------------


def test_add_view_creates_full_copy_and_redirects_to_change(
    logged_admin_client, submodule_1, root_question_1
):
    url = get_model_admin_base_url(SubmoduleQuestionConfig, "_add")
    response = logged_admin_client.get(
        url,
        {
            "submodule": submodule_1.id,
            "base_question": root_question_1.base_question.id,
        },
    )
    assert response.status_code == 200
    assert "label" not in response.context["adminform"].form.fields

    response = logged_admin_client.post(
        url,
        {
            "submodule": submodule_1.id,
            "base_question": root_question_1.base_question.id,
            "_save": "Save",
        },
    )
    config = SubmoduleQuestionConfig.objects.get()
    assert response.status_code == 302
    assert response.url == get_model_admin_base_url(
        SubmoduleQuestionConfig, "_change", [config.id]
    )
    assert config.label == root_question_1.label
    assert config.translations.count() == 1

    response = logged_admin_client.get(response.url)
    assert response.status_code == 200
    content = response.content.decode()
    assert "TestQuestion1" in content
    assert QuestionType.INTEGER in content
    assert "Test Question 1 PL" in content


def test_add_view_duplicate_and_invalid_question_are_form_errors(
    logged_admin_client, submodule_1, submodule_2, root_question_1, repeat_section_1
):
    SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_1.base_question
    )
    url = get_model_admin_base_url(SubmoduleQuestionConfig, "_add")

    for submodule, base_question in (
        (submodule_1, root_question_1.base_question),  # duplicate
        (submodule_2, root_question_1.base_question),  # not in submodule
        (submodule_1, repeat_section_1.base_question),  # repeat section
    ):
        response = logged_admin_client.post(
            url, {"submodule": submodule.id, "base_question": base_question.id}
        )
        assert response.status_code == 200
        assert response.context["adminform"].form.errors
    assert SubmoduleQuestionConfig.objects.count() == 1


def test_add_view_non_field_model_errors_do_not_crash(
    logged_admin_client, submodule_1, root_question_2
):
    # A shared select question without a choice list cannot be copied.
    RootQuestion.objects.filter(pk=root_question_2.pk).update(choices=None)
    response = logged_admin_client.post(
        get_model_admin_base_url(SubmoduleQuestionConfig, "_add"),
        {
            "submodule": submodule_1.id,
            "base_question": root_question_2.base_question.id,
        },
    )
    assert response.status_code == 200
    assert response.context["adminform"].form.non_field_errors()
    assert not SubmoduleQuestionConfig.objects.exists()


def test_change_view_saves_overrides(logged_admin_client, submodule_1, root_question_1):
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_1.base_question
    )
    url = get_model_admin_base_url(SubmoduleQuestionConfig, "_change", [config.id])
    response = logged_admin_client.post(
        url,
        _config_payload(
            config,
            label="Submodule label",
            relevant="${TestQuestionSub1} > 1",
            **{
                "translations-TOTAL_FORMS": "1",
                "translations-0-language": "es",
                "translations-0-label": "Etiqueta",
                "translations-0-hint": "",
            },
        ),
    )
    assert response.status_code == 302, response.context["adminform"].form.errors
    config.refresh_from_db()
    assert config.label == "Submodule label"
    assert config.relevant == "${TestQuestionSub1} > 1"
    assert config.translations.filter(language="es", label="Etiqueta").exists()
    root_question_1.refresh_from_db()
    assert root_question_1.label == "Test Question 1"


def test_change_view_surfaces_expression_lint_errors(
    logged_admin_client, submodule_1, root_question_1
):
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_1.base_question
    )
    url = get_model_admin_base_url(SubmoduleQuestionConfig, "_change", [config.id])
    response = logged_admin_client.post(
        url, _config_payload(config, relevant="${DoesNotExist} > 1")
    )
    assert response.status_code == 200
    errors = response.context["adminform"].form.errors
    assert "relevant" in errors
    assert "DoesNotExist" in str(errors["relevant"])
    config.refresh_from_db()
    assert config.relevant == ""


def test_change_view_lints_as_the_shared_question(
    logged_admin_client, submodule_1, root_question_1
):
    # ``.`` and a reference to the question itself behave as on the shared
    # question (owner = RootQuestion/TestQuestion1), not as an unknown name.
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_1.base_question
    )
    url = get_model_admin_base_url(SubmoduleQuestionConfig, "_change", [config.id])
    response = logged_admin_client.post(
        url, _config_payload(config, constraint="${TestQuestion1} > 0")
    )
    assert response.status_code == 302, response.context["adminform"].form.errors


def test_change_view_choice_rules_are_form_errors(
    logged_admin_client, submodule_1, root_question_2
):
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_2.base_question
    )
    url = get_model_admin_base_url(SubmoduleQuestionConfig, "_change", [config.id])
    response = logged_admin_client.post(url, _config_payload(config, choices=""))
    assert response.status_code == 200
    assert "choices" in response.context["adminform"].form.errors


def test_org_admin_cannot_add_config_to_shared_submodule(
    django_client, org_admin, submodule_1, submodule_2, root_question_1, root_question_3
):
    django_client.force_login(org_admin)
    url = get_model_admin_base_url(SubmoduleQuestionConfig, "_add")

    response = django_client.post(
        url,
        {
            "submodule": submodule_2.id,
            "base_question": root_question_3.base_question.id,
        },
    )
    assert response.status_code == 200
    assert "submodule" in response.context["adminform"].form.errors
    assert not SubmoduleQuestionConfig.objects.exists()

    response = django_client.post(
        url,
        {
            "submodule": submodule_1.id,
            "base_question": root_question_1.base_question.id,
        },
    )
    assert response.status_code == 302
    assert SubmoduleQuestionConfig.objects.filter(submodule=submodule_1).exists()


def test_org_admin_cannot_change_or_delete_shared_config(
    django_client, org_admin, submodule_2, root_question_3
):
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_2, root_question_3.base_question
    )
    django_client.force_login(org_admin)
    url = get_model_admin_base_url(SubmoduleQuestionConfig, "_change", [config.id])
    response = django_client.post(url, _config_payload(config, label="Hijack"))
    assert response.status_code == 403
    config.refresh_from_db()
    assert config.label == "Test Question 3"

    delete_url = get_model_admin_base_url(
        SubmoduleQuestionConfig, "_delete", [config.id]
    )
    response = django_client.post(delete_url, {"post": "yes"})
    assert response.status_code == 403
    assert SubmoduleQuestionConfig.objects.filter(pk=config.pk).exists()


def test_read_only_user_can_view_but_not_add(
    django_client, read_only_user, submodule_1, root_question_1
):
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_1.base_question
    )
    django_client.force_login(read_only_user)
    assert (
        django_client.get(
            get_model_admin_base_url(SubmoduleQuestionConfig, "_changelist")
        ).status_code
        == 200
    )
    response = django_client.get(
        get_model_admin_base_url(SubmoduleQuestionConfig, "_change", [config.id])
    )
    assert response.status_code == 200
    assert "TestQuestion1" in response.content.decode()
    assert (
        django_client.get(
            get_model_admin_base_url(SubmoduleQuestionConfig, "_add")
        ).status_code
        == 403
    )


def test_changelist_search_and_filter(
    logged_admin_client, submodule_1, root_question_1, root_question_2
):
    SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_1.base_question
    )
    SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_2.base_question
    )
    url = get_model_admin_base_url(SubmoduleQuestionConfig, "_changelist")
    response = logged_admin_client.get(url, {"q": "testquestion2"})
    assert response.status_code == 200
    assert list(response.context["cl"].result_list.values_list("pk", flat=True)) == [
        SubmoduleQuestionConfig.objects.get(
            base_question=root_question_2.base_question
        ).pk
    ]
    response = logged_admin_client.get(url, {"submodule__id__exact": submodule_1.id})
    assert response.status_code == 200
    assert response.context["cl"].result_count == 2


def test_base_question_autocomplete_excludes_repeat_sections(
    logged_admin_client, root_question_1, repeat_section_1
):
    response = logged_admin_client.get(
        "/admin/autocomplete/",
        {
            "app_label": "questions",
            "model_name": "submodulequestionconfig",
            "field_name": "base_question",
            "term": "",
        },
    )
    assert response.status_code == 200
    ids = {int(result["id"]) for result in response.json()["results"]}
    assert root_question_1.base_question.id in ids
    assert repeat_section_1.base_question.id not in ids


def test_submodule_autocomplete_offers_only_mutable_submodules(
    django_client, org_admin, submodule_1, submodule_2
):
    django_client.force_login(org_admin)
    response = django_client.get(
        "/admin/autocomplete/",
        {
            "app_label": "questions",
            "model_name": "submodulequestionconfig",
            "field_name": "submodule",
            "term": "",
        },
    )
    assert response.status_code == 200
    ids = {int(result["id"]) for result in response.json()["results"]}
    assert ids == {submodule_1.id}


def test_entry_point_links_on_submodule_and_question_admins(
    logged_admin_client, submodule_1, root_question_1, sub_question_2
):
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_1.base_question
    )
    change_url = get_model_admin_base_url(
        SubmoduleQuestionConfig, "_change", [config.id]
    )
    add_url = get_model_admin_base_url(SubmoduleQuestionConfig, "_add")

    content = logged_admin_client.get(
        get_model_admin_base_url(Submodule, "_change", [submodule_1.id])
    ).content.decode()
    assert change_url in content
    assert f"{add_url}?submodule={submodule_1.id}" in content

    content = logged_admin_client.get(
        get_model_admin_base_url(RootQuestion, "_change", [root_question_1.id])
    ).content.decode()
    assert change_url in content

    content = logged_admin_client.get(
        get_model_admin_base_url(SubQuestion, "_change", [sub_question_2.id])
    ).content.decode()
    # The admin HTML is minified, so check the query parts separately.
    assert f"{add_url}?submodule={submodule_1.id}" in content
    assert f"base_question={sub_question_2.base_question.id}" in content


def test_cascade_and_translation_models_are_registered(submodule_1, root_question_1):
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_1.base_question
    )
    SubmoduleQuestionConfigConstraintMessageTranslation.objects.create(
        config=config, language="fr", label="Msg"
    )
    config.delete()
    assert not SubmoduleQuestionConfigTranslation.objects.exists()
    assert not SubmoduleQuestionConfigConstraintMessageTranslation.objects.exists()


# --- integrations: survey health, submodules API cache ---------------------


def test_non_superuser_global_admin_can_open_survey_health(django_client):
    # Global Admins get model permissions through the Admins group, which the
    # "Assign global admin role" action always adds as well.
    user = _user(
        "global-admin@example.invalid", PermissionGroups.ADMINS, AdminPermissions
    )
    user.groups.add(Group.objects.get_or_create(name=PermissionGroups.GLOBAL_ADMINS)[0])
    assert not user.is_superuser
    assert user.has_perm("questions.view_submodulequestionconfig")
    django_client.force_login(user)
    assert django_client.get(reverse("survey-health")).status_code == 200


def test_config_label_edit_is_visible_on_next_submodules_get(
    api_client_authenticated,
    submodule_1,
    root_question_1,
    organization_1,
    django_capture_on_commit_callbacks,
):
    # Invalidation runs on commit; the test runs inside a transaction, so each
    # "transaction" below is simulated by capturing and running its callbacks.
    with django_capture_on_commit_callbacks(execute=True):
        config = SubmoduleQuestionConfig.create_from_question(
            submodule_1, root_question_1.base_question
        )
    # A unique query string keeps other runs' cached pages out of this test.
    url = f"/api/submodules/?submodule_ids={submodule_1.id}&_t={uuid.uuid4().hex}"
    header = {"HTTP_SURVEY_DESIGNER_ORGANIZATIONS": str(organization_1.id)}

    first = api_client_authenticated.get(url, **header)
    assert first.status_code == 200
    assert "Test Question 1" in first.content.decode()

    config.label = "Configured label for submodule"
    with django_capture_on_commit_callbacks(execute=True):
        config.save()

    second = api_client_authenticated.get(url, **header)
    assert second.status_code == 200
    assert "Configured label for submodule" in second.content.decode()


# --- choice list ownership (configs count) ----------------------------------


def _foreign_admin(organization_2):
    return _user(
        "foreign-admin@example.invalid",
        PermissionGroups.ADMINS,
        AdminPermissions,
        organization_2,
    )


def _choice_file(name):
    return ChoiceGroupFile.objects.create(
        name=name,
        csv_file=SimpleUploadedFile(
            f"{name}.csv", b"name,label\none,One\n", content_type="text/csv"
        ),
    )


def _file_question(name, submodule, choices_file):
    question = RootQuestion.objects.create(
        name=name,
        label=name,
        type=QuestionType.SELECT_ONE_FROM_FILE,
        choices_file=choices_file,
    )
    question.submodule.add(submodule)
    return question


def _config_with(submodule, question, **choice_fields):
    config = SubmoduleQuestionConfig.create_from_question(
        submodule, question.base_question
    )
    for field, value in choice_fields.items():
        setattr(config, field, value)
    config.full_clean()
    config.save()
    return config


def test_choice_group_organizations_include_config_usage(
    org_admin,
    organization_1,
    organization_2,
    submodule_1,
    foreign_submodule,
    root_question_2,
):
    list_b = ChoiceGroup.objects.create(name="ListB")
    foreign_question = RootQuestion.objects.create(
        name="ForeignSelect",
        label="Foreign select",
        type=QuestionType.SELECT_ONE,
        choices=list_b,
    )
    foreign_question.submodule.add(foreign_submodule)
    foreign_admin = _foreign_admin(organization_2)
    assert set(get_organizations(list_b)) == {organization_2}
    assert can_mutate_object(foreign_admin, list_b)

    # Org A's surveys now emit ListB through its submodule configuration, so
    # org B alone may no longer edit it.
    _config_with(submodule_1, root_question_2, choices=list_b)

    assert set(get_organizations(list_b)) == {organization_1, organization_2}
    assert not can_mutate_object(foreign_admin, list_b)
    assert not can_mutate_object(org_admin, list_b)


def test_choice_group_used_only_by_config_belongs_to_its_submodule(
    org_admin, organization_1, organization_2, submodule_1, root_question_2
):
    list_c = ChoiceGroup.objects.create(name="ConfigOnlyList")
    _config_with(submodule_1, root_question_2, choices=list_c)

    assert list(get_organizations(list_c)) == [organization_1]
    assert can_mutate_object(org_admin, list_c)
    assert not can_mutate_object(_foreign_admin(organization_2), list_c)


def test_choice_file_organizations_include_config_usage(
    org_admin,
    organization_1,
    organization_2,
    submodule_1,
    foreign_submodule,
    choice_file,
):
    _file_question("ForeignFileQuestion", foreign_submodule, choice_file)
    own_question = _file_question(
        "OwnFileQuestion", submodule_1, _choice_file("OwnChoiceFile")
    )
    foreign_admin = _foreign_admin(organization_2)
    assert set(get_organizations(choice_file)) == {organization_2}
    assert can_mutate_object(foreign_admin, choice_file)

    _config_with(submodule_1, own_question, choices_file=choice_file)

    assert set(get_organizations(choice_file)) == {organization_1, organization_2}
    assert not can_mutate_object(foreign_admin, choice_file)


def test_choice_file_used_only_by_config_belongs_to_its_submodule(
    org_admin, organization_1, organization_2, submodule_1
):
    question = _file_question(
        "OwnFileQuestion", submodule_1, _choice_file("OwnChoiceFile")
    )
    config_only = _choice_file("ConfigOnlyFile")
    _config_with(submodule_1, question, choices_file=config_only)

    assert list(get_organizations(config_only)) == [organization_1]
    assert can_mutate_object(org_admin, config_only)
    assert not can_mutate_object(_foreign_admin(organization_2), config_only)


# --- PROTECT on choice lists used by configs ---------------------------------

PROTECTED_MESSAGE = "would require deleting the following protected related objects"


def _assert_admin_delete_is_refused(client, url, config):
    for response in (client.get(url), client.post(url, {"post": "yes"})):
        assert response.status_code == 200
        content = response.content.decode()
        assert PROTECTED_MESSAGE in content
        assert str(config) in content


def test_choice_group_used_by_config_cannot_be_deleted(
    logged_admin_client, submodule_1, root_question_2
):
    protected = ChoiceGroup.objects.create(name="ProtectedConfigList")
    config = _config_with(submodule_1, root_question_2, choices=protected)

    with pytest.raises(ProtectedError):
        protected.delete()

    url = reverse("admin:questions_choicegroup_delete", args=[protected.pk])
    _assert_admin_delete_is_refused(logged_admin_client, url, config)
    assert ChoiceGroup.objects.filter(pk=protected.pk).exists()


def test_choice_file_used_by_config_cannot_be_deleted(
    logged_admin_client, submodule_1, choice_file
):
    protected = _choice_file("ProtectedConfigFile")
    question = _file_question("ProtectedFileQuestion", submodule_1, choice_file)
    config = _config_with(submodule_1, question, choices_file=protected)

    with pytest.raises(ProtectedError):
        protected.delete()

    url = reverse("admin:questions_choicegroupfile_delete", args=[protected.pk])
    _assert_admin_delete_is_refused(logged_admin_client, url, config)
    assert ChoiceGroupFile.objects.filter(pk=protected.pk).exists()

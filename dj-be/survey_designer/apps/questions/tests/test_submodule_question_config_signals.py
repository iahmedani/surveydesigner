"""Signals for per-submodule question configurations."""

from unittest import mock

import pytest
from django.db import transaction
from questions.models import (
    SubmoduleQuestionConfig,
    SubmoduleQuestionConfigConstraintMessageTranslation,
    SubmoduleQuestionConfigTranslation,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def config(submodule_1, root_question_1, root_question_2):
    config = SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_2.base_question
    )
    config.label = "See ${TestQuestion1}"
    config.hint = "Hint ${TestQuestion1}"
    config.constraint_message = "Less than ${TestQuestion1}"
    config.relevant = "${TestQuestion1} > 1"
    config.constraint = ". < ${TestQuestion1}"
    config.required = "${TestQuestion1} = 2"
    config.read_only = "${TestQuestion1} = 3"
    config.default = "${TestQuestion1}"
    config.choice_filter = "${TestQuestion1} = 4"
    config.calculation = "${TestQuestion1} + 1"
    config.save()
    SubmoduleQuestionConfigTranslation.objects.create(
        config=config,
        language="fr",
        label="Voir ${TestQuestion1}",
        hint="${TestQuestion1}",
    )
    SubmoduleQuestionConfigConstraintMessageTranslation.objects.create(
        config=config, language="fr", label="Moins que ${TestQuestion1}"
    )
    return config


REWRITTEN_FIELDS = (
    "label",
    "hint",
    "constraint_message",
    "relevant",
    "constraint",
    "required",
    "read_only",
    "default",
    "choice_filter",
    "calculation",
)


def test_root_rename_rewrites_config_fields_and_translations(config, root_question_1):
    root_question_1.name = "RenamedQuestion"
    root_question_1.save()

    config.refresh_from_db()
    for field in REWRITTEN_FIELDS:
        value = getattr(config, field)
        assert "${RenamedQuestion}" in value, field
        assert "${TestQuestion1}" not in value, field
    translation = config.translations.get()
    assert translation.label == "Voir ${RenamedQuestion}"
    assert translation.hint == "${RenamedQuestion}"
    assert config.constraint_translations.get().label == "Moins que ${RenamedQuestion}"


def test_rename_rewrites_the_renamed_questions_own_config(submodule_1, root_question_1):
    own = SubmoduleQuestionConfig.create_from_question(
        submodule_1, root_question_1.base_question
    )
    own.constraint = "${TestQuestion1} > 0"
    own.save()

    root_question_1.name = "RenamedOwn"
    root_question_1.save()

    own.refresh_from_db()
    assert own.constraint == "${RenamedOwn} > 0"


def test_sub_question_rename_rewrites_configs(submodule_1, sub_question_2, config):
    old_name = sub_question_2.name
    config.relevant = "${" + old_name + "} = 1 and ${TestQuestion1x} = 2"
    config.save()

    # Sub-question names are derived; renaming the root renames the sub-question.
    root = sub_question_2.root_question
    root.name = "NewRoot"
    root.save()
    sub_question_2.refresh_from_db()

    config.refresh_from_db()
    assert "${" + sub_question_2.name + "}" in config.relevant
    assert "${" + old_name + "}" not in config.relevant
    # Similar names are not touched.
    assert "${TestQuestion1x}" in config.relevant


def test_unrelated_tokens_are_left_alone(config, root_question_2):
    before = {field: getattr(config, field) for field in REWRITTEN_FIELDS}
    root_question_2.name = "SomethingElse"
    root_question_2.save()
    config.refresh_from_db()
    assert {field: getattr(config, field) for field in REWRITTEN_FIELDS} == before


def test_config_changes_invalidate_module_cache_on_commit(
    submodule_1, root_question_1, django_capture_on_commit_callbacks
):
    with mock.patch("modules.signals.invalidate_module_cache") as invalidate:
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            config = SubmoduleQuestionConfig.create_from_question(
                submodule_1, root_question_1.base_question
            )
            # Deferred: nothing is invalidated before the commit.
            assert not invalidate.called
        assert len(callbacks) == 1
        assert invalidate.call_count == 1

        for change in (
            lambda: SubmoduleQuestionConfigTranslation.objects.create(
                config=config, language="es", label="Etiqueta"
            ),
            lambda: config.translations.get(language="es").delete(),
            lambda: SubmoduleQuestionConfigConstraintMessageTranslation.objects.create(
                config=config, language="es", label="Mensaje"
            ),
            config.delete,
        ):
            invalidate.reset_mock()
            with django_capture_on_commit_callbacks(execute=True) as callbacks:
                change()
                assert not invalidate.called
            assert len(callbacks) == 1
            assert invalidate.call_count == 1


def test_bulk_config_changes_invalidate_once_per_transaction(
    submodule_1, root_question_1, root_question_2, django_capture_on_commit_callbacks
):
    with mock.patch("modules.signals.invalidate_module_cache") as invalidate:
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            for question in (root_question_1, root_question_2):
                config = SubmoduleQuestionConfig.create_from_question(
                    submodule_1, question.base_question
                )
                for language in ("es", "ar", "pt"):
                    SubmoduleQuestionConfigTranslation.objects.create(
                        config=config, language=language, label=language
                    )
        assert len(callbacks) == 1
        assert invalidate.call_count == 1


def test_invalidation_rolled_back_with_savepoint_is_registered_again(
    submodule_1, root_question_1, django_capture_on_commit_callbacks
):
    with mock.patch("modules.signals.invalidate_module_cache") as invalidate:
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            try:
                with transaction.atomic():
                    SubmoduleQuestionConfig.create_from_question(
                        submodule_1, root_question_1.base_question
                    )
                    raise RuntimeError("roll back")
            except RuntimeError:
                pass
            # The rolled-back registration is gone; a later change registers.
            config = SubmoduleQuestionConfig.create_from_question(
                submodule_1, root_question_1.base_question
            )
            config.save()
        assert len(callbacks) == 1
        assert invalidate.call_count == 1

"""Resolve the effective configuration of a question inside one submodule.

A shared ``RootQuestion``/``SubQuestion`` may be customised per submodule with a
``SubmoduleQuestionConfig`` (see ``docs/kb/design/submodule-question-config.md``).
Every consumer that emits or inspects a question *in the context of a
submodule* (XLSForm generation, composition validation, ``/api/submodules/``)
must use this module so the resolution rules live in exactly one place:

1. A config for ``(submodule, question)`` supplies every overridable field,
   the choice list / choice file and the translations.
2. Otherwise a sub-question whose choices are inherited from its root (no
   suffix / suffix_2) uses the choice list of the root's config in that
   submodule, if there is one. Every other field stays shared.
3. Otherwise the shared question is used unchanged.

``name`` and ``type`` always come from the shared question.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.db.models import Prefetch
from questions.const import QuestionType
from questions.models import (
    Choice,
    ChoiceGroup,
    ChoiceGroupFile,
    RootQuestion,
    SubmoduleQuestionConfig,
    SubmoduleQuestionConfigConstraintMessageTranslation,
    SubmoduleQuestionConfigTranslation,
    SubQuestion,
)

OVERRIDABLE_FIELDS = SubmoduleQuestionConfig.COPIED_FIELDS

_FILE_TYPES = (
    QuestionType.SELECT_ONE_FROM_FILE,
    QuestionType.SELECT_MULTIPLE_FROM_FILE,
)
_SELECT_TYPES = (QuestionType.SELECT_ONE, QuestionType.SELECT_MULTIPLE)


def _split_choice_source(source) -> tuple[ChoiceGroup | None, ChoiceGroupFile | None]:
    if isinstance(source, ChoiceGroupFile):
        return None, source
    if isinstance(source, ChoiceGroup):
        return source, None
    return None, None


def shared_choice_source(question) -> tuple[ChoiceGroup | None, ChoiceGroupFile | None]:
    """The shared question's effective (choice list, choice file)."""

    if isinstance(question, RootQuestion):
        if question.choices_file_id:
            return None, question.choices_file
        return question.choices, None
    return _split_choice_source(question.choices)


def inherits_root_choices(sub_question: SubQuestion) -> bool:
    """Whether ``SubQuestion.choices`` falls through to the root question."""

    return not sub_question.suffix_id and not sub_question.suffix_2_id


def xls_type_for(
    question_type: str,
    choices: ChoiceGroup | None,
    choices_file: ChoiceGroupFile | None,
) -> str:
    """The survey-sheet ``type`` cell for a type and its effective choices."""

    if question_type in _SELECT_TYPES and choices is not None:
        return f"{question_type} {choices.name}"
    if question_type in _FILE_TYPES and choices_file is not None:
        if choices_file.csv_file:
            filename = os.path.basename(choices_file.csv_file.name)
            return f"{question_type} {filename}"
    return question_type


def config_reference(config: SubmoduleQuestionConfig | None) -> dict | None:
    """Stable identification of a config for issues and row source maps."""

    if config is None:
        return None
    return {
        "model": "SubmoduleQuestionConfig",
        "id": config.id,
        "submodule": {"id": config.submodule_id, "name": config.submodule.name},
    }


@dataclass(frozen=True)
class EffectiveQuestion:
    """The values to emit for ``question`` inside submodule ``submodule_id``.

    It exposes the same attribute names as the shared question so emitters can
    use it in place of the model instance.
    """

    question: Any
    submodule_id: int | None
    config: SubmoduleQuestionConfig | None
    # The root config whose choice list a sub-question inherits (rule 2).
    choices_config: SubmoduleQuestionConfig | None
    values: dict

    def __getattr__(self, item):
        # Only called for attributes not defined on the dataclass.
        values = object.__getattribute__(self, "values")
        if item in values:
            return values[item]
        return getattr(object.__getattribute__(self, "question"), item)

    @property
    def name(self) -> str:
        return self.question.name

    @property
    def type(self) -> str:
        return self.question.type

    @property
    def is_customised(self) -> bool:
        return self.config is not None or self.choices_config is not None

    @property
    def source_config(self) -> SubmoduleQuestionConfig | None:
        """The config record a content manager edits to change this row."""

        return self.config or self.choices_config

    def _choice_pair(self) -> tuple[ChoiceGroup | None, ChoiceGroupFile | None]:
        # Lazy: shared choices are only loaded when a caller needs them.
        source = self.config or self.choices_config
        if source is not None:
            return source.choices, source.choices_file
        return shared_choice_source(self.question)

    @property
    def choices(self) -> ChoiceGroup | None:
        return self._choice_pair()[0]

    @property
    def choices_file(self) -> ChoiceGroupFile | None:
        return self._choice_pair()[1]

    @property
    def choice_source(self) -> ChoiceGroup | ChoiceGroupFile | None:
        return self.choices_file or self.choices

    @property
    def translations(self) -> list:
        if self.config is not None:
            return list(self.config.translations.all())
        return list(self.question.translations.all())

    @property
    def constraint_translations(self) -> list:
        if self.config is not None:
            return list(self.config.constraint_translations.all())
        return list(self.question.constraint_translations.all())

    def get_xls_type(self) -> str:
        choices, choices_file = self._choice_pair()
        return xls_type_for(self.type, choices, choices_file)

    def expression_values(self, fields: Iterable[str]) -> dict[str, str]:
        return {field: self.values.get(field) or "" for field in fields}


def _question_key(question) -> tuple[str, int] | None:
    if isinstance(question, RootQuestion):
        return ("root", question.id)
    if isinstance(question, SubQuestion):
        return ("sub", question.id)
    return None


class QuestionConfigResolver:
    """Bulk-loaded configs for a set of submodules.

    One instance loads all configs of the requested submodules with three
    queries (configs + label/hint translations + constraint-message
    translations), so resolving any number of questions adds no queries
    beyond what the shared questions themselves already prefetched.
    """

    def __init__(
        self,
        submodule_ids: Iterable[int],
        languages: Iterable[str] | None = None,
        *,
        prefetch_choice_options: bool = False,
    ):
        self.submodule_ids = {int(id_) for id_ in submodule_ids if id_ is not None}
        self.languages = None if languages is None else tuple(languages)
        self._configs: dict[tuple[int, str, int], SubmoduleQuestionConfig] = {}
        if not self.submodule_ids:
            return

        translations = SubmoduleQuestionConfigTranslation.objects.all()
        constraint_translations = (
            SubmoduleQuestionConfigConstraintMessageTranslation.objects.all()
        )
        if self.languages is not None:
            translations = translations.filter(language__in=self.languages)
            constraint_translations = constraint_translations.filter(
                language__in=self.languages
            )
        prefetches = [
            Prefetch("translations", queryset=translations),
            Prefetch("constraint_translations", queryset=constraint_translations),
        ]
        if prefetch_choice_options:
            prefetches.append(
                Prefetch(
                    "choices__choices",
                    queryset=Choice.objects.order_by().prefetch_related("translations"),
                )
            )
        configs = (
            SubmoduleQuestionConfig.objects.filter(submodule_id__in=self.submodule_ids)
            .select_related(
                "submodule",
                "base_question",
                "choices",
                "choices__choice_filter_list",
                "choices_file",
            )
            .prefetch_related(*prefetches)
        )
        for config in configs:
            base_question = config.base_question
            if base_question.root_question_id:
                key = (config.submodule_id, "root", base_question.root_question_id)
            elif base_question.sub_question_id:
                key = (config.submodule_id, "sub", base_question.sub_question_id)
            else:
                continue
            self._configs[key] = config

    def __bool__(self) -> bool:
        return bool(self._configs)

    @property
    def configs(self) -> list[SubmoduleQuestionConfig]:
        return list(self._configs.values())

    def config_for(self, question, submodule_id) -> SubmoduleQuestionConfig | None:
        key = _question_key(question)
        if key is None or submodule_id is None:
            return None
        return self._configs.get((int(submodule_id), *key))

    def resolve(self, question, submodule_id) -> EffectiveQuestion:
        """Apply the three resolution rules for ``question`` in ``submodule_id``."""

        config = self.config_for(question, submodule_id)
        if config is not None:
            values = {field: getattr(config, field) for field in OVERRIDABLE_FIELDS}
            return EffectiveQuestion(
                question=question,
                submodule_id=submodule_id,
                config=config,
                choices_config=None,
                values=values,
            )

        values = {field: getattr(question, field) for field in OVERRIDABLE_FIELDS}
        choices_config = None
        if (
            submodule_id is not None
            and isinstance(question, SubQuestion)
            and inherits_root_choices(question)
        ):
            # Rule 2: never mix the root's shared list with a customised root.
            choices_config = self._configs.get(
                (int(submodule_id), "root", question.root_question_id)
            )
        return EffectiveQuestion(
            question=question,
            submodule_id=submodule_id,
            config=None,
            choices_config=choices_config,
            values=values,
        )


def resolve_question(question, submodule) -> EffectiveQuestion:
    """Convenience for one-off callers; bulk callers build one resolver."""

    submodule_id = getattr(submodule, "pk", submodule)
    return QuestionConfigResolver([submodule_id]).resolve(question, submodule_id)


__all__ = [
    "OVERRIDABLE_FIELDS",
    "EffectiveQuestion",
    "QuestionConfigResolver",
    "config_reference",
    "inherits_root_choices",
    "resolve_question",
    "shared_choice_source",
    "xls_type_for",
]

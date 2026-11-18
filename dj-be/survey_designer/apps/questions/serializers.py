from typing import Dict, List, Union

from questions.models import (
    Calculation,
    Choice,
    ChoiceGroup,
    ChoiceGroupFile,
    ChoiceTranslation,
    RecallPeriod,
    RepeatSection,
    RootQuestion,
    RootQuestionTranslation,
    SubmoduleQuestionConfigTranslation,
    SubQuestion,
    SubQuestionTranslation,
    Suffix,
)
from rest_framework import serializers

# Serializer context keys used by /api/submodules/ to emit per-submodule
# effective question values (see services/question_config.py).
QUESTION_CONFIG_RESOLVER_CONTEXT = "question_config_resolver"
QUESTION_CONFIG_SUBMODULE_CONTEXT = "question_config_submodule_id"


class CalculationSerializer(serializers.ModelSerializer):
    label = serializers.SerializerMethodField()

    class Meta:
        model = Calculation
        fields = ("id", "label")

    def get_label(self, obj):
        return f"{obj.label} ({obj.name})"


class SuffixSerializer(serializers.ModelSerializer):
    class Meta:
        model = Suffix
        fields = ("id", "name", "description")


class RecallPeriodSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecallPeriod
        fields = ("id", "name", "description")


class ChoiceTranslationSerializer(serializers.ModelSerializer):
    language_display = serializers.SerializerMethodField()

    class Meta:
        model = ChoiceTranslation
        fields = (
            "language",
            "language_display",
        )

    def get_language_display(self, obj) -> str:
        return obj.get_language_display()


class ChoiceSerializer(serializers.ModelSerializer):
    translations = ChoiceTranslationSerializer(many=True, read_only=True)

    class Meta:
        model = Choice
        fields = ("translations",)


class ChoiceGroupSerializer(serializers.ModelSerializer):
    choices = ChoiceSerializer(many=True, read_only=True)

    class Meta:
        model = ChoiceGroup
        fields = ("choices",)


class ChoiceGroupFileSerializer(serializers.ModelSerializer):
    csv_file = serializers.FileField(read_only=True)

    class Meta:
        model = ChoiceGroupFile
        fields = ("id", "name", "csv_file")


class RootQuestionTranslationSerializer(serializers.ModelSerializer):
    language_display = serializers.SerializerMethodField()

    class Meta:
        model = RootQuestionTranslation
        fields = (
            "language",
            "language_display",
        )

    def get_language_display(self, obj) -> str:
        return obj.get_language_display()


class SubQuestionTranslationSerializer(RootQuestionTranslationSerializer):
    class Meta:
        model = SubQuestionTranslation
        fields = RootQuestionTranslationSerializer.Meta.fields


class SubmoduleQuestionConfigTranslationSerializer(RootQuestionTranslationSerializer):
    class Meta:
        model = SubmoduleQuestionConfigTranslation
        fields = RootQuestionTranslationSerializer.Meta.fields


class EffectiveQuestionSerializerMixin:
    """Replace shared values by the submodule's effective configuration.

    Active only when the serializer context carries a QuestionConfigResolver
    and the submodule being serialized; the JSON shape never changes.
    """

    def _effective(self, instance):
        resolver = self.context.get(QUESTION_CONFIG_RESOLVER_CONTEXT)
        submodule_id = self.context.get(QUESTION_CONFIG_SUBMODULE_CONTEXT)
        if not resolver or submodule_id is None:
            return None
        effective = resolver.resolve(instance, submodule_id)
        return effective if effective.is_customised else None

    def apply_effective(self, data, effective, *, include_choices_file):
        if effective.config is not None:
            data["label"] = effective.label
            data["translations"] = SubmoduleQuestionConfigTranslationSerializer(
                effective.translations, many=True
            ).data
        choices = effective.choices
        data["choices"] = (
            ChoiceGroupSerializer(choices).data if choices is not None else None
        )
        if include_choices_file:
            choices_file = effective.choices_file
            data["choices_file"] = (
                ChoiceGroupFileSerializer(choices_file, context=self.context).data
                if choices_file is not None
                else None
            )
        return data


class SubQuestionSerializer(
    EffectiveQuestionSerializerMixin, serializers.ModelSerializer
):
    suffix = SuffixSerializer(required=False)
    suffix_2 = SuffixSerializer(required=False)
    recall_period = RecallPeriodSerializer(required=False)
    group = serializers.SerializerMethodField()
    translations = SubQuestionTranslationSerializer(many=True, read_only=True)
    choices = ChoiceGroupSerializer(read_only=True)

    class Meta:
        model = SubQuestion
        fields = (
            "id",
            "name",
            "label",
            "choices",
            "suffix",
            "suffix_2",
            "recall_period",
            "group",
            "translations",
        )

    def get_group(self, obj) -> List[Dict[str, Union[str, int]]]:
        group: List[Dict[str, Union[str, int]]] = []
        if obj.suffix_id:
            group.append(
                {
                    "name": obj.suffix.name,
                    "display": obj.suffix.description or obj.suffix.name,
                    "suffix_1_id": obj.suffix_id,
                }
            )

        if obj.suffix_2_id:
            group.append(
                {
                    "name": obj.suffix_2.name,
                    "display": obj.suffix_2.description or obj.suffix_2.name,
                    "suffix_2_id": obj.suffix_2_id,
                }
            )

        if obj.recall_period_id:
            group.append(
                {
                    "name": obj.recall_period.name,
                    "display": obj.recall_period.description or obj.recall_period.name,
                    "recall_period_id": obj.recall_period_id,
                }
            )

        return group

    def to_representation(self, instance):
        data = super().to_representation(instance)
        effective = self._effective(instance)
        if effective is None:
            return data
        return self.apply_effective(data, effective, include_choices_file=False)


class RootQuestionSerializer(
    EffectiveQuestionSerializerMixin, serializers.ModelSerializer
):
    sub_questions = SubQuestionSerializer(many=True, read_only=True)
    translations = RootQuestionTranslationSerializer(many=True, read_only=True)
    choices = ChoiceGroupSerializer(read_only=True)
    choices_file = ChoiceGroupFileSerializer(read_only=True)

    class Meta:
        model = RootQuestion
        fields = (
            "id",
            "name",
            "label",
            "choices",
            "choices_file",
            "sub_questions",
            "translations",
        )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        effective = self._effective(instance)
        if effective is None:
            return data
        return self.apply_effective(data, effective, include_choices_file=True)


class RootQuestionExportSerializer(serializers.ModelSerializer):
    module_name = serializers.SerializerMethodField()
    module_label = serializers.SerializerMethodField()
    submodule_name = serializers.SerializerMethodField()
    submodule_label = serializers.SerializerMethodField()
    choice_list = serializers.SerializerMethodField()
    name = serializers.CharField(read_only=True)
    translations = serializers.SerializerMethodField()

    def get_choice_list(self, obj):
        if obj.choices_file:
            return obj.choices_file.name
        elif obj.choices:
            return obj.choices.name
        return ""

    class Meta:
        model = RootQuestion
        fields = (
            "module_name",
            "module_label",
            "submodule_name",
            "submodule_label",
            "type",
            "calculation",
            "choice_list",
            "name",
            "relevant",
            "constraint",
            "description",
            "translations",
            "appearance",
            "parameters",
            "required",
            "default",
            "disabled",
            "read_only",
        )

    def get_module_name(self, obj):
        return self.__get_submodule_values_list_str(obj, "module__name")

    def get_module_label(self, obj):
        return self.__get_submodule_values_list_str(obj, "module__label")

    def get_submodule_name(self, obj):
        return self.__get_submodule_values_list_str(obj, "name")

    def get_submodule_label(self, obj):
        return self.__get_submodule_values_list_str(obj, "label")

    @staticmethod
    def __get_submodule_values_list_str(obj, lookup):
        return ", ".join(obj.submodule.values_list(lookup, flat=True))

    def get_translations(self, obj):
        translations = [
            {
                "language": "en",
                "language_display": "English",
                "label": obj.label,
                "hint": obj.hint,
            }
        ]

        for translation in obj.translations.all():
            translations.append(
                {
                    "language": translation.language,
                    "language_display": translation.get_language_display(),
                    "label": translation.label,
                    "hint": translation.hint,
                }
            )

        return translations


class SubQuestionExportSerializer(serializers.ModelSerializer):
    module_name = serializers.SerializerMethodField()
    module_label = serializers.SerializerMethodField()
    submodule_name = serializers.SerializerMethodField()
    submodule_label = serializers.SerializerMethodField()

    choice_list = serializers.CharField(
        source="choices.name", read_only=True, default=""
    )
    name = serializers.CharField(read_only=True)
    suffix1 = serializers.CharField(source="suffix.name", read_only=True, default="")
    suffix2 = serializers.CharField(source="suffix_2.name", read_only=True, default="")
    recall_period = serializers.CharField(
        source="recall_period.name", read_only=True, default=""
    )
    translations = serializers.SerializerMethodField()

    class Meta:
        model = SubQuestion
        fields = (
            "module_name",
            "module_label",
            "submodule_name",
            "submodule_label",
            "type",
            "calculation",
            "choice_list",
            "name",
            "suffix1",
            "suffix2",
            "recall_period",
            "relevant",
            "constraint",
            "description",
            "translations",
            "appearance",
            "parameters",
            "required",
            "default",
            "disabled",
            "read_only",
        )

    def get_module_name(self, obj):
        return self.__get_submodule_values_list_str(obj, "module__name")

    def get_module_label(self, obj):
        return self.__get_submodule_values_list_str(obj, "module__label")

    def get_submodule_name(self, obj):
        return self.__get_submodule_values_list_str(obj, "name")

    def get_submodule_label(self, obj):
        return self.__get_submodule_values_list_str(obj, "label")

    @staticmethod
    def __get_submodule_values_list_str(obj, lookup):
        return ", ".join(obj.root_question.submodule.values_list(lookup, flat=True))

    def get_translations(self, obj):
        translations = [
            {
                "language": "en",
                "language_display": "English",
                "label": obj.label,
                "hint": obj.hint,
            }
        ]

        for translation in obj.translations.all():
            translations.append(
                {
                    "language": translation.language,
                    "language_display": translation.get_language_display(),
                    "label": translation.label,
                    "hint": translation.hint,
                }
            )

        return translations


class RepeatSectionExportSerializer(serializers.ModelSerializer):
    module_name = serializers.SerializerMethodField()
    module_label = serializers.SerializerMethodField()
    submodule_name = serializers.SerializerMethodField()
    submodule_label = serializers.SerializerMethodField()
    type = serializers.SerializerMethodField()
    name = serializers.CharField(read_only=True)
    translations = serializers.SerializerMethodField()

    class Meta:
        model = RepeatSection
        fields = (
            "module_name",
            "module_label",
            "submodule_name",
            "submodule_label",
            "type",
            "name",
            "repeat_count",
            "translations",
            "description",
            "relevant",
        )

    def get_type(self, obj):
        return "repeat"

    def get_translations(self, obj):
        translations = [
            {
                "language": "en",
                "language_display": "English",
                "label": obj.label,
            }
        ]

        for translation in obj.translations.all():
            translations.append(
                {
                    "language": translation.language,
                    "language_display": translation.get_language_display(),
                    "label": translation.label,
                }
            )

        return translations

    def get_module_name(self, obj):
        return self.__get_submodule_values_list_str(obj, "module__name")

    def get_module_label(self, obj):
        return self.__get_submodule_values_list_str(obj, "module__label")

    def get_submodule_name(self, obj):
        return self.__get_submodule_values_list_str(obj, "name")

    def get_submodule_label(self, obj):
        return self.__get_submodule_values_list_str(obj, "label")

    @staticmethod
    def __get_submodule_values_list_str(obj, lookup):
        return ", ".join(obj.submodule.values_list(lookup, flat=True))


class CalculationExportSerializer(serializers.ModelSerializer):
    module_name = serializers.CharField(
        source="submodule.module.name", read_only=True, default=""
    )
    module_label = serializers.CharField(
        source="submodule.module.label", read_only=True, default=""
    )
    submodule_name = serializers.CharField(
        source="submodule.name", read_only=True, default=""
    )
    submodule_label = serializers.CharField(
        source="submodule.label", read_only=True, default=""
    )
    type = serializers.SerializerMethodField()
    name = serializers.CharField(read_only=True)
    translations = serializers.SerializerMethodField()

    class Meta:
        model = Calculation
        fields = (
            "module_name",
            "module_label",
            "submodule_name",
            "submodule_label",
            "type",
            "name",
            "calculation",
            "translations",
            "description",
        )

    def get_type(self, obj):
        return "calculate"

    def get_translations(self, obj):
        return [
            {
                "language": "en",
                "language_display": "English",
                "label": obj.label,
            }
        ]

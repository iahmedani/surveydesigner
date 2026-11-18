from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from modules.models import Module, Submodule, SubmoduleMapping


def invalidate_module_cache():
    cache.delete_pattern("*modules_*")


class _PendingModuleCacheInvalidation:
    """on_commit callback that remembers whether it already ran."""

    def __init__(self):
        self.done = False

    def __call__(self):
        self.done = True
        invalidate_module_cache()


def invalidate_module_cache_on_commit():
    """Invalidate the cached modules API once the current transaction commits.

    Deferring avoids a concurrent GET re-caching pre-commit data; registering
    only once per transaction avoids one full keyspace SCAN per saved row on
    bulk imports. The connection's pending callbacks are consulted instead of
    a flag, so a registration discarded by a (savepoint) rollback is simply
    registered again by the next call. Outside an atomic block ``on_commit``
    runs the callback immediately.
    """
    connection = transaction.get_connection()
    if connection.in_atomic_block and any(
        isinstance(func, _PendingModuleCacheInvalidation) and not func.done
        for _, func, _ in connection.run_on_commit
    ):
        return
    transaction.on_commit(_PendingModuleCacheInvalidation())


@receiver(post_save, sender=SubmoduleMapping)
@receiver(post_delete, sender=SubmoduleMapping)
def handle_submodule_mapping_change(sender, instance, **kwargs):
    invalidate_module_cache()


@receiver(post_save, sender=Module)
@receiver(post_delete, sender=Module)
@receiver(post_save, sender=Submodule)
@receiver(post_delete, sender=Submodule)
def handle_module_metadata_change(sender, instance, **kwargs):
    invalidate_module_cache()


@receiver(m2m_changed, sender=SubmoduleMapping.survey_categories.through)
@receiver(m2m_changed, sender=SubmoduleMapping.survey_types.through)
@receiver(m2m_changed, sender=SubmoduleMapping.survey_attributes.through)
def handle_m2m_changed(sender, instance, action, **kwargs):
    if action in ["post_add", "post_remove", "post_clear"]:
        invalidate_module_cache()

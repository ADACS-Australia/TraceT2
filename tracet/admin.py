from django.db import connection
from django.core.cache import cache
from django.contrib import admin
import django.contrib.auth as auth

from . import models
from tracet.templatetags.iso8601 import iso8601


@admin.register(models.Stream)
class Stream(admin.ModelAdmin):
    list_display = ["name", "domain", "_last_polled", "enabled"]
    fields = ["name", "domain", "config", "enabled", "_last_polled"]
    readonly_fields = ["_last_polled"]

    @admin.display(description="Last Polled")
    def _last_polled(self, obj=None):
        if obj is None:
            return "Never polled"
        else:
            return iso8601(obj.last_polled)

    # The following hooks are to detect changes to Stream configuration
    # made via the admin interface and trigger the listener to requery and make new
    # Kafka connections.
    def save_model(self, *args, **kwargs):
        cache.set("reset_streams", True)
        return super().save_model(*args, **kwargs)

    def delete_model(self, *args, **kwargs):
        cache.set("reset_streams", True)
        return super().delete_model(*args, **kwargs)

    def delete_queryset(self, *args, **kwargs):
        cache.set("reset_streams", True)
        return super().delete_queryset(*args, **kwargs)


@admin.register(models.Topic)
class Topic(admin.ModelAdmin):
    list_display = [
        "name",
        "stream",
        "type",
        "notice_count",
        "payload_filesize",
        "status",
        "enabled",
    ]
    fields = ["name", "stream", "type", "status", "enabled"]
    readonly_fields = ["status"]

    @admin.display(description="Payload size [MB]")
    def payload_filesize(self, obj):
        # Return the cumulative payload filesize of this topic's associated notices. [MB]
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT SUM(OCTET_LENGTH(payload)) FROM tracet_notice WHERE topic_id = %s",
                [obj.id],
            )
            if filesize := cursor.fetchone()[0]:
                return filesize / 1e6
            else:
                # If there are no notices, fetchone() will return None
                return 0

    @admin.display(description="Notice count")
    def notice_count(self, obj):
        return obj.notices.count()

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ["status"]
        else:
            # Once a topic is created, we limit edit solely to
            # modifying "enabled".
            return ["name", "stream", "type", "status"]

    # The following hooks are to detect changes to Topic configuration
    # made via the admin interface and trigger the listener to requery and make new
    # Kafka connections.
    def save_model(self, *args, **kwargs):
        cache.set("reset_streams", True)
        return super().save_model(*args, **kwargs)

    def delete_model(self, *args, **kwargs):
        cache.set("reset_streams", True)
        return super().delete_model(*args, **kwargs)

    def delete_queryset(self, *args, **kwargs):
        cache.set("reset_streams", True)
        return super().delete_queryset(*args, **kwargs)


# auth.models.User has already been registered with Django admin
# so we subclass it simply so that we can register it ourselves with minor changes.
class User(auth.models.User):
    class Meta:
        proxy = True


@admin.register(User)
class User(auth.admin.UserAdmin):
    def __init__(self, *args, **kwargs):
        # Remove the user_permissions field from the form: we only want people using
        # groups as the permission mechanism.
        self.fieldsets[2][1]["fields"] = (
            "is_active",
            "is_staff",
            "is_superuser",
            "groups",
        )
        super().__init__(*args, **kwargs)

from django.db import connection
from django.contrib import admin
import django.contrib.auth as auth

# Register your models here.
from . import models


@admin.register(models.Stream)
class Stream(admin.ModelAdmin):
    list_display = ["name", "domain", "last_polled"]
    fields = ["name", "domain", "config", "enabled", "last_polled"]
    readonly_fields = ["last_polled"]


@admin.register(models.Topic)
class Topic(admin.ModelAdmin):
    list_display = ["name", "stream", "type", "notice_count", "payload_filesize", "status"]
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

    def has_change_permission(self, request, obj=None):
        return False  # Disable editing existing entries


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

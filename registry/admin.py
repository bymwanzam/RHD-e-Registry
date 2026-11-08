"""Django admin (at /django-admin/, for system administrators): direct access to the data.
Day-to-day administration is done in the e-Registry itself (Administration page)."""
from django.contrib import admin

from .models import Attachment, Facility, Letter, LetterEvent, Notification, Outbox, Setting, User


@admin.register(Facility)
class FacilityAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'level', 'parent', 'active')
    list_filter = ('level', 'active')
    search_fields = ('name', 'code')


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('name', 'username', 'role', 'facility', 'phone', 'is_admin', 'is_active')
    list_filter = ('role', 'facility', 'is_admin', 'is_active')
    search_fields = ('name', 'username', 'phone', 'email')
    exclude = ('password', 'last_login')


class EventInline(admin.TabularInline):
    model = LetterEvent
    extra = 0
    can_delete = False
    fields = ('at', 'actor', 'action', 'to_user', 'note', 'status')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Letter)
class LetterAdmin(admin.ModelAdmin):
    list_display = ('ref_no', 'subject', 'status', 'origin_facility', 'dest_facility', 'holder', 'updated_at')
    list_filter = ('status', 'category', 'priority', 'dest_facility')
    search_fields = ('ref_no', 'subject', 'external_sender')
    inlines = [EventInline]
    readonly_fields = ('created_at', 'updated_at', 'dispatched_at', 'received_at', 'closed_at')


@admin.register(LetterEvent)
class LetterEventAdmin(admin.ModelAdmin):
    """The audit trail is read-only."""
    list_display = ('at', 'letter', 'actor', 'action', 'to_user', 'status')
    list_filter = ('action',)
    search_fields = ('letter__ref_no', 'letter__subject', 'note')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Outbox)
class OutboxAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'channel', 'recipient', 'status', 'attempts', 'delivery_status')
    list_filter = ('channel', 'status')


admin.site.register(Attachment)
admin.site.register(Notification)
admin.site.register(Setting)

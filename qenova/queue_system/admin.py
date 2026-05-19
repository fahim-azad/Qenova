from django.contrib import admin

from .models import Organization, QueueBooking, Token, QueueTracker, QueueAnalytics, EmailNotification, Feedback

@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ('id', 'account', 'type', 'token_limit', 'queue_status')

@admin.register(QueueBooking)
class QueueBookingAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'organization', 'booking_date', 'queue_position')

@admin.register(Token)
class TokenAdmin(admin.ModelAdmin):
    list_display = ('serial_number', 'user', 'organization', 'status', 'booking_date')

@admin.register(QueueTracker)
class QueueTrackerAdmin(admin.ModelAdmin):
    list_display = ('organization', 'queue_load', 'waiting_time')

@admin.register(QueueAnalytics)
class QueueAnalyticsAdmin(admin.ModelAdmin):
    list_display = ('organization', 'date', 'total_tokens', 'completed_tokens', 'skipped_tokens', 'peak_hour')

@admin.register(EmailNotification)
class EmailNotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'email_type', 'subject', 'sent_at')
    list_filter = ('email_type',)

@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ('user', 'organization', 'rating', 'submitted_at')
    list_filter = ('rating', 'submitted_at')


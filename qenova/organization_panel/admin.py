from django.contrib import admin

from .models import (
    BehaviorMonitoring,
    NotificationCenter,
    OrganizationDashboard,
    OrganizationNotification,
    QueueReport,
)


@admin.register(OrganizationDashboard)
class OrganizationDashboardAdmin(admin.ModelAdmin):
    list_display = ('organization', 'updated_at')


@admin.register(QueueReport)
class QueueReportAdmin(admin.ModelAdmin):
    list_display = ('organization', 'report_date', 'efficiency_rating', 'created_at')
    list_filter = ('efficiency_rating', 'report_date')


@admin.register(BehaviorMonitoring)
class BehaviorMonitoringAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'no_shows', 'late_arrivals', 'cancellations',
        'is_blacklisted', 'updated_at',
    )
    list_filter = ('is_blacklisted',)


@admin.register(NotificationCenter)
class NotificationCenterAdmin(admin.ModelAdmin):
    list_display = ('organization', 'updated_at')


@admin.register(OrganizationNotification)
class OrganizationNotificationAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'organization', 'notification_type', 'subject', 'is_read', 'sent_at')
    list_filter = ('notification_type', 'is_read', 'sent_at')
    search_fields = ('subject', 'recipient__username', 'message')

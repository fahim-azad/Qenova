from django.urls import path

from . import views

urlpatterns = [
    path(
        'org-dashboard/insights/',
        views.queue_insights_view,
        name='org_queue_insights',
    ),
    path(
        'org-dashboard/notifications/',
        views.notification_center_view,
        name='org_notification_center',
    ),
    path(
        'org-dashboard/settings/',
        views.organization_settings_view,
        name='org_settings',
    ),
    path(
        'org-dashboard/emergency-monitoring/',
        views.emergency_monitoring_view,
        name='org_emergency_monitoring',
    ),
    path(
        'notifications/',
        views.customer_notifications_view,
        name='customer_notifications',
    ),
    path(
        'notifications/<int:notification_id>/read/',
        views.mark_notification_read_view,
        name='mark_notification_read',
    ),
    path(
        'notifications/mark-all-read/',
        views.mark_all_notifications_read_view,
        name='mark_all_notifications_read',
    ),
]

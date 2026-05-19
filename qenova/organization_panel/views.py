from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
import datetime

from queue_system.models import Organization

from .models import (
    BehaviorMonitoring,
    NotificationCenter,
    OrganizationDashboard,
    OrganizationNotification,
    QueueReport,
)


def _get_organization_for_user(user):
    org_profile = user.organization_profile
    organization, _ = Organization.objects.get_or_create(account=org_profile)
    return organization


@login_required
def queue_insights_view(request):
    """
    Queue Insights & Reports — App 4 organization panel.
    Surfaces health, traffic, completion trends, insights, and behavioral reports.
    """
    if not request.user.is_organization:
        return redirect('dashboard')

    organization = _get_organization_for_user(request.user)
    days = int(request.GET.get('days', 7))
    if days not in (7, 14, 30):
        days = 7

    QueueReport.generateDailyReport(organization, datetime.date.today())
    insights = QueueReport.generateQueueInsights(organization, days=days)
    behavioral_reports = BehaviorMonitoring.generateBehavioralReports(organization)
    reports = QueueReport.objects.filter(organization=organization).order_by('-report_date')[:30]

    return render(request, 'organization_panel/queue_insights.html', {
        'organization': organization,
        'insights': insights,
        'behavioral_reports': behavioral_reports,
        'reports': reports,
        'days': days,
    })


@login_required
def notification_center_view(request):
    """Organization Notification Center — broadcast announcements and queue alerts."""
    if not request.user.is_organization:
        return redirect('dashboard')

    organization = _get_organization_for_user(request.user)
    center = NotificationCenter.get_or_create_for_organization(organization)

    if request.method == 'POST':
        action = request.POST.get('action')
        send_email = request.POST.get('send_email') == 'on'

        if action == 'broadcast':
            subject = request.POST.get('subject', '').strip()
            message = request.POST.get('message', '').strip()
            audience = request.POST.get('audience', 'active_today')
            if not subject or not message:
                messages.error(request, 'Subject and message are required for announcements.')
            else:
                sent = center.broadcastAnnouncement(
                    subject, message, audience=audience, send_email=send_email,
                )
                messages.success(
                    request,
                    f'Announcement sent to {len(sent)} customer(s).',
                )

        elif action == 'queue_alert':
            alert_type = request.POST.get('alert_type', 'general')
            custom_message = request.POST.get('custom_message', '').strip() or None
            sent = center.sendQueueAlert(
                alert_type,
                custom_message=custom_message,
                send_email=send_email,
            )
            messages.success(request, f'Queue alert sent to {len(sent)} customer(s).')

        return redirect('org_notification_center')

    sent_notifications = OrganizationNotification.objects.filter(
        organization=organization,
    ).select_related('recipient').order_by('-sent_at')[:50]

    return render(request, 'organization_panel/notification_center.html', {
        'organization': organization,
        'center': center,
        'sent_notifications': sent_notifications,
    })


@login_required
def customer_notifications_view(request):
    """Customer inbox for organization notifications."""
    if request.user.is_organization:
        return redirect('org_notification_center')

    inbox = NotificationCenter.get_inbox_for_user(request.user)
    unread_count = NotificationCenter.unread_count_for_user(request.user)

    return render(request, 'organization_panel/customer_notifications.html', {
        'notifications': inbox[:100],
        'unread_count': unread_count,
    })


@login_required
def mark_notification_read_view(request, notification_id):
    """Mark a single notification as read."""
    note = get_object_or_404(
        OrganizationNotification,
        id=notification_id,
        recipient=request.user,
    )
    note.mark_read()
    next_url = request.GET.get('next', 'customer_notifications')
    return redirect(next_url)


@login_required
def mark_all_notifications_read_view(request):
    """Mark all notifications as read for the current user."""
    OrganizationNotification.objects.filter(
        recipient=request.user,
        is_read=False,
    ).update(is_read=True)
    messages.success(request, 'All notifications marked as read.')
    return redirect('customer_notifications')


@login_required
def organization_settings_view(request):
    """Organization Settings Management — working hours, limits, and queue config."""
    if not request.user.is_organization:
        return redirect('dashboard')

    organization = _get_organization_for_user(request.user)
    dashboard = OrganizationDashboard.get_for_organization(organization)

    if request.method == 'POST':
        action = request.POST.get('action')
        notify = request.POST.get('notify_customers') == 'on'

        if action == 'working_hours':
            clear = request.POST.get('clear_hours') == 'on'
            if clear:
                result = dashboard.manageWorkingHours(clear=True)
            else:
                import datetime
                ws = request.POST.get('work_start', '').strip()
                we = request.POST.get('work_end', '').strip()
                try:
                    work_start = datetime.time.fromisoformat(ws) if ws else None
                    work_end = datetime.time.fromisoformat(we) if we else None
                except ValueError:
                    messages.error(request, 'Invalid time format. Use HH:MM.')
                    return redirect('org_settings')
                result = dashboard.manageWorkingHours(work_start=work_start, work_end=work_end)
            if result.get('success'):
                messages.success(request, f"Working hours: {result['working_hours_display']}")
            else:
                messages.error(request, result.get('error', 'Could not update working hours.'))

        elif action == 'token_limit':
            result = dashboard.setTokenLimit(request.POST.get('token_limit'))
            if result.get('success'):
                messages.success(request, f"Token limit set to {result['token_limit']}.")
            else:
                messages.error(request, result.get('error'))

        elif action == 'booking_limit':
            result = dashboard.controlDailyBookingLimit(request.POST.get('daily_booking_limit'))
            if result.get('success'):
                messages.success(request, f"Daily booking limit set to {result['daily_booking_limit']}.")
            else:
                messages.error(request, result.get('error'))

        elif action == 'queue_capacity':
            result = dashboard.setQueueCapacity(request.POST.get('queue_capacity'))
            if result.get('success'):
                messages.success(request, f"Queue capacity set to {result['queue_capacity']} slots/day.")
                if result.get('queue_capacity') and organization.manageQueueCapacity()['is_full']:
                    if notify:
                        center = NotificationCenter.get_or_create_for_organization(organization)
                        center.sendQueueAlert('queue_full')
            else:
                messages.error(request, result.get('error'))

        elif action == 'configure_all':
            import datetime
            ws = request.POST.get('work_start', '').strip()
            we = request.POST.get('work_end', '').strip()
            work_start = work_end = None
            if ws or we:
                try:
                    work_start = datetime.time.fromisoformat(ws) if ws else None
                    work_end = datetime.time.fromisoformat(we) if we else None
                except ValueError:
                    messages.error(request, 'Invalid time format in full configuration.')
                    return redirect('org_settings')
            result = dashboard.configureQueueSettings(
                token_limit=request.POST.get('token_limit') or None,
                work_start=work_start,
                work_end=work_end,
                queue_status=request.POST.get('queue_status'),
                organization_type=request.POST.get('organization_type'),
                clear_hours=request.POST.get('clear_hours') == 'on',
            )
            if result['changes']:
                messages.success(request, 'Updated: ' + '; '.join(result['changes']))
            for err in result['errors']:
                messages.error(request, err)
            if notify and request.POST.get('queue_status'):
                center = NotificationCenter.get_or_create_for_organization(organization)
                center.sendQueueAlert('status_change')

        elif action == 'queue_status':
            new_status = request.POST.get('queue_status')
            result = dashboard.configureQueueSettings(queue_status=new_status)
            if result.get('success') or result.get('changes'):
                messages.success(request, f'Queue status updated to {new_status}.')
                if notify:
                    center = NotificationCenter.get_or_create_for_organization(organization)
                    center.sendQueueAlert('status_change')
            else:
                for err in result.get('errors', ['Invalid status.']):
                    messages.error(request, err)

        return redirect('org_settings')

    settings = dashboard.getQueueSettings()
    return render(request, 'organization_panel/organization_settings.html', {
        'organization': organization,
        'dashboard': dashboard,
        'settings': settings,
    })


@login_required
def emergency_monitoring_view(request):
    """Emergency Queue Monitoring — activity, flow, misuse, and performance."""
    if not request.user.is_organization:
        return redirect('dashboard')

    organization = _get_organization_for_user(request.user)
    days = int(request.GET.get('days', 7))
    if days not in (7, 14, 30):
        days = 7

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'flag_misuse':
            user_id = request.POST.get('user_id')
            if user_id:
                from django.contrib.auth import get_user_model
                User = get_user_model()
                user = User.objects.filter(id=user_id).first()
                if user:
                    BehaviorMonitoring.recordEmergencyMisuse(user)
                    messages.warning(request, f'Emergency misuse recorded for {user.username}.')
        elif action == 'scan_misuse':
            cases = BehaviorMonitoring.detectEmergencyMisuse(organization, auto_record=True)
            messages.info(request, f'Misuse scan complete. {len(cases)} case(s) reviewed; records updated where applicable.')
        return redirect('org_emergency_monitoring')

    report = QueueReport.generateEmergencyMonitoringReport(organization, days=days)

    return render(request, 'organization_panel/emergency_monitoring.html', {
        'organization': organization,
        'report': report,
        'activity': report['activity'],
        'flow': report['flow'],
        'performance': report['performance'],
        'misuse_scan': report['misuse_scan'],
        'days': days,
    })

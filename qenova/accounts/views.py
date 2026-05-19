from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.mail import send_mail
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from .models import User
from .forms import UserRegistrationForm, OrganizationRegistrationForm, UserProfileForm, CustomAuthenticationForm

def register_view(request):
    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, 'Registration successful! You can now log in.')
            return redirect('login')
    else:
        form = UserRegistrationForm()
        
    return render(request, 'accounts/register.html', {'form': form})

def login_view(request):
    if request.method == 'POST':
        form = CustomAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                if form.cleaned_data.get('remember_me'):
                    request.session.set_expiry(1209600)  # 2 weeks
                
                messages.success(request, f'Welcome back, {username}!')
                return redirect('dashboard')
            else:
                messages.error(request, 'Invalid username or password.')
        else:
            messages.error(request, 'Invalid username or password.')
    else:
        form = CustomAuthenticationForm()
        
    return render(request, 'accounts/login.html', {'form': form})

def logout_view(request):
    logout(request)
    messages.success(request, 'You have been successfully logged out.')
    return redirect('home')

@login_required
def dashboard_view(request):
    # Route to correct dashboard based on User role
    if request.user.is_superuser:
        return redirect('admin_dashboard')
    elif request.user.is_organization:
        return redirect('org_dashboard')
        
    from queue_system.models import Token
    from emergency_and_swap.models import SlotSwap
    from organization_panel.models import NotificationCenter
    user_tokens = Token.objects.filter(user=request.user).order_by('-booking_date', '-id')
    incoming_swaps = SlotSwap.objects.filter(target_user=request.user, status='Pending')
    unread_notifications = NotificationCenter.unread_count_for_user(request.user)
    recent_notifications = NotificationCenter.get_inbox_for_user(request.user)[:5]

    return render(request, 'accounts/dashboard.html', {
        'user_tokens': user_tokens,
        'incoming_swaps': incoming_swaps,
        'unread_notifications': unread_notifications,
        'recent_notifications': recent_notifications,
    })

def home_view(request):
    # A simple placeholder homepage view
    return render(request, 'accounts/home.html')

def org_register_view(request):
    if request.method == 'POST':
        # request.FILES is required for the logo image upload
        form = OrganizationRegistrationForm(request.POST, request.FILES)
        if form.is_valid():
            user = form.save()
            user.status = 'Pending Approval'
            user.save()
            
            # Note: We do not send an activation email here. Organizations require manual Admin approval.
            messages.success(request, 'Registration successful! Your organization account is pending Admin approval. You will not be able to log in until an Admin approves your account.')
            return redirect('org_login')
    else:
        form = OrganizationRegistrationForm()
        
    return render(request, 'accounts/org_register.html', {'form': form})

def org_login_view(request):
    if request.method == 'POST':
        form = CustomAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None and user.is_organization:
                login(request, user)
                if form.cleaned_data.get('remember_me'):
                    request.session.set_expiry(1209600)  # 2 weeks
                    
                messages.success(request, f'Welcome to your Organization Panel, {username}!')
                return redirect('org_dashboard')
            else:
                messages.error(request, 'Invalid organization credentials or unauthorized access.')
        else:
            messages.error(request, 'Invalid username or password.')
    else:
        form = CustomAuthenticationForm()
        
    return render(request, 'accounts/org_login.html', {'form': form})

@login_required
def org_dashboard_view(request):
    if not request.user.is_organization:
        messages.error(request, 'Unauthorized access.')
        return redirect('dashboard')

    from queue_system.models import Organization, QueueTracker, Token
    org_profile = request.user.organization_profile
    organization, _ = Organization.objects.get_or_create(account=org_profile)
    tracker, _ = QueueTracker.objects.get_or_create(organization=organization)

    # Call monitorQueueFlow() for live stats
    flow_stats = tracker.monitorQueueFlow()

    # Queue capacity stats via manageQueueCapacity()
    capacity = organization.manageQueueCapacity()

    # Query OrganizationDashboard for advanced health and analytics widgets
    from organization_panel.models import OrganizationDashboard
    dashboard_stats, _ = OrganizationDashboard.objects.get_or_create(organization=organization)
    monitor_stats = dashboard_stats.monitorQueue()
    gen_stats = dashboard_stats.generateDashboardStats()

    # Upcoming waiting tokens today
    import datetime
    today = datetime.date.today()
    waiting_tokens = Token.objects.filter(
        organization=organization,
        booking_date=today,
        status='Waiting'
    ).order_by('id')

    # Query QueueReport for daily analytics & performance alerts
    from organization_panel.models import QueueReport
    report = QueueReport.generateDailyReport(organization, today)
    performance = report.analyzePerformance()

    return render(request, 'accounts/org_dashboard.html', {
        'organization': organization,
        'tracker': tracker,
        'flow_stats': flow_stats,
        'capacity': capacity,
        'waiting_tokens': waiting_tokens,
        'monitor_stats': monitor_stats,
        'gen_stats': gen_stats,
        'report': report,
        'performance': performance,
    })

@login_required
def update_queue_status_view(request):
    """Legacy URL — delegates to Organization Settings."""
    if not request.user.is_organization:
        return redirect('dashboard')

    if request.method == 'POST':
        from queue_system.models import Organization
        from organization_panel.models import NotificationCenter, OrganizationDashboard
        new_status = request.POST.get('queue_status')
        org_profile = request.user.organization_profile
        organization, _ = Organization.objects.get_or_create(account=org_profile)
        dashboard = OrganizationDashboard.get_for_organization(organization)
        result = dashboard.configureQueueSettings(queue_status=new_status)
        if result.get('changes'):
            center = NotificationCenter.get_or_create_for_organization(organization)
            center.sendQueueAlert('status_change')
            messages.success(request, f'Queue status updated to "{new_status}".')
        else:
            for err in result.get('errors', ['Invalid status.']):
                messages.error(request, err)

    return redirect('org_settings')

@login_required
def call_next_token_view(request):
    """Organization calls the next waiting token."""
    if not request.user.is_organization:
        return redirect('dashboard')

    if request.method == 'POST':
        from queue_system.models import Organization
        from organization_panel.models import OrganizationDashboard
        org_profile = request.user.organization_profile
        organization, _ = Organization.objects.get_or_create(account=org_profile)
        dashboard, _ = OrganizationDashboard.objects.get_or_create(organization=organization)

        next_token = dashboard.callNextToken()
        if next_token:
            from organization_panel.models import NotificationCenter
            center = NotificationCenter.get_or_create_for_organization(organization)
            center.sendQueueAlert('token_called', token=next_token)
            messages.success(request, f'Now serving: {next_token.serial_number} ({next_token.user.username}). Customers notified.')
        else:
            messages.info(request, 'No more waiting tokens in the queue.')

    return redirect('org_dashboard')

@login_required
def skip_token_view(request, token_id=None):
    """Organization skips the specified token or the current serving token."""
    if not request.user.is_organization:
        return redirect('dashboard')

    if request.method == 'POST':
        from queue_system.models import Organization
        from organization_panel.models import OrganizationDashboard
        org_profile = request.user.organization_profile
        organization, _ = Organization.objects.get_or_create(account=org_profile)
        dashboard, _ = OrganizationDashboard.objects.get_or_create(organization=organization)

        success = dashboard.skipToken(token_id=token_id)
        if success:
            messages.success(request, 'Token has been marked as Skipped.')
        else:
            messages.warning(request, 'No token was available to skip.')

    return redirect('org_dashboard')

@login_required
def reset_queue_view(request):
    """Organization manually resets the entire day's queue."""
    if not request.user.is_organization:
        return redirect('dashboard')

    if request.method == 'POST':
        from queue_system.models import Organization
        org_profile = request.user.organization_profile
        organization, _ = Organization.objects.get_or_create(account=org_profile)
        organization.resetQueue()
        from organization_panel.models import NotificationCenter
        center = NotificationCenter.get_or_create_for_organization(organization)
        center.sendQueueAlert('queue_reset')
        messages.success(request, 'Queue has been fully reset. Customers were notified. Serial numbers restart from T-001 on the next booking.')

    return redirect('org_dashboard')

@login_required
def set_token_limit_view(request):
    """Legacy URL — delegates to OrganizationDashboard.setTokenLimit()."""
    if not request.user.is_organization:
        return redirect('dashboard')

    if request.method == 'POST':
        from queue_system.models import Organization
        from organization_panel.models import OrganizationDashboard
        org_profile = request.user.organization_profile
        organization, _ = Organization.objects.get_or_create(account=org_profile)
        dashboard = OrganizationDashboard.get_for_organization(organization)
        result = dashboard.setTokenLimit(request.POST.get('token_limit', 50))
        if result.get('success'):
            messages.success(request, f'Daily token limit updated to {result["token_limit"]}.')
        else:
            messages.error(request, result.get('error', 'Invalid token limit.'))

    return redirect('org_settings')

@login_required
def set_working_hours_view(request):
    """Legacy URL — delegates to OrganizationDashboard.manageWorkingHours()."""
    if not request.user.is_organization:
        return redirect('dashboard')

    if request.method == 'POST':
        from queue_system.models import Organization
        from organization_panel.models import OrganizationDashboard
        import datetime
        org_profile = request.user.organization_profile
        organization, _ = Organization.objects.get_or_create(account=org_profile)
        dashboard = OrganizationDashboard.get_for_organization(organization)
        ws = request.POST.get('work_start', '').strip()
        we = request.POST.get('work_end', '').strip()
        try:
            work_start = datetime.time.fromisoformat(ws) if ws else None
            work_end = datetime.time.fromisoformat(we) if we else None
            result = dashboard.manageWorkingHours(work_start=work_start, work_end=work_end)
            if result.get('success'):
                messages.success(request, f'Working hours: {result["working_hours_display"]}')
            else:
                messages.error(request, result.get('error'))
        except ValueError:
            messages.error(request, 'Invalid time format. Please use HH:MM.')

    return redirect('org_settings')

@login_required
def profile_view(request):
    if request.method == 'POST':
        form = UserProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Your profile has been updated successfully.')
            return redirect('profile')
    else:
        form = UserProfileForm(instance=request.user)
        
    return render(request, 'accounts/profile.html', {'form': form})

def activate_account_view(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save()
        messages.success(request, 'Your account has been verified! You may now log in.')
        return redirect('login')
    else:
        messages.error(request, 'The activation link is invalid or has expired.')
        return redirect('home')

@login_required
def org_live_status_api_view(request):
    """API endpoint providing real-time queue status for auto-refreshing the dashboard."""
    from django.http import JsonResponse

    if not request.user.is_organization:
        return JsonResponse({'error': 'Unauthorized'}, status=403)
        
    from queue_system.models import Organization, Token
    from organization_panel.models import OrganizationDashboard
    import datetime
    
    org_profile = request.user.organization_profile
    organization, _ = Organization.objects.get_or_create(account=org_profile)
    dashboard_stats, _ = OrganizationDashboard.objects.get_or_create(organization=organization)
    
    monitor_stats = dashboard_stats.monitorQueue()
    
    today = datetime.date.today()
    waiting_tokens = Token.objects.filter(
        organization=organization,
        booking_date=today,
        status='Waiting'
    ).order_by('id')
    
    waiting_list = []
    for t in waiting_tokens:
        waiting_list.append({
            'id': t.id,
            'serial_number': t.serial_number,
            'username': t.user.username,
            'booked_at': t.booking.created_at.strftime('%Y-%m-%d %H:%M:%S') if (t.booking and t.booking.created_at) else '—',
            'status': t.status
        })
        
    return JsonResponse({
        'health_status': monitor_stats['health_status'],
        'waiting_count': monitor_stats['waiting_count'],
        'serving_token_number': monitor_stats['serving_token_number'] or 'None',
        'serving_token_user': monitor_stats['serving_token_user'] or '',
        'queue_status': organization.queue_status,
        'waiting_list': waiting_list
    })

@login_required
def org_reports_view(request):
    """View displaying historical daily queue reports for the organization."""
    if not request.user.is_organization:
        return redirect('dashboard')
        
    from queue_system.models import Organization
    from organization_panel.models import QueueReport
    import datetime
    
    org_profile = request.user.organization_profile
    organization, _ = Organization.objects.get_or_create(account=org_profile)
    
    # Auto-generate today's report in case it hasn't been generated yet to show up-to-date stats
    QueueReport.generateDailyReport(organization, datetime.date.today())
    
    reports = QueueReport.objects.filter(organization=organization).order_by('-report_date')
    
    return render(request, 'accounts/org_reports.html', {
        'reports': reports,
        'organization': organization
    })

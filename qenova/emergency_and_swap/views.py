from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from queue_system.models import Token, Organization
from .models import EmergencyRequest, SlotSwap, EmergencyAnalytics, PriorityQueue
from .forms import EmergencyRequestForm

@login_required
def submit_emergency_view(request, token_id):
    """Customer submits an emergency request for their waiting token."""
    token = get_object_or_404(Token, id=token_id, user=request.user)

    if token.status != 'Waiting':
        messages.error(request, "Only waiting tokens can request emergency priority.")
        return redirect('dashboard')

    if hasattr(token, 'emergency_request'):
        messages.warning(request, f"You have already submitted an emergency request for this token. Current Status: {token.emergency_request.status}")
        return redirect('dashboard')

    if request.method == 'POST':
        form = EmergencyRequestForm(request.POST, request.FILES)
        if form.is_valid():
            req = form.save(commit=False)
            req.token = token
            req.save()

            if 'document' in request.FILES:
                req.uploadDocument(request.FILES['document'])

            messages.success(request, "Emergency request submitted successfully. The organization will review your uploaded proof.")
            return redirect('dashboard')
    else:
        form = EmergencyRequestForm()

    return render(request, 'emergency_and_swap/submit_emergency.html', {
        'form': form,
        'token': token,
        'organization': token.organization
    })


@login_required
def org_emergencies_view(request):
    """Organization views and manages emergency priority requests."""
    if not request.user.is_organization:
        messages.error(request, "Unauthorized access.")
        return redirect('dashboard')

    org_profile = request.user.organization_profile
    organization = get_object_or_404(Organization, account=org_profile)
    
    # Generate/update analytics
    analytics = EmergencyAnalytics.generateEmergencyReport(organization)

    # Fetch all requests for this organization
    requests = EmergencyRequest.objects.filter(token__organization=organization).order_by('-created_at')

    # Detect suspicious status on the fly for each request
    request_data = []
    for r in requests:
        chk = EmergencyAnalytics.detectFakeEmergency(r)
        request_data.append({
            'req': r,
            'is_suspicious': chk['is_suspicious'],
            'reasons': chk['reasons'],
            'recent_count': chk['recent_count']
        })

    from django.utils import timezone
    active_prios = PriorityQueue.managePriorityQueue(organization, timezone.now().date())
    total_waiting_count = Token.objects.filter(organization=organization, booking_date=timezone.now().date(), status='Waiting').count()

    trends = EmergencyAnalytics.analyzeEmergencyTrends(organization)

    return render(request, 'emergency_and_swap/org_emergencies.html', {
        'requests': request_data,
        'analytics': analytics,
        'organization': organization,
        'active_prios': active_prios,
        'total_waiting_count': total_waiting_count,
        'trends': trends
    })


@login_required
def approve_emergency_view(request, request_id):
    """Organization staff approves emergency and triggers PriorityQueue insert."""
    if not request.user.is_organization:
        messages.error(request, "Unauthorized access.")
        return redirect('dashboard')

    req = get_object_or_404(EmergencyRequest, id=request_id)
    req.approveEmergency(reviewer=request.user)
    
    # Send notification of emergency approval (we can trigger standard notification if needed)
    from queue_system.models import EmailNotification
    # Create simple custom notification
    EmailNotification.objects.create(
        user=req.token.user,
        subject="QeNova - Emergency Priority Approved!",
        message=f"Hello {req.token.user.username},\n\nYour emergency request has been APPROVED by {req.token.organization.account.organization_name}.\nYour token {req.token.serial_number} has been bumped to priority status!",
        email_type='Update'
    )

    messages.success(request, f"Emergency request for Token {req.token.serial_number} approved and priority slot assigned!")
    return redirect('org_emergencies')


@login_required
def reject_emergency_view(request, request_id):
    """Organization staff rejects emergency request."""
    if not request.user.is_organization:
        messages.error(request, "Unauthorized access.")
        return redirect('dashboard')

    req = get_object_or_404(EmergencyRequest, id=request_id)
    req.rejectEmergency(reviewer=request.user)

    from emergency_and_swap.models import EmergencyAnalytics
    from organization_panel.models import BehaviorMonitoring
    check = EmergencyAnalytics.detectFakeEmergency(req)
    if check['is_suspicious']:
        BehaviorMonitoring.recordEmergencyMisuse(req.token.user)

    messages.warning(request, f"Emergency request for Token {req.token.serial_number} has been rejected.")
    return redirect('org_emergencies')


# =========================================================================
# SLOT SWAPPING FLOW
# =========================================================================

@login_required
def swap_list_view(request, token_id):
    """Lists other waiting users in the queue that the customer can request a swap with."""
    token = get_object_or_404(Token, id=token_id, user=request.user)

    if token.status != 'Waiting':
        messages.error(request, "Only waiting tokens can swap slots.")
        return redirect('dashboard')

    # Get all other waiting tokens for the same organization today
    other_tokens = Token.objects.filter(
        organization=token.organization,
        booking_date=token.booking_date,
        status='Waiting'
    ).exclude(id=token.id).order_by('id')

    # Fetch swap requests initiated by this token
    sent_requests = SlotSwap.objects.filter(current_slot=token)
    sent_target_ids = [s.requested_slot.id for s in sent_requests]

    return render(request, 'emergency_and_swap/swap_list.html', {
        'token': token,
        'other_tokens': other_tokens,
        'sent_target_ids': sent_target_ids,
        'organization': token.organization
    })


@login_required
def request_swap_view(request, token_id, target_token_id):
    """Initiates a swap request with another waiting user."""
    token = get_object_or_404(Token, id=token_id, user=request.user)
    target_token = get_object_or_404(Token, id=target_token_id)

    # Validate swap
    temp_swap = SlotSwap(current_slot=token, requested_slot=target_token)
    if not temp_swap.validateSwap():
        messages.error(request, "Invalid swap request. Both slots must be waiting for the same date and organization.")
        return redirect('swap_list', token_id=token_id)

    # Check for active duplicate request
    if SlotSwap.objects.filter(current_slot=token, requested_slot=target_token, status='Pending').exists():
        messages.warning(request, "Swap request is already pending.")
        return redirect('swap_list', token_id=token_id)

    # Create swap request
    SlotSwap.requestSwap(token, target_token)
    messages.success(request, f"Swap request sent to user {target_token.user.username} (Token {target_token.serial_number})!")
    return redirect('swap_list', token_id=token_id)


@login_required
def approve_swap_view(request, swap_id):
    """Target user approves the swap request."""
    swap = get_object_or_404(SlotSwap, id=swap_id, target_user=request.user)

    if swap.status != 'Pending':
        messages.error(request, "This swap request has already been processed.")
        return redirect('dashboard')

    if swap.approveSwap():
        messages.success(request, "Swap request approved successfully! Your slots have been exchanged.")
    else:
        messages.error(request, "Failed to approve swap. The slots may no longer be active or valid.")

    return redirect('dashboard')


@login_required
def reject_swap_view(request, swap_id):
    """Target user rejects the swap request."""
    swap = get_object_or_404(SlotSwap, id=swap_id, target_user=request.user)

    if swap.status != 'Pending':
        messages.error(request, "This swap request has already been processed.")
        return redirect('dashboard')

    swap.rejectSwap()
    messages.warning(request, f"You rejected the swap request from {swap.requester.username}.")
    return redirect('dashboard')


@login_required
def adjust_priority_position_view(request, priority_id):
    """Organization staff manually adjusts the insertion position of a priority token."""
    if not request.user.is_organization:
        messages.error(request, "Unauthorized access.")
        return redirect('dashboard')

    priority_rec = get_object_or_404(PriorityQueue, id=priority_id)
    if request.method == 'POST':
        new_position = int(request.POST.get('new_position', 1))
        if priority_rec.adjustPriorityPosition(new_position):
            messages.success(request, f"Priority position for Token {priority_rec.token.serial_number} updated to {new_position}.")
        else:
            messages.error(request, "Invalid position. Could not adjust priority position.")

    return redirect('org_emergencies')

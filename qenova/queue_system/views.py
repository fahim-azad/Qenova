from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from accounts.models import OrganizationUser
from .models import Organization, QueueBooking, Token, QueueTracker, Feedback
from .forms import QueueBookingForm, RescheduleBookingForm, FeedbackForm
import datetime

def org_list_view(request):
    query = request.GET.get('q', '')
    
    # Only show organizations that are active (approved)
    organizations = OrganizationUser.objects.filter(user__is_active=True)
    
    if query:
        organizations = organizations.filter(organization_name__icontains=query)
        
    return render(request, 'queue_system/org_list.html', {
        'organizations': organizations,
        'query': query
    })

def org_detail_view(request, org_id):
    # Fetch the organization, ensuring it's active
    org_user = get_object_or_404(OrganizationUser, id=org_id, user__is_active=True)
    
    # Get or create the Queue System Organization profile
    organization, created = Organization.objects.get_or_create(account=org_user)
    capacity = organization.manageQueueCapacity()
    
    # Fetch feedback history and average rating (Blueprint: viewFeedbackHistory & calculateRating)
    feedbacks = Feedback.viewFeedbackHistory(organization)
    avg_rating = Feedback.calculateRating(organization)
    
    # Check if user already submitted feedback
    user_feedback = None
    if request.user.is_authenticated and not request.user.is_organization:
        user_feedback = Feedback.objects.filter(user=request.user, organization=organization).first()
    
    return render(request, 'queue_system/org_detail.html', {
        'organization': organization,
        'org_user': org_user,
        'capacity': capacity,
        'feedbacks': feedbacks,
        'avg_rating': avg_rating,
        'user_feedback': user_feedback,
    })

@login_required
def book_queue_view(request, org_id):
    if request.user.is_organization:
        messages.error(request, "Organizations cannot book queues.")
        return redirect('org_list')

    # Security check: Block blacklisted users
    from organization_panel.models import BehaviorMonitoring
    behavior_record, _ = BehaviorMonitoring.objects.get_or_create(user=request.user)
    if behavior_record.is_blacklisted:
        messages.error(request, "Access Denied: Your account has been suspended/blacklisted due to poor queue discipline and security violations.")
        return redirect('org_detail', org_id=org_id)

    org_user = get_object_or_404(OrganizationUser, id=org_id, user__is_active=True)
    organization, _ = Organization.objects.get_or_create(account=org_user)

    if organization.queue_status != 'Active':
        messages.error(request, f"Sorry, the queue for {org_user.organization_name} is currently {organization.queue_status}.")
        return redirect('org_detail', org_id=org_id)

    # Working Hour Validation
    if not organization.isWithinWorkingHours():
        hours = organization.getWorkingHoursDisplay()
        messages.error(request, f"Booking is only allowed during working hours: {hours}.")
        return redirect('org_detail', org_id=org_id)

    if request.method == 'POST':
        form = QueueBookingForm(request.POST)
        if form.is_valid():
            is_instant = form.cleaned_data.get('is_instant')
            booking_date = datetime.date.today() if is_instant else form.cleaned_data.get('booking_date')
            
            if not booking_date:
                booking_date = datetime.date.today()

            # Blueprint: Check Availability
            if not QueueBooking.checkAvailability(organization, booking_date):
                messages.error(request, "Sorry, this organization has reached its daily token limit for this date.")
                return render(request, 'queue_system/booking_form.html', {'form': form, 'organization': organization})

            # Blueprint: Prevent Duplicate Bookings
            if QueueBooking.objects.filter(user=request.user, organization=organization, booking_date=booking_date).exists():
                messages.error(request, "You already have an active booking for this organization on this date.")
                return redirect('org_detail', org_id=org_id)

            # Blueprint: bookQueue()
            booking = QueueBooking(
                user=request.user,
                organization=organization,
                booking_date=booking_date
            )
            booking.bookQueue()

            # Blueprint: Generate Token
            # Count how many tokens exist for this org on this date to generate serial
            token_count = Token.objects.filter(organization=organization, booking_date=booking_date).count()
            serial = f"T-{token_count + 1:03d}"
            
            token = Token(
                user=request.user,
                organization=organization,
                booking=booking,
                booking_date=booking_date
            )
            token.assignSerial(serial)

            # Calculate estimated time immediately after booking
            token.calculateEstimatedTime()

            # Send confirmation email (Blueprint: sendTokenConfirmation)
            from .models import EmailNotification
            EmailNotification.sendTokenConfirmation(token)

            messages.success(request, f"Queue booked successfully! Your Token is {serial}. A confirmation email has been sent.")
            return redirect('booking_success', token_id=token.id)

    else:
        form = QueueBookingForm()

    return render(request, 'queue_system/booking_form.html', {
        'form': form,
        'organization': organization,
        'org_user': org_user
    })

@login_required
def booking_success_view(request, token_id):
    token = get_object_or_404(Token, id=token_id, user=request.user)
    # Recalculate estimated time on success page
    estimated = token.calculateEstimatedTime()
    return render(request, 'queue_system/booking_success.html', {
        'token': token,
        'estimated_time': estimated.strftime('%I:%M %p') if estimated else 'N/A',
    })

from django.http import JsonResponse

@login_required
def queue_status_api(request, org_id):
    org_user = get_object_or_404(OrganizationUser, id=org_id, user__is_active=True)
    organization, _ = Organization.objects.get_or_create(account=org_user)
    tracker, _ = QueueTracker.objects.get_or_create(organization=organization)
    
    # Refresh queue states
    tracker.refreshQueue()
    
    # Find upcoming tokens
    import datetime
    today = datetime.date.today()
    upcoming_tokens = Token.objects.filter(
        organization=organization,
        booking_date=today,
        status='Waiting'
    ).order_by('id')[:5]
    
    upcoming_list = [t.serial_number for t in upcoming_tokens]
    
    # User's active token position
    user_token = Token.objects.filter(
        organization=organization,
        user=request.user,
        booking_date=today,
        status='Waiting'
    ).first()
    
    user_position = None
    estimated_time_str = None
    suggested_arrival_str = None
    if user_token:
        # Count how many waiting tokens are ahead of the user's token
        user_position = Token.objects.filter(
            organization=organization,
            booking_date=today,
            status='Waiting',
            id__lt=user_token.id
        ).count() + 1  # Position is index + 1

        # Calculate estimated time and suggested arrival
        estimated_dt = user_token.calculateEstimatedTime()
        if estimated_dt:
            estimated_time_str = estimated_dt.strftime('%I:%M %p')
            suggested_arrival_str = tracker.getSuggestedArrivalTime(user_token)
        
    data = {
        'current_token': tracker.current_token.serial_number if tracker.current_token else 'None',
        'queue_load': tracker.queue_load,
        'waiting_time': tracker.waiting_time,
        'upcoming_tokens': upcoming_list,
        'user_position': user_position,
        'user_token_serial': user_token.serial_number if user_token else None,
        'estimated_time': estimated_time_str,
        'suggested_arrival': suggested_arrival_str,
    }
    return JsonResponse(data)



@login_required
def cancel_booking_view(request, booking_id):
    booking = get_object_or_404(QueueBooking, id=booking_id, user=request.user)
    if request.method == 'POST':
        org_name = booking.organization.account.organization_name
        booking.cancelQueue()
        messages.success(request, f'Your queue booking at {org_name} has been cancelled.')
        return redirect('dashboard')
    return render(request, 'queue_system/cancel_confirm.html', {'booking': booking})


@login_required
def reschedule_booking_view(request, booking_id):
    booking = get_object_or_404(QueueBooking, id=booking_id, user=request.user)
    if request.method == 'POST':
        form = RescheduleBookingForm(request.POST)
        if form.is_valid():
            new_date = form.cleaned_data['new_date']
            if QueueBooking.objects.filter(user=request.user, organization=booking.organization, booking_date=new_date).exclude(id=booking.id).exists():
                messages.error(request, 'You already have a booking for this organization on that date.')
                return render(request, 'queue_system/reschedule_form.html', {'form': form, 'booking': booking})
            if not QueueBooking.checkAvailability(booking.organization, new_date):
                messages.error(request, 'Sorry, the queue is full on that date.')
                return render(request, 'queue_system/reschedule_form.html', {'form': form, 'booking': booking})
            booking.rescheduleQueue(new_date)
            messages.success(request, f'Your booking has been rescheduled to {new_date}.')
            return redirect('dashboard')
    else:
        form = RescheduleBookingForm()
    return render(request, 'queue_system/reschedule_form.html', {'form': form, 'booking': booking})

@login_required
def analytics_view(request, org_id):
    org_user = get_object_or_404(OrganizationUser, id=org_id, user__is_active=True)
    organization, _ = Organization.objects.get_or_create(account=org_user)

    # Only allow the owning organization or staff to view analytics
    if not (request.user.is_superuser or (request.user.is_organization and org_user.user == request.user)):
        messages.error(request, 'You are not authorized to view these analytics.')
        return redirect('org_list')

    from .models import QueueAnalytics
    # Generate today's analytics
    today_analytics = QueueAnalytics.generateAnalytics(organization)

    # 7-day traffic flow
    weekly_flow = QueueAnalytics.monitorQueueFlow(organization, days=7)

    # Peak hour label
    peak_hour = today_analytics.peak_hour
    peak_label = None
    if peak_hour is not None:
        import datetime
        t = datetime.time(hour=peak_hour)
        peak_label = t.strftime('%I:00 %p')

    # Completion rate
    completion_rate = 0
    if today_analytics.total_tokens > 0:
        completion_rate = round((today_analytics.completed_tokens / today_analytics.total_tokens) * 100, 1)

    return render(request, 'queue_system/analytics.html', {
        'org_user': org_user,
        'organization': organization,
        'today': today_analytics,
        'weekly_flow': weekly_flow,
        'peak_label': peak_label,
        'completion_rate': completion_rate,
    })


@login_required
def submit_feedback_view(request, org_id):
    """Customer submits feedback for a visited organization (Blueprint: submitFeedback)"""
    if request.user.is_organization:
        messages.error(request, "Organizations cannot submit feedback.")
        return redirect('org_list')

    org_user = get_object_or_404(OrganizationUser, id=org_id, user__is_active=True)
    organization, _ = Organization.objects.get_or_create(account=org_user)

    # Fetch existing feedback if any to pre-populate or update
    feedback_instance = Feedback.objects.filter(user=request.user, organization=organization).first()

    if request.method == 'POST':
        form = FeedbackForm(request.POST, instance=feedback_instance)
        if form.is_valid():
            rating = form.cleaned_data['rating']
            comment = form.cleaned_data['comment']
            
            # Use Blueprint method: submitFeedback()
            feedback, created = Feedback.submitFeedback(
                user=request.user,
                organization=organization,
                rating=rating,
                comment=comment
            )
            
            if created:
                messages.success(request, f"Feedback submitted successfully for {org_user.organization_name}!")
            else:
                messages.success(request, f"Feedback updated successfully for {org_user.organization_name}!")
                
            return redirect('org_detail', org_id=org_id)
    else:
        form = FeedbackForm(instance=feedback_instance)

    return render(request, 'queue_system/feedback_form.html', {
        'form': form,
        'org_user': org_user,
        'organization': organization,
        'feedback': feedback_instance
    })


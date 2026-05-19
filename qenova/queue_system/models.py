from django.db import models
from django.conf import settings
from accounts.models import OrganizationUser

class Organization(models.Model):
    """
    App 2 Blueprint: Organization
    Extends the base OrganizationUser with queue-specific settings.
    """
    # Link to the auth OrganizationUser
    account = models.OneToOneField(OrganizationUser, on_delete=models.CASCADE, related_name='queue_profile')
    
    # Blueprint Attributes
    type = models.CharField(max_length=100, default='General')
    token_limit = models.IntegerField(default=50, help_text="Maximum tokens per day")
    queue_status = models.CharField(max_length=50, default='Active', choices=[
        ('Active', 'Active'),
        ('Paused', 'Paused'),
        ('Closed', 'Closed')
    ])
    # Structured working hours (24h format, e.g. 09:00 to 17:00)
    work_start = models.TimeField(null=True, blank=True, help_text="Queue opens at (e.g. 09:00)")
    work_end = models.TimeField(null=True, blank=True, help_text="Queue closes at (e.g. 17:00)")

    def __str__(self):
        return f"{self.account.organization_name} ({self.type})"

    def isWithinWorkingHours(self):
        """
        Returns True if the current time is within the organization's working hours.
        If work_start/work_end are not set, always returns True (no restriction).
        """
        if not self.work_start or not self.work_end:
            return True  # No restriction set
        import datetime
        now = datetime.datetime.now().time()
        return self.work_start <= now <= self.work_end

    def getWorkingHoursDisplay(self):
        """Returns a human-readable working hours string."""
        if self.work_start and self.work_end:
            return f"{self.work_start.strftime('%I:%M %p')} – {self.work_end.strftime('%I:%M %p')}"
        return "Not set (open all day)"

    # Blueprint Methods
    def setTokenLimit(self, limit):
        self.token_limit = limit
        self.save()

    def resetQueue(self):
        """
        Fully resets the daily queue for this organization:
        1. Marks all remaining 'Waiting' tokens as 'Skipped' (expired).
        2. Clears the current_token in the tracker.
        3. Resets queue_load and waiting_time to 0.
        4. Sets queue_status back to 'Active' so new bookings can start.
        Serial numbers will restart from T-001 automatically on the next booking.
        """
        import datetime
        today = datetime.date.today()

        # Step 1: Expire all remaining Waiting tokens for today
        Token.objects.filter(
            organization=self,
            booking_date=today,
            status='Waiting'
        ).update(status='Skipped')

        # Step 2: Also expire any 'Serving' token
        Token.objects.filter(
            organization=self,
            booking_date=today,
            status='Serving'
        ).update(status='Skipped')

        # Step 3: Reset the tracker
        try:
            tracker = self.tracker
            tracker.current_token = None
            tracker.queue_load = 0
            tracker.waiting_time = 0
            tracker.save()
        except Exception:
            pass

        # Step 4: Re-activate queue
        self.queue_status = 'Active'
        self.save()

    def updateQueueStatus(self, status):
        self.queue_status = status
        self.save()

    def manageQueueCapacity(self):
        """
        Returns a dict with capacity data for today:
        - total_booked: tokens issued today
        - remaining: how many slots are left
        - is_full: True if limit reached
        """
        import datetime
        today = datetime.date.today()
        total_booked = Token.objects.filter(
            organization=self,
            booking_date=today
        ).exclude(status='Skipped').count()
        remaining = max(0, self.token_limit - total_booked)
        return {
            'total_booked': total_booked,
            'remaining': remaining,
            'is_full': remaining == 0,
            'token_limit': self.token_limit,
        }


class QueueBooking(models.Model):
    """
    App 2 Blueprint: QueueBooking
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bookings')
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='bookings')
    booking_date = models.DateField()
    queue_position = models.IntegerField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Booking {self.id} for {self.user.username} at {self.organization.account.organization_name}"

    # Blueprint Methods
    def bookQueue(self):
        self.save()

    def cancelQueue(self):
        """Cancels this booking and marks the linked token as Skipped."""
        try:
            # Mark the linked Token as Skipped instead of deleting it (for history)
            self.token.updateStatus('Skipped')
            from organization_panel.models import BehaviorMonitoring
            BehaviorMonitoring.recordCancellation(self.user)
        except Exception:
            pass
        self.delete()

    def rescheduleQueue(self, new_date):
        """Reschedules this booking to a new date and updates the linked token."""
        self.booking_date = new_date
        self.save()
        try:
            self.token.booking_date = new_date
            self.token.save()
            # Recalculate estimated time for the new date
            self.token.calculateEstimatedTime()
        except Exception:
            pass

    @classmethod
    def checkAvailability(cls, organization, date):
        count = cls.objects.filter(organization=organization, booking_date=date).count()
        return count < organization.token_limit

    @classmethod
    def expireOldBookings(cls):
        """Marks all tokens from past dates still in 'Waiting' as 'Skipped' (handles queue expiry)."""
        import datetime
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        from queue_system.models import Token
        expired = Token.objects.filter(
            booking_date__lte=yesterday,
            status='Waiting'
        )
        count = expired.update(status='Skipped')
        return count


class Token(models.Model):
    """
    App 2 Blueprint: Token
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='tokens')
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='tokens')
    booking = models.OneToOneField(QueueBooking, on_delete=models.CASCADE, related_name='token', null=True, blank=True)
    
    serial_number = models.CharField(max_length=20)
    booking_date = models.DateField()
    status = models.CharField(max_length=50, default='Waiting', choices=[
        ('Waiting', 'Waiting'),
        ('Serving', 'Serving'),
        ('Completed', 'Completed'),
        ('Skipped', 'Skipped')
    ])
    estimated_time = models.DateTimeField(null=True, blank=True)
    served_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Token {self.serial_number} for {self.user.username}"

    # Blueprint Methods
    def assignSerial(self, serial):
        self.serial_number = serial
        self.save()

    def updateStatus(self, new_status):
        from django.utils import timezone
        import datetime
        self.status = new_status
        if new_status == 'Serving':
            self.served_at = timezone.now()
            if self.estimated_time and self.served_at > self.estimated_time + datetime.timedelta(minutes=5):
                from organization_panel.models import BehaviorMonitoring
                profile, _ = BehaviorMonitoring.objects.get_or_create(user=self.user)
                profile.trackLateArrival()
        elif new_status == 'Completed':
            self.completed_at = timezone.now()
        elif new_status == 'Skipped':
            from organization_panel.models import BehaviorMonitoring
            profile, _ = BehaviorMonitoring.objects.get_or_create(user=self.user)
            profile.detectNoShow()
        self.save()

    def calculateEstimatedTime(self):
        """
        Calculates the estimated DateTime this token will be served.
        Based on: now + (number of waiting tokens ahead * 5 minutes per token)
        Saves the result to self.estimated_time.
        """
        import datetime
        tokens_ahead = Token.objects.filter(
            organization=self.organization,
            booking_date=self.booking_date,
            status='Waiting',
            id__lt=self.id  # Tokens booked before this one
        ).count()
        
        minutes_to_wait = tokens_ahead * 5  # 5 minutes per token
        estimated_arrival = datetime.datetime.now() + datetime.timedelta(minutes=minutes_to_wait)
        self.estimated_time = estimated_arrival
        self.save()
        return estimated_arrival

    def validateToken(self):
        return self.status == 'Waiting'


class QueueTracker(models.Model):
    """
    App 2 Blueprint: QueueTracker
    """
    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name='tracker')
    current_token = models.ForeignKey(Token, on_delete=models.SET_NULL, null=True, blank=True, related_name='currently_tracked')
    queue_load = models.IntegerField(default=0)
    waiting_time = models.IntegerField(default=0, help_text="Estimated waiting time in minutes")

    def __str__(self):
        return f"Tracker for {self.organization.account.organization_name}"

    # Blueprint Methods
    def trackQueue(self):
        # queue_load is the number of tokens with 'Waiting' status today
        import datetime
        today = datetime.date.today()
        waiting_count = Token.objects.filter(
            organization=self.organization,
            booking_date=today,
            status='Waiting'
        ).count()
        self.queue_load = waiting_count
        
        # average service time estimated as 5 minutes per token
        self.waiting_time = waiting_count * 5
        self.save()

    def refreshQueue(self):
        # Find the token with 'Serving' status for today, if any
        import datetime
        today = datetime.date.today()
        serving_token = Token.objects.filter(
            organization=self.organization,
            booking_date=today,
            status='Serving'
        ).first()
        
        self.current_token = serving_token
        self.trackQueue() # This saves it as well

    def showCurrentToken(self):
        return self.current_token

    def calculateWaitingTime(self):
        """
        Calculates and returns the total estimated waiting time in minutes
        based on how many tokens are currently waiting.
        """
        self.trackQueue()
        return self.waiting_time

    def getSuggestedArrivalTime(self, user_token):
        """
        Suggests the best time to arrive for a given user's token.
        Returns a formatted datetime string.
        """
        import datetime
        estimated = user_token.calculateEstimatedTime()
        # Suggest arriving 5 minutes before their token is served
        suggested = estimated - datetime.timedelta(minutes=5)
        return suggested.strftime('%I:%M %p')  # e.g., '03:45 PM'

    def monitorQueueFlow(self):
        """
        Returns a summary of the current queue flow state.
        Used by the organization dashboard to monitor health.
        """
        import datetime
        today = datetime.date.today()
        waiting = Token.objects.filter(organization=self.organization, booking_date=today, status='Waiting').count()
        serving = Token.objects.filter(organization=self.organization, booking_date=today, status='Serving').count()
        completed = Token.objects.filter(organization=self.organization, booking_date=today, status='Completed').count()
        skipped = Token.objects.filter(organization=self.organization, booking_date=today, status='Skipped').count()

        # Determine queue health
        limit = self.organization.token_limit
        if waiting == 0:
            health = 'Empty'
        elif waiting < limit * 0.5:
            health = 'Normal'
        elif waiting < limit * 0.8:
            health = 'Busy'
        else:
            health = 'Overloaded'

        return {
            'waiting': waiting,
            'serving': serving,
            'completed': completed,
            'skipped': skipped,
            'health': health,
            'queue_status': self.organization.queue_status,
        }


class QueueAnalytics(models.Model):
    """
    App 2 Blueprint: QueueAnalytics
    Stores computed analytics per organization per day.
    """
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='analytics')
    date = models.DateField()

    # Blueprint Attributes
    total_tokens = models.IntegerField(default=0)
    completed_tokens = models.IntegerField(default=0)
    skipped_tokens = models.IntegerField(default=0)
    peak_hour = models.IntegerField(null=True, blank=True, help_text="Hour of the day (0-23) with most bookings")

    class Meta:
        unique_together = ('organization', 'date')
        ordering = ['-date']

    def __str__(self):
        return f"Analytics for {self.organization.account.organization_name} on {self.date}"

    @classmethod
    def generateAnalytics(cls, organization, date=None):
        """
        Computes and stores analytics for a given organization and date.
        Returns the QueueAnalytics instance.
        """
        import datetime
        if date is None:
            date = datetime.date.today()

        tokens = Token.objects.filter(organization=organization, booking_date=date)
        total = tokens.count()
        completed = tokens.filter(status='Completed').count()
        skipped = tokens.filter(status='Skipped').count()
        peak = cls.detectPeakHour(organization, date)

        analytics, _ = cls.objects.update_or_create(
            organization=organization,
            date=date,
            defaults={
                'total_tokens': total,
                'completed_tokens': completed,
                'skipped_tokens': skipped,
                'peak_hour': peak,
            }
        )
        return analytics

    @classmethod
    def detectPeakHour(cls, organization, date=None):
        """
        Detects the hour of the day with the most bookings.
        Returns an integer (0-23) or None if no bookings.
        """
        import datetime
        from django.db.models import Count
        from django.db.models.functions import ExtractHour
        if date is None:
            date = datetime.date.today()

        result = (
            QueueBooking.objects
            .filter(organization=organization, booking_date=date)
            .annotate(hour=ExtractHour('created_at'))
            .values('hour')
            .annotate(count=Count('id'))
            .order_by('-count')
            .first()
        )
        return result['hour'] if result else None

    @classmethod
    def monitorQueueFlow(cls, organization, days=7):
        """
        Returns a list of analytics dicts for the past N days.
        Used for trend monitoring.
        """
        import datetime
        today = datetime.date.today()
        records = []
        for i in range(days - 1, -1, -1):
            day = today - datetime.timedelta(days=i)
            tokens = Token.objects.filter(organization=organization, booking_date=day)
            records.append({
                'date': str(day),
                'total': tokens.count(),
                'completed': tokens.filter(status='Completed').count(),
                'skipped': tokens.filter(status='Skipped').count(),
            })
        return records




class EmailNotification(models.Model):
    """
    App 2 Blueprint: EmailNotification
    Records every email sent for persistent notification history.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications'
    )
    subject = models.CharField(max_length=255)
    message = models.TextField()
    sent_at = models.DateTimeField(auto_now_add=True)
    email_type = models.CharField(max_length=50, default='General', choices=[
        ('Confirmation', 'Token Confirmation'),
        ('Update', 'Queue Update'),
        ('Reminder', 'Queue Reminder'),
    ])

    def __str__(self):
        return f"[{self.email_type}] to {self.user.email}"

    @classmethod
    def sendTokenConfirmation(cls, token):
        """Sends booking confirmation immediately after a token is issued."""
        from django.core.mail import send_mail
        user = token.user
        org_name = token.organization.account.organization_name
        est = (
            token.estimated_time.strftime('%I:%M %p')
            if token.estimated_time else 'To be calculated'
        )
        subject = 'QeNova - Queue Booking Confirmed: ' + token.serial_number
        lines = [
            'Hello ' + user.username + ',',
            '',
            'Your queue booking has been confirmed!',
            '',
            'Organization : ' + org_name,
            'Token Number : ' + token.serial_number,
            'Booking Date : ' + str(token.booking_date),
            'Status       : ' + token.status,
            'Estimated Time: ' + est,
            '',
            'Please arrive a few minutes before your estimated serving time.',
            '',
            'Thank you for using QeNova!',
        ]
        body = '\n'.join(lines)
        send_mail(subject, body, None, [user.email], fail_silently=True)
        cls.objects.create(user=user, subject=subject, message=body, email_type='Confirmation')

    @classmethod
    def sendQueueUpdate(cls, token):
        """Sends a live update when the customer's token starts being served."""
        from django.core.mail import send_mail
        user = token.user
        org_name = token.organization.account.organization_name
        subject = 'QeNova - Token ' + token.serial_number + ' is Now Being Served'
        lines = [
            'Hello ' + user.username + ',',
            '',
            'Your token is now being served.',
            '',
            'Organization : ' + org_name,
            'Token Number : ' + token.serial_number,
            'Status       : Serving',
            '',
            'Please proceed to the service counter immediately.',
            '',
            'Thank you for using QeNova!',
        ]
        body = '\n'.join(lines)
        send_mail(subject, body, None, [user.email], fail_silently=True)
        cls.objects.create(user=user, subject=subject, message=body, email_type='Update')

    @classmethod
    def sendReminder(cls, token):
        """Sends a heads-up reminder when a token is close to being served."""
        from django.core.mail import send_mail
        user = token.user
        org_name = token.organization.account.organization_name
        subject = 'QeNova - Reminder: Token ' + token.serial_number + ' Coming Up Soon'
        lines = [
            'Hello ' + user.username + ',',
            '',
            'Your queue turn is approaching soon.',
            '',
            'Organization : ' + org_name,
            'Token Number : ' + token.serial_number,
            'Booking Date : ' + str(token.booking_date),
            '',
            'Please make sure you are near the service area.',
            '',
            'Thank you for using QeNova!',
        ]
        body = '\n'.join(lines)
        send_mail(subject, body, None, [user.email], fail_silently=True)
        cls.objects.create(user=user, subject=subject, message=body, email_type='Reminder')


class Feedback(models.Model):
    """
    App 2 Blueprint: Feedback
    Submitted by customers after their queue experience.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='feedbacks'
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='feedbacks'
    )
    rating = models.IntegerField(
        choices=[(1, '1 Star'), (2, '2 Stars'), (3, '3 Stars'), (4, '4 Stars'), (5, '5 Stars')]
    )
    comment = models.TextField(blank=True, null=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-submitted_at']
        unique_together = ('user', 'organization')

    def __str__(self):
        return f"{self.user.username} -> {self.organization.account.organization_name} ({self.rating} stars)"

    @classmethod
    def submitFeedback(cls, user, organization, rating, comment=''):
        """
        Creates or updates a feedback entry for a user-organization pair.
        Users can only submit one feedback per organization.
        Returns the Feedback instance and a boolean (True=created, False=updated).
        """
        feedback, created = cls.objects.update_or_create(
            user=user,
            organization=organization,
            defaults={'rating': rating, 'comment': comment}
        )
        return feedback, created

    @classmethod
    def viewFeedbackHistory(cls, organization):
        """
        Returns all feedback records for a given organization, newest first.
        """
        return cls.objects.filter(organization=organization).select_related('user')

    @classmethod
    def calculateRating(cls, organization):
        """
        Calculates the average rating for a given organization.
        Returns a float rounded to 1 decimal place, or None if no feedback exists.
        """
        from django.db.models import Avg
        result = cls.objects.filter(organization=organization).aggregate(Avg('rating'))
        avg = result.get('rating__avg')
        return round(avg, 1) if avg is not None else None

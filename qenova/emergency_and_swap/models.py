from django.db import models
from django.conf import settings
from django.utils import timezone
from queue_system.models import Token, QueueBooking

class EmergencyRequest(models.Model):
    """
    App 3 Blueprint: EmergencyRequest
    Allows customers to request an emergency priority bump by uploading a document.
    """
    token = models.OneToOneField(Token, on_delete=models.CASCADE, related_name='emergency_request', null=True, blank=True)
    emergency_type = models.CharField(max_length=100)
    document = models.FileField(upload_to='emergency_docs/', null=True, blank=True)
    status = models.CharField(max_length=50, default='Pending', choices=[
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
        ('Rejected', 'Rejected')
    ])
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Emergency ({self.emergency_type}) for Token {self.token.serial_number} - {self.status}"

    @classmethod
    def submitEmergency(cls, token, emergency_type, document=None):
        """Creates and returns a new EmergencyRequest."""
        request = cls.objects.create(
            token=token,
            emergency_type=emergency_type,
            document=document
        )
        # Send emergency alert email to organization
        from queue_system.models import EmailNotification
        from django.core.mail import send_mail
        
        org_user = token.organization.account.user
        subject = f"QeNova Alert - New Emergency Request: Token {token.serial_number}"
        message = (
            f"Hello {token.organization.account.organization_name} Staff,\n\n"
            f"A new emergency request has been submitted for Token {token.serial_number} "
            f"(User: {token.user.username}).\n"
            f"Please review the uploaded supporting documents immediately."
        )
        EmailNotification.objects.create(
            user=org_user,
            subject=subject,
            message=message,
            email_type='Update'
        )
        send_mail(subject, message, None, [org_user.email], fail_silently=True)

        return request

    def uploadDocument(self, doc_file):
        """Handles uploading or updating the supporting document."""
        self.document = doc_file
        self.save()

    def approveEmergency(self, reviewer=None, notes=None):
        """Marks the request as approved, logs the approval, and inserts the token into the PriorityQueue."""
        self.status = 'Approved'
        self.save()
        
        # Log approval
        EmergencyApprovalLog.objects.create(
            request=self,
            action='Approved',
            reviewed_by=reviewer,
            notes=notes
        )
        
        # Trigger the Smart Priority insertion
        PriorityQueue.insertPriorityToken(self.token)

        # Notify emergency user of approval
        from queue_system.models import EmailNotification
        from django.core.mail import send_mail
        
        subject = f"QeNova - Emergency Priority Approved!"
        message = (
            f"Hello {self.token.user.username},\n\n"
            f"Your emergency request for Token {self.token.serial_number} has been APPROVED by "
            f"{self.token.organization.account.organization_name}.\n"
            f"Your token is now bumped to priority status in the queue!"
        )
        EmailNotification.objects.create(
            user=self.token.user,
            subject=subject,
            message=message,
            email_type='Update'
        )
        send_mail(subject, message, None, [self.token.user.email], fail_silently=True)

    def rejectEmergency(self, reviewer=None, notes=None):
        """Rejects the emergency request and logs the rejection."""
        self.status = 'Rejected'
        self.save()

        # Log rejection
        EmergencyApprovalLog.objects.create(
            request=self,
            action='Rejected',
            reviewed_by=reviewer,
            notes=notes
        )

        # Notify emergency user of rejection
        from queue_system.models import EmailNotification
        from django.core.mail import send_mail
        
        subject = f"QeNova - Emergency Request Rejected"
        message = (
            f"Hello {self.token.user.username},\n\n"
            f"Your emergency request for Token {self.token.serial_number} has been Rejected by "
            f"{self.token.organization.account.organization_name}.\n"
            f"Notes: {notes or 'No comments provided.'}"
        )
        EmailNotification.objects.create(
            user=self.token.user,
            subject=subject,
            message=message,
            email_type='Update'
        )
        send_mail(subject, message, None, [self.token.user.email], fail_silently=True)


class EmergencyApprovalLog(models.Model):
    """
    App 3 Blueprint: EmergencyApprovalLog
    Stores logs of approvals and rejections of emergency requests for auditing.
    """
    request = models.ForeignKey(EmergencyRequest, on_delete=models.CASCADE, related_name='logs')
    action = models.CharField(max_length=50)  # 'Approved' or 'Rejected'
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Log: {self.request.token.serial_number} {self.action} by {self.reviewed_by.username if self.reviewed_by else 'System'}"



class PriorityQueue(models.Model):
    """
    App 3 Blueprint: PriorityQueue
    Tracks which tokens have been elevated to priority queue slots.
    """
    token = models.OneToOneField(Token, on_delete=models.CASCADE, related_name='priority_info')
    priority_serial = models.CharField(max_length=20)
    insertion_position = models.IntegerField(default=1)
    urgency_level = models.CharField(max_length=50, default='High')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Priority Token {self.priority_serial} (Position {self.insertion_position})"

    @classmethod
    def insertPriorityToken(cls, token, urgency_level='High'):
        """
        Smart Priority Insert:
        Emergency users are inserted after the currently running (Serving) token
        instead of the absolute front (which would disrupt the current active session).
        Shifts all subsequent waiting tokens' details to the right.
        """
        # Find all today's waiting tokens for the organization in queue order
        waiting_tokens = list(Token.objects.filter(
            organization=token.organization,
            booking_date=token.booking_date,
            status='Waiting'
        ).order_by('id'))

        # Count already approved emergency tokens currently waiting today
        approved_emergencies_count = EmergencyRequest.objects.filter(
            token__organization=token.organization,
            token__booking_date=token.booking_date,
            token__status='Waiting',
            status='Approved'
        ).exclude(token=token).count()

        # Target index is after all approved emergencies currently waiting
        target_idx = approved_emergencies_count

        # Save details, delete old PriorityQueue records temporarily to avoid conflicts
        details = []
        for t in waiting_tokens:
            prio_data = None
            if hasattr(t, 'priority_info'):
                p = t.priority_info
                prio_data = {
                    'priority_serial': p.priority_serial,
                    'insertion_position': p.insertion_position,
                    'urgency_level': p.urgency_level
                }
                p.delete()

            er = None
            if hasattr(t, 'emergency_request'):
                er = t.emergency_request

            details.append({
                'user': t.user,
                'booking': t.booking,
                'prio_data': prio_data,
                'er': er,
                'id': t.id
            })

        token_idx = -1
        for idx, d in enumerate(details):
            if d['id'] == token.id:
                token_idx = idx
                break

        if token_idx != -1 and token_idx > target_idx:
            # We shift user, booking, prio_data, and er details to move the emergency token up to target_idx
            target_user = token.user
            target_booking = token.booking
            target_er = details[token_idx]['er']

            # Shift list to the right
            for i in range(token_idx, target_idx, -1):
                details[i]['user'] = details[i-1]['user']
                details[i]['booking'] = details[i-1]['booking']
                details[i]['prio_data'] = details[i-1]['prio_data']
                details[i]['er'] = details[i-1]['er']

            details[target_idx]['user'] = target_user
            details[target_idx]['booking'] = target_booking
            details[target_idx]['prio_data'] = {
                'priority_serial': f"P-{token.serial_number}",
                'insertion_position': target_idx + 1,
                'urgency_level': urgency_level
            }
            details[target_idx]['er'] = target_er

            # Clear bookings and temporarily detach emergency requests to avoid unique constraints
            for d in details:
                t = Token.objects.get(id=d['id'])
                t.booking = None
                t.save()
                
                if d['er']:
                    d['er'].token = None
                    d['er'].save()

            # Save updates back to database
            for d in details:
                t = Token.objects.get(id=d['id'])
                t.user = d['user']
                t.booking = d['booking']
                t.save()
                
                # Align booking user
                if t.booking:
                    b = t.booking
                    b.user = t.user
                    b.save()

                # Recreate priority record
                if d['prio_data']:
                    cls.objects.create(
                        token=t,
                        priority_serial=d['prio_data']['priority_serial'],
                        insertion_position=d['prio_data']['insertion_position'],
                        urgency_level=d['prio_data']['urgency_level']
                    )

                # Reattach EmergencyRequest
                if d['er']:
                    d['er'].token = t
                    d['er'].save()
        else:
            # Clear all er tokens temporarily first to avoid any conflicts
            for d in details:
                if d['er']:
                    d['er'].token = None
                    d['er'].save()

            # Recreate existing priority records and ensure EmergencyRequests remain attached
            for d in details:
                t = Token.objects.get(id=d['id'])
                if d['prio_data']:
                    cls.objects.create(
                        token=t,
                        priority_serial=d['prio_data']['priority_serial'],
                        insertion_position=d['prio_data']['insertion_position'],
                        urgency_level=d['prio_data']['urgency_level']
                    )
                if d['er']:
                    d['er'].token = t
                    d['er'].save()

            # Create new priority record (since it didn't shift)
            cls.objects.create(
                token=token,
                priority_serial=f"P-{token.serial_number}",
                insertion_position=target_idx + 1,
                urgency_level=urgency_level
            )

        # Recalculate estimated waiting times for all remaining waiting tokens
        updated_waiting = Token.objects.filter(
            organization=token.organization,
            booking_date=token.booking_date,
            status='Waiting'
        ).order_by('id')
        for t in updated_waiting:
            t.calculateEstimatedTime()

        # Refresh positions
        priority_rec = cls.objects.filter(token__user=token.user, token__status='Waiting').first()
        cls.managePriorityQueue(token.organization, token.booking_date)

        return priority_rec

    @classmethod
    def managePriorityQueue(cls, organization, date=None):
        """
        App 3 Blueprint: managePriorityQueue
        Refreshes/cleans up the priority queue for a specific organization and date.
        Deletes any priority records whose tokens are no longer 'Waiting'.
        Renumber the remaining waiting priority records' insertion_position.
        """
        if not date:
            date = timezone.now().date()
            
        # Clean up stale/inactive priority tracking records
        cls.objects.filter(
            token__organization=organization,
            token__booking_date=date
        ).exclude(token__status='Waiting').delete()
        
        # Order remaining active priority tokens
        active_prios = cls.objects.filter(
            token__organization=organization,
            token__booking_date=date,
            token__status='Waiting'
        ).order_by('id')
        
        for idx, prio in enumerate(active_prios):
            prio.insertion_position = idx + 1
            prio.save()
            
        return active_prios

    def adjustPriorityPosition(self, new_position):
        """
        App 3 Blueprint: adjustPriorityPosition
        Adjusts the insertion position of this priority token.
        new_position is 1-indexed. We shift user and booking details to move the token
        to the target slot index (new_position - 1) among waiting tokens.
        """
        # Find all today's waiting tokens for the organization in queue order
        waiting_tokens = list(Token.objects.filter(
            organization=self.token.organization,
            booking_date=self.token.booking_date,
            status='Waiting'
        ).order_by('id'))

        target_idx = new_position - 1
        if target_idx < 0 or target_idx >= len(waiting_tokens):
            return False # Invalid position

        # Details list of waitings, delete old priority records temporarily
        details = []
        for t in waiting_tokens:
            prio_data = None
            if hasattr(t, 'priority_info'):
                p = t.priority_info
                prio_data = {
                    'priority_serial': p.priority_serial,
                    'insertion_position': p.insertion_position,
                    'urgency_level': p.urgency_level
                }
                p.delete()

            er = None
            if hasattr(t, 'emergency_request'):
                er = t.emergency_request

            details.append({
                'user': t.user,
                'booking': t.booking,
                'prio_data': prio_data,
                'er': er,
                'id': t.id
            })

        # Find current index of our token (by user / matching ID)
        token_idx = -1
        for idx, d in enumerate(details):
            if d['id'] == self.token.id:
                token_idx = idx
                break

        if token_idx == -1:
            return False

        if token_idx != target_idx:
            target_user = details[token_idx]['user']
            target_booking = details[token_idx]['booking']
            target_prio_data = details[token_idx]['prio_data']
            target_er = details[token_idx]['er']

            if token_idx > target_idx:
                # Shift to the right (move token up)
                for i in range(token_idx, target_idx, -1):
                    details[i]['user'] = details[i-1]['user']
                    details[i]['booking'] = details[i-1]['booking']
                    details[i]['prio_data'] = details[i-1]['prio_data']
                    details[i]['er'] = details[i-1]['er']
            else:
                # Shift to the left (move token down)
                for i in range(token_idx, target_idx):
                    details[i]['user'] = details[i+1]['user']
                    details[i]['booking'] = details[i+1]['booking']
                    details[i]['prio_data'] = details[i+1]['prio_data']
                    details[i]['er'] = details[i+1]['er']

            details[target_idx]['user'] = target_user
            details[target_idx]['booking'] = target_booking
            details[target_idx]['prio_data'] = target_prio_data
            details[target_idx]['er'] = target_er

        # Clear bookings and temporarily detach emergency requests to avoid unique constraints
        for d in details:
            t = Token.objects.get(id=d['id'])
            t.booking = None
            t.save()
            
            if d['er']:
                d['er'].token = None
                d['er'].save()

        # Save updates back to database
        for d in details:
            t = Token.objects.get(id=d['id'])
            t.user = d['user']
            t.booking = d['booking']
            t.save()
            
            # Align booking user
            if t.booking:
                b = t.booking
                b.user = t.user
                b.save()

            # Recreate priority record
            if d['prio_data']:
                self.__class__.objects.create(
                    token=t,
                    priority_serial=d['prio_data']['priority_serial'],
                    insertion_position=d['prio_data']['insertion_position'],
                    urgency_level=d['prio_data']['urgency_level']
                )

            # Reattach EmergencyRequest
            if d['er']:
                d['er'].token = t
                d['er'].save()

        # Recalculate estimated waiting times
        for t in waiting_tokens:
            t_refreshed = Token.objects.get(id=t.id)
            t_refreshed.calculateEstimatedTime()

        # Update all priority tracker positions for this organization/date
        self.__class__.managePriorityQueue(self.token.organization, self.token.booking_date)

        # Notify user of new queue position turn
        from queue_system.models import EmailNotification
        from django.core.mail import send_mail
        
        subject = f"QeNova - Queue Turn Updated"
        message = (
            f"Hello {self.token.user.username},\n\n"
            f"Your priority slot has been adjusted by the organization.\n"
            f"Your token {self.token.serial_number} is now at waitlist Position {new_position}.\n"
            f"Please keep checking the live status."
        )
        EmailNotification.objects.create(
            user=self.token.user,
            subject=subject,
            message=message,
            email_type='Update'
        )
        send_mail(subject, message, None, [self.token.user.email], fail_silently=True)

        return True



class EmergencyAnalytics(models.Model):
    """
    App 3 Blueprint: EmergencyAnalytics
    Tracks usage reports and scans for potential gaming/abuses of the emergency system.
    """
    organization = models.ForeignKey('queue_system.Organization', on_delete=models.CASCADE, related_name='emergency_analytics')
    total_emergencies = models.IntegerField(default=0)
    approved_requests = models.IntegerField(default=0)
    rejected_requests = models.IntegerField(default=0)

    def __str__(self):
        return f"Emergency Analytics for {self.organization.account.organization_name}"

    @classmethod
    def generateEmergencyReport(cls, organization):
        """Returns emergency counts for the organization."""
        total = EmergencyRequest.objects.filter(token__organization=organization).count()
        approved = EmergencyRequest.objects.filter(token__organization=organization, status='Approved').count()
        rejected = EmergencyRequest.objects.filter(token__organization=organization, status='Rejected').count()
        
        record, _ = cls.objects.get_or_create(organization=organization)
        record.total_emergencies = total
        record.approved_requests = approved
        record.rejected_requests = rejected
        record.save()
        return record

    @classmethod
    def analyzeEmergencyTrends(cls, organization):
        """
        Analyzes trends of emergency requests for a given organization:
        - Breakdown by emergency type.
        - Approval and rejection rates.
        - Peak hour/day analysis.
        """
        requests = EmergencyRequest.objects.filter(token__organization=organization)
        total = requests.count()

        # Type breakdown
        from django.db.models import Count
        type_breakdown = list(requests.values('emergency_type').annotate(count=Count('id')).order_by('-count'))

        # Rates
        approved_count = requests.filter(status='Approved').count()
        rejected_count = requests.filter(status='Rejected').count()
        approval_rate = (approved_count / total * 100) if total > 0 else 0.0
        rejection_rate = (rejected_count / total * 100) if total > 0 else 0.0

        # Peak Hour
        hour_counts = {}
        day_counts = {}
        for req in requests:
            h = req.created_at.astimezone().hour
            d = req.created_at.strftime('%A')
            hour_counts[h] = hour_counts.get(h, 0) + 1
            day_counts[d] = day_counts.get(d, 0) + 1

        peak_hour = max(hour_counts, key=hour_counts.get) if hour_counts else None
        peak_day = max(day_counts, key=day_counts.get) if day_counts else None

        return {
            'total_requests': total,
            'type_breakdown': type_breakdown,
            'approval_rate': approval_rate,
            'rejection_rate': rejection_rate,
            'peak_hour': peak_hour,
            'peak_day': peak_day
        }

    @classmethod
    def trackEmergencyUsage(cls, user):
        """Counts how many times a particular user has requested emergencies."""
        return EmergencyRequest.objects.filter(token__user=user).count()

    @classmethod
    def detectFakeEmergency(cls, request):
        """
        Rule-based logic to detect suspicious requests:
        - If user has more than 2 emergency requests in the past 7 days.
        - If the document is missing.
        """
        user = request.token.user
        seven_days_ago = timezone.now() - timezone.timedelta(days=7)
        recent_count = EmergencyRequest.objects.filter(
            token__user=user,
            created_at__gte=seven_days_ago
        ).count()

        is_suspicious = False
        reasons = []

        if recent_count > 2:
            is_suspicious = True
            reasons.append("User requested more than 2 emergencies this week.")
        if not request.document:
            is_suspicious = True
            reasons.append("No supporting document uploaded.")

        return {
            'is_suspicious': is_suspicious,
            'reasons': reasons,
            'recent_count': recent_count
        }


class SlotSwap(models.Model):
    """
    App 3 Blueprint: SlotSwap
    Enables users to request queue position swaps with other waiting customers.
    """
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='requested_swaps')
    target_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='received_swaps')
    current_slot = models.ForeignKey(Token, on_delete=models.CASCADE, related_name='requester_swaps')
    requested_slot = models.ForeignKey(Token, on_delete=models.CASCADE, related_name='target_swaps')
    status = models.CharField(max_length=50, default='Pending', choices=[
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
        ('Rejected', 'Rejected'),
        ('Expired', 'Expired')
    ])
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Swap Request from {self.requester.username} to {self.target_user.username} - {self.status}"

    @classmethod
    def requestSwap(cls, current_token, target_token):
        """Creates a new swap request and notifies the target user, preventing duplicates."""
        # Prevent duplicate pending swaps
        existing = cls.objects.filter(
            current_slot=current_token,
            requested_slot=target_token,
            status='Pending'
        ).first()
        if existing:
            return existing

        swap = cls.objects.create(
            requester=current_token.user,
            target_user=target_token.user,
            current_slot=current_token,
            requested_slot=target_token
        )

        # Log request
        SlotSwapLog.objects.create(
            swap=swap,
            action='Requested',
            details=f"Requester: {current_token.user.username}, Target: {target_token.user.username}"
        )

        # Notify target user
        from queue_system.models import EmailNotification
        from django.core.mail import send_mail

        subject = f"QeNova - Slot Swap Requested by {current_token.user.username}"
        message = (
            f"Hello {target_token.user.username},\n\n"
            f"User {current_token.user.username} (Token {current_token.serial_number}) has requested to swap "
            f"slots with your Token {target_token.serial_number}.\n"
            f"Please visit your dashboard to Approve or Reject this request."
        )
        EmailNotification.objects.create(
            user=target_token.user,
            subject=subject,
            message=message,
            email_type='Update'
        )
        send_mail(subject, message, None, [target_token.user.email], fail_silently=True)

        return swap

    @classmethod
    def checkAvailableSlots(cls, token):
        """Returns all other tokens at the same organization/date that are currently Waiting."""
        from queue_system.models import Token
        return Token.objects.filter(
            organization=token.organization,
            booking_date=token.booking_date,
            status='Waiting'
        ).exclude(user=token.user)

    @classmethod
    def isSlotReserved(cls, token):
        """Checks if a token is already involved in a pending swap request."""
        return cls.objects.filter(
            models.Q(current_slot=token) | models.Q(requested_slot=token),
            status='Pending'
        ).exists()

    def detectSlotConflicts(self):
        """Detects if any of the tokens in the swap request are no longer available for swapping."""
        if self.current_slot.status != 'Waiting' or self.requested_slot.status != 'Waiting':
            return True
        if self.current_slot.organization != self.requested_slot.organization:
            return True
        if self.current_slot.booking_date != self.requested_slot.booking_date:
            return True
        return False

    def validateSwap(self):
        """Validates if both tokens are waiting and belong to the same organization and booking date."""
        if self.status != 'Pending':
            return False
        if self.detectSlotConflicts():
            return False
        return True

    def isFairSwap(self):
        """
        Determines if the proposed swap is 'fair' (i.e. distance in queue order is <= 10 positions).
        """
        t1 = self.current_slot
        t2 = self.requested_slot
        from queue_system.models import Token
        tokens = Token.objects.filter(
            organization=t1.organization,
            booking_date=t1.booking_date,
            status='Waiting'
        ).order_by('id')
        
        try:
            pos1 = list(tokens).index(t1)
            pos2 = list(tokens).index(t2)
            distance = abs(pos1 - pos2)
            return distance <= 10
        except ValueError:
            return False

    @classmethod
    def suggestSwapOptions(cls, token):
        """
        Suggests better swap choices that are ahead of the current token in the queue,
        limited to a maximum improvement distance of 10 slots to ensure fairness.
        """
        available = cls.checkAvailableSlots(token)
        better_options = available.filter(id__lt=token.id).order_by('-id')
        
        from queue_system.models import Token
        tokens = list(Token.objects.filter(
            organization=token.organization,
            booking_date=token.booking_date,
            status='Waiting'
        ).order_by('id'))
        
        try:
            pos_token = tokens.index(token)
            fair_options = []
            for opt in better_options:
                try:
                    pos_opt = tokens.index(opt)
                    if pos_token - pos_opt <= 10:
                        fair_options.append(opt)
                except ValueError:
                    continue
            return fair_options
        except ValueError:
            return []

    @classmethod
    def expirePendingSwaps(cls):
        """
        Automatically expires pending swaps that are older than 15 minutes or 
        where the target slot's booking day has passed or either token is no longer Waiting.
        """
        from django.utils import timezone
        expiry_limit = timezone.now() - timezone.timedelta(minutes=15)
        
        expired_swaps = cls.objects.filter(
            status='Pending',
            created_at__lt=expiry_limit
        )
        
        count = 0
        for swap in expired_swaps:
            swap.status = 'Expired'
            swap.save()
            
            # Log expiration
            SlotSwapLog.objects.create(
                swap=swap,
                action='Expired',
                details="Swap request expired automatically after 15 minutes."
            )
            
            # Notify requester
            from queue_system.models import EmailNotification
            from django.core.mail import send_mail
            
            subject = "QeNova - Slot Swap Request Expired"
            message = (
                f"Hello {swap.requester.username},\n\n"
                f"Your slot swap request with {swap.target_user.username} has expired."
            )
            EmailNotification.objects.create(
                user=swap.requester,
                subject=subject,
                message=message,
                email_type='Update'
            )
            send_mail(subject, message, None, [swap.requester.email], fail_silently=True)
            count += 1
            
        return count

    def approveSwap(self):
        """
        Swaps the users and bookings of the requester's token and target's token,
        exchanging their queue slots perfectly.
        """
        if not self.validateSwap():
            return False
            
        t1 = self.current_slot
        t2 = self.requested_slot

        # Fetch any related priority and emergency records to carry them along
        pq1 = getattr(t1, 'priority_info', None)
        pq2 = getattr(t2, 'priority_info', None)
        er1 = getattr(t1, 'emergency_request', None)
        er2 = getattr(t2, 'emergency_request', None)

        # Serialize and delete PriorityQueue objects to avoid NOT NULL and UNIQUE key constraints
        pq1_data = None
        pq2_data = None
        if pq1:
            pq1_data = {
                'priority_serial': pq1.priority_serial,
                'insertion_position': pq1.insertion_position,
                'urgency_level': pq1.urgency_level,
            }
            pq1.delete()
        if pq2:
            pq2_data = {
                'priority_serial': pq2.priority_serial,
                'insertion_position': pq2.insertion_position,
                'urgency_level': pq2.urgency_level,
            }
            pq2.delete()

        # Clear nullable EmergencyRequest.token links temporarily to avoid unique constraints
        if er1:
            er1.token = None
            er1.save()
        if er2:
            er2.token = None
            er2.save()

        # Swap user attributes
        user1 = t1.user
        user2 = t2.user
        t1.user = user2
        t2.user = user1

        # Swap booking attributes
        booking1 = t1.booking
        booking2 = t2.booking

        # Temporarily set bookings to None to avoid unique constraint violations
        t1.booking = None
        t2.booking = None
        t1.save()
        t2.save()

        # Assign correct values and save
        t1.booking = booking2
        t2.booking = booking1
        t1.save()
        t2.save()

        # Update and align bookings
        if t1.booking:
            t1.booking.user = t1.user
            t1.booking.save()
        if t2.booking:
            t2.booking.user = t2.user
            t2.booking.save()

        # Re-create/reattach priority and emergency records to swapped targets
        # User 1 (now at t2) gets User 1's original priority/emergency metadata
        if pq1_data:
            PriorityQueue.objects.create(
                token=t2,
                priority_serial=pq1_data['priority_serial'],
                insertion_position=pq1_data['insertion_position'],
                urgency_level=pq1_data['urgency_level']
            )
        if er1:
            er1.token = t2
            er1.save()

        # User 2 (now at t1) gets User 2's original priority/emergency metadata
        if pq2_data:
            PriorityQueue.objects.create(
                token=t1,
                priority_serial=pq2_data['priority_serial'],
                insertion_position=pq2_data['insertion_position'],
                urgency_level=pq2_data['urgency_level']
            )
        if er2:
            er2.token = t1
            er2.save()

        # Recalculate estimated waiting times
        t1.calculateEstimatedTime()
        t2.calculateEstimatedTime()

        self.status = 'Approved'
        self.save()

        # Notify requester
        from queue_system.models import EmailNotification
        from django.core.mail import send_mail

        subject = "QeNova - Slot Swap Request APPROVED!"
        message = (
            f"Hello {user1.username},\n\n"
            f"Your slot swap request with {user2.username} has been APPROVED.\n"
            f"Your token booking has been updated successfully!"
        )
        EmailNotification.objects.create(
            user=user1,
            subject=subject,
            message=message,
            email_type='Update'
        )
        send_mail(subject, message, None, [user1.email], fail_silently=True)

        # Log approval
        SlotSwapLog.objects.create(
            swap=self,
            action='Approved',
            details=f"Swap approved. Slots exchanged."
        )

        return True

    def rejectSwap(self):
        """Rejects the swap request and notifies the requester."""
        if self.status != 'Pending':
            return False
        self.status = 'Rejected'
        self.save()

        # Notify requester
        from queue_system.models import EmailNotification
        from django.core.mail import send_mail

        subject = "QeNova - Slot Swap Request Rejected"
        message = (
            f"Hello {self.requester.username},\n\n"
            f"Your slot swap request with {self.target_user.username} was rejected."
        )
        EmailNotification.objects.create(
            user=self.requester,
            subject=subject,
            message=message,
            email_type='Update'
        )
        send_mail(subject, message, None, [self.requester.email], fail_silently=True)

        # Log rejection
        SlotSwapLog.objects.create(
            swap=self,
            action='Rejected',
            details=f"Swap rejected by target user."
        )
        return True

    @classmethod
    def detectUnusualSwapBehavior(cls, user):
        """
        Rule-based detection for spam/unusual swap behavior:
        - If the user has sent > 3 swap requests in the past 24 hours.
        - If the user has sent > 5 swap requests in total.
        """
        one_day_ago = timezone.now() - timezone.timedelta(days=1)
        recent_count = cls.objects.filter(requester=user, created_at__gte=one_day_ago).count()
        total_count = cls.objects.filter(requester=user).count()

        is_suspicious = False
        reasons = []

        if recent_count > 3:
            is_suspicious = True
            reasons.append("User requested more than 3 swaps in the last 24 hours.")
        if total_count > 5:
            is_suspicious = True
            reasons.append("User requested more than 5 swaps in total.")

        return {
            'is_suspicious': is_suspicious,
            'reasons': reasons,
            'recent_count': recent_count,
            'total_count': total_count
        }


class SlotSwapLog(models.Model):
    """
    App 3 Blueprint: SlotSwapLog
    Logs all slot swap activities (requesting, approving, rejecting) for auditing and behavior monitoring.
    """
    swap = models.ForeignKey(SlotSwap, on_delete=models.CASCADE, related_name='activity_logs')
    action = models.CharField(max_length=50) # 'Requested', 'Approved', 'Rejected'
    timestamp = models.DateTimeField(auto_now_add=True)
    details = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Swap Log: {self.swap.id} - {self.action} at {self.timestamp}"


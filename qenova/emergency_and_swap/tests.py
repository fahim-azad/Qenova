from django.test import TestCase
from django.contrib.auth import get_user_model
from queue_system.models import Organization, QueueBooking, Token
from emergency_and_swap.models import EmergencyRequest, PriorityQueue, SlotSwap
import datetime

User = get_user_model()

class EmergencyAndSwapTestCase(TestCase):
    def setUp(self):
        # Create user accounts
        self.customer1 = User.objects.create_user(username='customer1', password='password1', is_customer=True)
        self.customer2 = User.objects.create_user(username='customer2', password='password1', is_customer=True)
        self.customer3 = User.objects.create_user(username='customer3', password='password1', is_customer=True)
        
        self.org_user = User.objects.create_user(username='hospital', password='password1', is_organization=True)
        # Create OrganizationUser profile
        from accounts.models import OrganizationUser
        self.org_profile = OrganizationUser.objects.create(user=self.org_user, organization_name="Hospital")
        
        # Create Organization
        self.org = Organization.objects.create(account=self.org_profile)
        
        # Create daily bookings and tokens
        today = datetime.date.today()
        
        # Token 1
        self.booking1 = QueueBooking.objects.create(user=self.customer1, organization=self.org, booking_date=today)
        self.token1 = Token.objects.create(user=self.customer1, organization=self.org, booking=self.booking1, booking_date=today, status='Waiting', serial_number='T-001')
        
        # Token 2
        self.booking2 = QueueBooking.objects.create(user=self.customer2, organization=self.org, booking_date=today)
        self.token2 = Token.objects.create(user=self.customer2, organization=self.org, booking=self.booking2, booking_date=today, status='Waiting', serial_number='T-002')

        # Token 3
        self.booking3 = QueueBooking.objects.create(user=self.customer3, organization=self.org, booking_date=today)
        self.token3 = Token.objects.create(user=self.customer3, organization=self.org, booking=self.booking3, booking_date=today, status='Waiting', serial_number='T-003')

    def test_submit_and_approve_emergency(self):
        """Tests that submitting and approving an emergency request bumps the token to the front."""
        # 1. Submit emergency for Token 3 (which is currently the last token in line)
        req = EmergencyRequest.submitEmergency(token=self.token3, emergency_type='Medical')
        self.assertEqual(req.status, 'Pending')
        self.assertEqual(req.emergency_type, 'Medical')
        
        # 2. Approve emergency
        req.approveEmergency(reviewer=self.org_user, notes="Urgent medical assistance requested")
        self.assertEqual(req.status, 'Approved')
        
        # Verify Approval Log
        from emergency_and_swap.models import EmergencyApprovalLog
        log = EmergencyApprovalLog.objects.filter(request=req).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.action, 'Approved')
        self.assertEqual(log.reviewed_by, self.org_user)
        self.assertEqual(log.notes, "Urgent medical assistance requested")
        
        # 3. Verify PriorityQueue entry
        pq = PriorityQueue.objects.filter(token__user=self.customer3).first()
        self.assertIsNotNone(pq)
        self.assertEqual(pq.priority_serial, 'P-T-003')
        
        # 4. Verify order of waiting tokens today.
        # Token 3 should be moved to the first waiting slot (ID of Token 1)
        tokens_ordered = list(Token.objects.filter(organization=self.org, status='Waiting').order_by('id'))
        
        # The first token in the list should now be user3 (since they were bumped!)
        self.assertEqual(tokens_ordered[0].user, self.customer3)
        self.assertEqual(tokens_ordered[1].user, self.customer1)
        self.assertEqual(tokens_ordered[2].user, self.customer2)

    def test_slot_swap(self):
        """Tests that slot swapping swaps users and bookings correctly between two tokens."""
        from queue_system.models import EmailNotification
        EmailNotification.objects.all().delete()

        # Create priority info for Token 1
        PriorityQueue.objects.create(
            token=self.token1,
            priority_serial='P-001',
            insertion_position=1,
            urgency_level='High'
        )

        # 1. Create a SlotSwap request between Token 1 and Token 2 -> should notify customer2 (target_user)
        swap = SlotSwap.requestSwap(self.token1, self.token2)
        self.assertEqual(swap.status, 'Pending')
        self.assertTrue(swap.validateSwap())
        self.assertEqual(EmailNotification.objects.filter(user=self.customer2, subject__icontains="requested").count(), 1)

        # Try to request swap again -> should return the existing swap (prevent duplicate)
        swap_dup = SlotSwap.requestSwap(self.token1, self.token2)
        self.assertEqual(swap_dup.id, swap.id)
        
        # 2. Approve the swap -> should notify customer1 (requester)
        approved = swap.approveSwap()
        self.assertTrue(approved)
        self.assertEqual(swap.status, 'Approved')
        self.assertEqual(EmailNotification.objects.filter(user=self.customer1, subject__icontains="approved").count(), 1)

        # Try to approve again -> should fail validation and return False
        self.assertFalse(swap.validateSwap())
        self.assertFalse(swap.approveSwap())
        
        # 3. Fetch tokens again from DB and assert users have been swapped
        t1_refreshed = Token.objects.get(id=self.token1.id)
        t2_refreshed = Token.objects.get(id=self.token2.id)
        
        self.assertEqual(t1_refreshed.user, self.customer2)
        self.assertEqual(t2_refreshed.user, self.customer1)
        
        # Assert bookings have also been swapped and aligned
        self.assertEqual(t1_refreshed.booking.user, self.customer2)
        self.assertEqual(t2_refreshed.booking.user, self.customer1)

        # Assert priority info has been correctly moved to t2_refreshed (since customer1 was swapped to token2)
        self.assertFalse(hasattr(t1_refreshed, 'priority_info'))
        self.assertTrue(hasattr(t2_refreshed, 'priority_info'))
        self.assertEqual(t2_refreshed.priority_info.priority_serial, 'P-001')

    def test_document_upload_and_validation(self):
        """Tests file validation and document uploading on EmergencyRequest."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from emergency_and_swap.forms import EmergencyRequestForm

        # 1. Valid PDF file
        valid_pdf = SimpleUploadedFile("proof.pdf", b"file_content", content_type="application/pdf")
        form = EmergencyRequestForm(data={'emergency_type': 'Medical'}, files={'document': valid_pdf})
        self.assertTrue(form.is_valid())

        # 2. Invalid Extension File
        invalid_exe = SimpleUploadedFile("malware.exe", b"malicious_code", content_type="application/octet-stream")
        form = EmergencyRequestForm(data={'emergency_type': 'Medical'}, files={'document': invalid_exe})
        self.assertFalse(form.is_valid())
        self.assertIn("Unsupported file format", form.errors['document'][0])

        # 3. Size Validation (exceeds 5MB)
        huge_content = b"x" * (6 * 1024 * 1024) # 6MB
        huge_file = SimpleUploadedFile("proof.jpg", huge_content, content_type="image/jpeg")
        form = EmergencyRequestForm(data={'emergency_type': 'Medical'}, files={'document': huge_file})
        self.assertFalse(form.is_valid())
        self.assertIn("File size exceeds the 5MB limit", form.errors['document'][0])

        # 4. uploadDocument method
        req = EmergencyRequest.submitEmergency(token=self.token1, emergency_type='Medical')
        valid_jpg = SimpleUploadedFile("proof.jpg", b"image_data", content_type="image/jpeg")
        req.uploadDocument(valid_jpg)
        self.assertIn("proof", req.document.name)

    def test_reject_emergency(self):
        """Tests that rejecting an emergency request logs the action and changes status to Rejected."""
        req = EmergencyRequest.submitEmergency(token=self.token3, emergency_type='Urgent Business')
        req.rejectEmergency(reviewer=self.org_user, notes="Insufficient proof documents")
        self.assertEqual(req.status, 'Rejected')

        from emergency_and_swap.models import EmergencyApprovalLog
        log = EmergencyApprovalLog.objects.filter(request=req).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.action, 'Rejected')
        self.assertEqual(log.reviewed_by, self.org_user)
        self.assertEqual(log.notes, "Insufficient proof documents")

    def test_priority_queue_flow(self):
        """Tests managing priority queue flow and manually adjusting positions."""
        # 1. Bump customer3 (which is at self.token3) to front
        req3 = EmergencyRequest.submitEmergency(token=self.token3, emergency_type='Medical')
        req3.approveEmergency(reviewer=self.org_user)
        
        # 2. Bump customer2. First, find which token currently belongs to customer2
        token2_current = Token.objects.get(user=self.customer2)
        req2 = EmergencyRequest.submitEmergency(token=token2_current, emergency_type='Disability')
        req2.approveEmergency(reviewer=self.org_user)
        
        # Verify active priority queues managed dynamically
        prios = PriorityQueue.managePriorityQueue(self.org)
        self.assertEqual(prios.count(), 2)
        
        # 3. Manually adjust position of customer2's priority token to absolute front (new_position=1)
        prio2 = PriorityQueue.objects.get(token__user=self.customer2)
        success = prio2.adjustPriorityPosition(1)
        self.assertTrue(success)
        
        # Verify order: Token with customer2 is now first waiting today
        tokens_ordered = list(Token.objects.filter(organization=self.org, status='Waiting').order_by('id'))
        self.assertEqual(tokens_ordered[0].user, self.customer2)

    def test_detect_fake_emergency(self):
        """Tests that detectFakeEmergency correctly identifies suspicious activities."""
        from emergency_and_swap.models import EmergencyAnalytics
        from django.core.files.uploadedfile import SimpleUploadedFile

        # 1. Normal submission with document should be low risk
        req1 = EmergencyRequest.submitEmergency(token=self.token1, emergency_type='Medical')
        valid_doc = SimpleUploadedFile("proof.pdf", b"medical evidence", content_type="application/pdf")
        req1.uploadDocument(valid_doc)

        chk = EmergencyAnalytics.detectFakeEmergency(req1)
        self.assertFalse(chk['is_suspicious'])
        self.assertEqual(chk['recent_count'], 1)

        # 2. Submission without document should be suspicious
        req2 = EmergencyRequest.submitEmergency(token=self.token2, emergency_type='Medical')
        chk2 = EmergencyAnalytics.detectFakeEmergency(req2)
        self.assertTrue(chk2['is_suspicious'])
        self.assertIn("No supporting document uploaded.", chk2['reasons'])

        # 3. Requesting > 2 emergencies in 7 days should trigger suspicion
        # Create user tokens to request more
        today = datetime.date.today()
        # Create third token for customer1
        booking = QueueBooking.objects.create(user=self.customer1, organization=self.org, booking_date=today)
        token = Token.objects.create(user=self.customer1, organization=self.org, booking=booking, booking_date=today, status='Waiting', serial_number='T-004')
        req3 = EmergencyRequest.submitEmergency(token=token, emergency_type='Medical')
        req3.uploadDocument(valid_doc)

        # Create fourth token for customer1
        booking2 = QueueBooking.objects.create(user=self.customer1, organization=self.org, booking_date=today)
        token2 = Token.objects.create(user=self.customer1, organization=self.org, booking=booking2, booking_date=today, status='Waiting', serial_number='T-005')
        req4 = EmergencyRequest.submitEmergency(token=token2, emergency_type='Medical')
        req4.uploadDocument(valid_doc)

        # check that req4 is suspicious due to high frequency
        chk4 = EmergencyAnalytics.detectFakeEmergency(req4)
        self.assertTrue(chk4['is_suspicious'])
        self.assertIn("User requested more than 2 emergencies this week.", chk4['reasons'])

    def test_emergency_analytics_trends(self):
        """Tests that generateEmergencyReport and analyzeEmergencyTrends compute correct statistics."""
        from emergency_and_swap.models import EmergencyAnalytics

        # 1. Create multiple requests under different categories
        req1 = EmergencyRequest.submitEmergency(token=self.token1, emergency_type='Medical')
        req2 = EmergencyRequest.submitEmergency(token=self.token2, emergency_type='Urgent Business')
        req3 = EmergencyRequest.submitEmergency(token=self.token3, emergency_type='Medical')

        req1.approveEmergency(reviewer=self.org_user)
        req2.rejectEmergency(reviewer=self.org_user)

        # 2. Generate report
        report = EmergencyAnalytics.generateEmergencyReport(self.org)
        self.assertEqual(report.total_emergencies, 3)
        self.assertEqual(report.approved_requests, 1)
        self.assertEqual(report.rejected_requests, 1)

        # 3. Analyze trends
        trends = EmergencyAnalytics.analyzeEmergencyTrends(self.org)
        self.assertEqual(trends['total_requests'], 3)
        self.assertAlmostEqual(trends['approval_rate'], 33.3333333, places=2)
        self.assertAlmostEqual(trends['rejection_rate'], 33.3333333, places=2)
        self.assertIsNotNone(trends['peak_hour'])
        self.assertIsNotNone(trends['peak_day'])

        # Medical should be the peak type (count: 2)
        medical_trend = next(item for item in trends['type_breakdown'] if item['emergency_type'] == 'Medical')
        self.assertEqual(medical_trend['count'], 2)

    def test_emergency_notifications(self):
        """Tests that submitting, approving, rejecting, and adjusting positions triggers EmailNotifications."""
        from queue_system.models import EmailNotification

        # 1. Clear any prior notifications to have clean counts
        EmailNotification.objects.all().delete()

        # 2. Submit emergency -> should notify organization staff
        req = EmergencyRequest.submitEmergency(token=self.token1, emergency_type='Medical')
        self.assertEqual(EmailNotification.objects.filter(user=self.org_user).count(), 1)
        
        # 3. Approve emergency -> should notify user
        req.approveEmergency(reviewer=self.org_user)
        self.assertEqual(EmailNotification.objects.filter(user=self.customer1, subject__icontains="approved").count(), 1)

        # 4. Reject emergency (submit new first)
        req2 = EmergencyRequest.submitEmergency(token=self.token2, emergency_type='Urgent Business')
        req2.rejectEmergency(reviewer=self.org_user, notes="Unconvincing document")
        self.assertEqual(EmailNotification.objects.filter(user=self.customer2, subject__icontains="rejected").count(), 1)

        # 5. Adjust priority position -> should notify priority customer
        prio = PriorityQueue.objects.get(token=self.token1)
        prio.adjustPriorityPosition(2)
        self.assertEqual(EmailNotification.objects.filter(user=self.customer1, subject__icontains="updated").count(), 1)

    def test_slot_availability_and_conflicts(self):
        """Tests checkAvailableSlots, isSlotReserved, and detectSlotConflicts methods."""
        # 1. checkAvailableSlots: Token 1 should be able to swap with Token 2 and Token 3
        available = SlotSwap.checkAvailableSlots(self.token1)
        self.assertIn(self.token2, available)
        self.assertIn(self.token3, available)
        self.assertNotIn(self.token1, available)

        # 2. isSlotReserved: initially False
        self.assertFalse(SlotSwap.isSlotReserved(self.token1))
        
        # Request a swap -> now isSlotReserved should be True
        swap = SlotSwap.requestSwap(self.token1, self.token2)
        self.assertTrue(SlotSwap.isSlotReserved(self.token1))
        self.assertTrue(SlotSwap.isSlotReserved(self.token2))
        self.assertFalse(SlotSwap.isSlotReserved(self.token3))

        # 3. detectSlotConflicts: initially False (both are Waiting)
        self.assertFalse(swap.detectSlotConflicts())

        # Change Token 2 status to 'Serving' -> conflict should be detected
        self.token2.status = 'Serving'
        self.token2.save()
        self.assertTrue(swap.detectSlotConflicts())
        self.assertFalse(swap.validateSwap())

    def test_slot_swap_tracking(self):
        """Tests that SlotSwap actions are correctly logged and unusual swap behavior is detected."""
        from emergency_and_swap.models import SlotSwapLog

        # 1. Initially no logs
        SlotSwapLog.objects.all().delete()
        SlotSwap.objects.all().delete()

        # 2. Request a swap -> should log "Requested"
        swap = SlotSwap.requestSwap(self.token1, self.token2)
        self.assertEqual(SlotSwapLog.objects.filter(swap=swap, action='Requested').count(), 1)

        # 3. Approve swap -> should log "Approved"
        swap.approveSwap()
        self.assertEqual(SlotSwapLog.objects.filter(swap=swap, action='Approved').count(), 1)

        # 4. Check unusual behavior: customer1 has requested 1 swap so far
        behavior = SlotSwap.detectUnusualSwapBehavior(self.customer1)
        self.assertFalse(behavior['is_suspicious'])
        self.assertEqual(behavior['total_count'], 1)

        # Let's create multiple dummy swap requests to trigger suspicious behavior
        for i in range(5):
            SlotSwap.objects.create(
                requester=self.customer1,
                target_user=self.customer2,
                current_slot=self.token1,
                requested_slot=self.token2,
                status='Pending'
            )
        
        behavior = SlotSwap.detectUnusualSwapBehavior(self.customer1)
        self.assertTrue(behavior['is_suspicious'])
        self.assertIn("User requested more than 3 swaps in the last 24 hours.", behavior['reasons'])

    def test_smart_swap_features(self):
        """Tests suggestSwapOptions, isFairSwap, and expirePendingSwaps methods."""
        from django.utils import timezone
        from emergency_and_swap.models import SlotSwapLog
        SlotSwap.objects.all().delete()
        SlotSwapLog.objects.all().delete()

        # 1. suggestSwapOptions: Token 3 is behind Token 1 and 2.
        # token1 ID < token2 ID < token3 ID.
        # Suggest options for token3: should suggest token2 and token1 as better swap choices.
        suggestions = SlotSwap.suggestSwapOptions(self.token3)
        self.assertIn(self.token1, suggestions)
        self.assertIn(self.token2, suggestions)

        # 2. isFairSwap: token1 and token2 are adjacent, so distance <= 10 -> True
        swap = SlotSwap.objects.create(
            requester=self.customer2,
            target_user=self.customer1,
            current_slot=self.token2,
            requested_slot=self.token1,
            status='Pending'
        )
        self.assertTrue(swap.isFairSwap())

        # 3. expirePendingSwaps: Backdate the swap request to make it expire
        swap.created_at = timezone.now() - timezone.timedelta(minutes=20)
        swap.save()

        count = SlotSwap.expirePendingSwaps()
        self.assertEqual(count, 1)
        
        swap.refresh_from_db()
        self.assertEqual(swap.status, 'Expired')
        self.assertEqual(SlotSwapLog.objects.filter(swap=swap, action='Expired').count(), 1)





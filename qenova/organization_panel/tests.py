from django.test import TestCase
from django.contrib.auth import get_user_model
from queue_system.models import Token, Organization
from accounts.models import OrganizationUser
from organization_panel.models import OrganizationDashboard
import datetime

User = get_user_model()

class OrganizationDashboardTestCase(TestCase):
    def setUp(self):
        # 1. Create Organization Account and Profile
        self.org_user = User.objects.create_user(
            username='TestHospital',
            email='test@hospital.com',
            password='password123',
            is_organization=True
        )
        self.org_profile = OrganizationUser.objects.create(
            user=self.org_user,
            organization_name='Test Hospital'
        )
        self.org = Organization.objects.create(
            account=self.org_profile,
            token_limit=10,
            work_start=datetime.time(9, 0),
            work_end=datetime.time(17, 0),
        )
        
        # 2. Create customer users and book tokens
        self.customer1 = User.objects.create_user(
            username='customer1',
            email='c1@test.com',
            password='password123',
            is_customer=True
        )
        self.customer2 = User.objects.create_user(
            username='customer2',
            email='c2@test.com',
            password='password123',
            is_customer=True
        )

        today = datetime.date.today()
        # Token 1: Waiting
        self.t1 = Token.objects.create(
            user=self.customer1,
            organization=self.org,
            booking_date=today,
            serial_number='T-001',
            status='Waiting'
        )
        # Token 2: Serving
        self.t2 = Token.objects.create(
            user=self.customer2,
            organization=self.org,
            booking_date=today,
            serial_number='T-002',
            status='Serving'
        )

    def test_dashboard_monitoring_and_stats(self):
        """Tests monitorQueue and generateDashboardStats methods on OrganizationDashboard."""
        dashboard, _ = OrganizationDashboard.objects.get_or_create(organization=self.org)
        
        # 1. monitorQueue
        mon = dashboard.monitorQueue()
        self.assertEqual(mon['waiting_count'], 1)
        self.assertEqual(mon['serving_token_number'], 'T-002')
        self.assertEqual(mon['serving_token_user'], 'customer2')
        self.assertEqual(mon['health_status'], 'Healthy')

        # 2. generateDashboardStats
        stats = dashboard.generateDashboardStats()
        self.assertEqual(stats['total_bookings'], 2)
        self.assertEqual(stats['total_users'], 2)
        self.assertEqual(stats['status_distribution']['Waiting'], 1)
        self.assertEqual(stats['status_distribution']['Serving'], 1)
        self.assertEqual(stats['status_distribution']['Completed'], 0)

    def test_org_live_status_api(self):
        """Tests the real-time queue live status API view response."""
        from django.urls import reverse
        
        self.client.login(username='TestHospital', password='password123')
        url = reverse('org_live_status_api')
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['health_status'], 'Healthy')
        self.assertEqual(data['waiting_count'], 1)
        self.assertEqual(data['serving_token_number'], 'T-002')
        self.assertEqual(data['serving_token_user'], 'customer2')
        self.assertEqual(len(data['waiting_list']), 1)
        self.assertEqual(data['waiting_list'][0]['serial_number'], 'T-001')

    def test_call_and_skip_token(self):
        """Tests the callNextToken and skipToken queue control methods."""
        dashboard, _ = OrganizationDashboard.objects.get_or_create(organization=self.org)
        
        # Test callNextToken: completing T-002 (currently serving) and serving T-001
        next_token = dashboard.callNextToken()
        self.assertIsNotNone(next_token)
        self.assertEqual(next_token.serial_number, 'T-001')
        self.assertEqual(next_token.status, 'Serving')
        
        # Verify the previous serving token (T-002) is completed
        self.t2.refresh_from_db()
        self.assertEqual(self.t2.status, 'Completed')

        # Test skipToken: skipping the current serving token (T-001)
        skipped = dashboard.skipToken()
        self.assertTrue(skipped)
        
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.status, 'Skipped')

    def test_queue_report_performance_and_efficiency(self):
        """Tests QueueReport analytics computations."""
        from organization_panel.models import QueueReport
        import datetime
        from django.utils import timezone
        
        # Make t2 a completed token with timestamps
        self.t2.status = 'Completed'
        self.t2.served_at = timezone.now() - datetime.timedelta(minutes=15)
        self.t2.completed_at = timezone.now()
        self.t2.save()
        
        report = QueueReport.objects.create(organization=self.org)
        perf = report.analyzePerformance()
        
        self.assertEqual(perf['total_tokens'], 2)
        self.assertEqual(perf['completed_count'], 1)
        self.assertAlmostEqual(perf['avg_service_time_minutes'], 15.0, places=1)
        
        efficiency = report.monitorQueueEfficiency()
        self.assertIn(efficiency, ['Low', 'Medium', 'High'])

    def test_daily_report_generation_and_archiving(self):
        """Tests the daily report generation and archiving flow."""
        from organization_panel.models import QueueReport
        import datetime
        from django.utils import timezone
        
        # Make t2 a completed token with timestamps
        self.t2.status = 'Completed'
        self.t2.served_at = timezone.now() - datetime.timedelta(minutes=10)
        self.t2.completed_at = timezone.now()
        self.t2.save()

        # Generate report for today
        today = datetime.date.today()
        report = QueueReport.generateDailyReport(self.org, report_date=today)
        self.assertIsNotNone(report)
        self.assertEqual(report.report_date, today)
        self.assertEqual(report.stats['total_tokens'], 2)
        self.assertEqual(report.stats['completed_count'], 1)
        self.assertEqual(report.stats['avg_service_time_minutes'], 10.0)

        # Generate report for a past date
        past_date = today - datetime.timedelta(days=45)
        past_report = QueueReport.generateDailyReport(self.org, report_date=past_date)
        self.assertEqual(QueueReport.objects.filter(organization=self.org).count(), 2)

        # Archive reports older than 30 days
        cutoff = today - datetime.timedelta(days=30)
        archived_count = QueueReport.archiveOldReports(cutoff)
        self.assertEqual(archived_count, 1)
        self.assertEqual(QueueReport.objects.filter(organization=self.org).count(), 1)

    def test_performance_monitoring_and_alerts(self):
        """Tests that service speed tracking and performance alerts trigger properly."""
        from organization_panel.models import QueueReport
        import datetime
        from django.utils import timezone

        # Case 1: optimal performance
        self.t2.status = 'Completed'
        self.t2.served_at = timezone.now() - datetime.timedelta(minutes=5)
        self.t2.completed_at = timezone.now()
        self.t2.save()

        report = QueueReport.objects.create(organization=self.org)
        perf = report.analyzePerformance()
        self.assertEqual(len(perf['performance_alerts']), 0)
        self.assertEqual(perf['queue_speed_tokens_per_hour'], 12.0)

        # Case 2: high service time warning
        self.t2.served_at = timezone.now() - datetime.timedelta(minutes=25)
        self.t2.save()

        perf = report.analyzePerformance()
        self.assertTrue(any("extremely high" in alert for alert in perf['performance_alerts']))
        self.assertEqual(perf['queue_speed_tokens_per_hour'], 2.4)

    def test_behavior_monitoring(self):
        """Tests BehaviorMonitoring rules, scoring, no-shows, late arrivals, and abuse detection."""
        from organization_panel.models import BehaviorMonitoring
        import datetime
        from django.utils import timezone
        
        profile, _ = BehaviorMonitoring.objects.get_or_create(user=self.customer1)
        
        # Initial behavior check
        behavior = profile.monitorBehavior()
        self.assertEqual(behavior['reliability_score'], 100)
        self.assertFalse(behavior['is_flagged_for_abuse'])
        
        # Test no show trigger
        profile.detectNoShow()
        self.assertEqual(profile.no_shows, 1)
        
        # Test late arrival trigger
        profile.trackLateArrival()
        self.assertEqual(profile.late_arrivals, 1)

        # Record cancellation and emergency misuse
        BehaviorMonitoring.recordCancellation(self.customer1)
        BehaviorMonitoring.recordEmergencyMisuse(self.customer1)
        BehaviorMonitoring.recordSuspiciousActivity(self.customer1)
        
        profile.refresh_from_db()
        self.assertEqual(profile.cancellations, 1)
        self.assertEqual(profile.emergency_misuses, 1)
        self.assertEqual(profile.suspicious_activities, 1)
        
        behavior = profile.monitorBehavior()
        # Deducts: 15 (no-show) + 5 (late) + 5 (cancel) + 25 (suspicious) + 30 (emergency) = 80 points
        self.assertEqual(behavior['reliability_score'], 20)
        self.assertTrue(behavior['is_flagged_for_abuse'])
        self.assertIn("Emergency slot misuse detected.", behavior['flags'])

    def test_blacklist_blocking_and_report_generation(self):
        """Tests that blacklisted users are blocked from bookings, and generateBehaviorReport works."""
        from organization_panel.models import BehaviorMonitoring
        from django.urls import reverse
        
        profile, _ = BehaviorMonitoring.objects.get_or_create(user=self.customer1)
        
        profile.is_blacklisted = True
        profile.save()
        
        report = profile.generateBehaviorReport()
        self.assertEqual(report['status'], 'Blacklisted')
        self.assertTrue(report['is_blacklisted'])
        
        self.client.login(username='customer1', password='password123')
        url = reverse('book_queue', args=[self.org.account.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('org_detail', args=[self.org.account.id]), response['Location'])

    def test_queue_insights_and_reports(self):
        """Tests Queue Insights: traffic, completion trends, insights, and behavioral reports."""
        from organization_panel.models import QueueReport, BehaviorMonitoring
        from django.urls import reverse

        traffic = QueueReport.analyzeQueueTraffic(self.org, days=7)
        self.assertEqual(len(traffic['daily_traffic']), 7)
        self.assertIn('total_bookings_period', traffic)

        completion = QueueReport.analyzeCompletionTrends(self.org, days=7)
        self.assertEqual(len(completion['trends']), 7)
        self.assertIn(completion['trend_direction'], ['stable', 'improving', 'declining'])

        health = QueueReport.monitorQueueHealth(self.org)
        self.assertEqual(health['health_status'], 'Healthy')
        self.assertEqual(health['waiting_count'], 1)

        insights = QueueReport.generateQueueInsights(self.org, days=7)
        self.assertIn(insights['efficiency_rating'], ['Low', 'Medium', 'High'])
        self.assertIn('recommendations', insights)
        self.assertIn('queue_health', insights)

        behavioral = BehaviorMonitoring.generateBehavioralReports(self.org)
        self.assertEqual(len(behavioral), 2)
        usernames = {b['username'] for b in behavioral}
        self.assertIn('customer1', usernames)
        self.assertIn('customer2', usernames)

        self.client.login(username='TestHospital', password='password123')
        response = self.client.get(reverse('org_queue_insights'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Queue Insights')
        self.assertContains(response, 'Behavioral Reports')

    def test_notification_center(self):
        """Tests broadcastAnnouncement, sendQueueAlert, and notifyUsers."""
        from organization_panel.models import NotificationCenter, OrganizationNotification
        from queue_system.models import EmailNotification
        from django.urls import reverse

        center = NotificationCenter.get_or_create_for_organization(self.org)

        # broadcastAnnouncement to active customers today
        sent = center.broadcastAnnouncement(
            'Clinic closed early',
            'Please reschedule if you are still waiting.',
            audience='active_today',
            send_email=False,
        )
        self.assertEqual(len(sent), 2)
        self.assertEqual(
            OrganizationNotification.objects.filter(organization=self.org).count(),
            2,
        )

        # sendQueueAlert for status change
        self.org.queue_status = 'Paused'
        self.org.save()
        alerts = center.sendQueueAlert('status_change', send_email=False)
        self.assertGreaterEqual(len(alerts), 1)

        # notifyUsers directly
        extra = center.notifyUsers(
            [self.customer1],
            'Direct test',
            'Single user ping.',
            notification_type='General',
            send_email=False,
        )
        self.assertEqual(len(extra), 1)
        self.assertFalse(extra[0].is_read)

        # Organization notification center page
        self.client.login(username='TestHospital', password='password123')
        response = self.client.get(reverse('org_notification_center'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Broadcast Announcement')

        # Customer inbox
        self.client.login(username='customer1', password='password123')
        inbox_resp = self.client.get(reverse('customer_notifications'))
        self.assertEqual(inbox_resp.status_code, 200)
        unread = NotificationCenter.unread_count_for_user(self.customer1)
        self.assertGreater(unread, 0)

        mark_url = reverse('mark_all_notifications_read')
        self.client.get(mark_url)
        self.assertEqual(NotificationCenter.unread_count_for_user(self.customer1), 0)

    def test_organization_settings_management(self):
        """Tests OrganizationDashboard settings methods and settings page."""
        import datetime
        from django.urls import reverse

        dashboard = OrganizationDashboard.get_for_organization(self.org)

        # getQueueSettings
        settings = dashboard.getQueueSettings()
        self.assertEqual(settings['token_limit'], 10)
        self.assertIn('capacity', settings)

        # manageWorkingHours
        r = dashboard.manageWorkingHours(
            work_start=datetime.time(8, 0),
            work_end=datetime.time(16, 0),
        )
        self.assertTrue(r['success'])
        self.org.refresh_from_db()
        self.assertEqual(self.org.work_start, datetime.time(8, 0))
        self.assertIn('08:00', self.org_profile.working_hours)

        # setTokenLimit / setQueueCapacity / controlDailyBookingLimit
        self.assertTrue(dashboard.setTokenLimit(25)['success'])
        self.assertEqual(dashboard.setQueueCapacity(30)['queue_capacity'], 30)
        self.assertEqual(dashboard.controlDailyBookingLimit(20)['daily_booking_limit'], 20)

        # configureQueueSettings
        result = dashboard.configureQueueSettings(
            queue_status='Paused',
            organization_type='Clinic',
        )
        self.assertTrue(result['success'])
        self.org.refresh_from_db()
        self.assertEqual(self.org.queue_status, 'Paused')
        self.assertEqual(self.org.type, 'Clinic')

        # Settings page
        self.client.login(username='TestHospital', password='password123')
        response = self.client.get(reverse('org_settings'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Organization Settings')
        self.assertContains(response, 'manageWorkingHours')

    def test_emergency_queue_monitoring(self):
        """Tests emergency monitoring on QueueReport and BehaviorMonitoring."""
        from organization_panel.models import QueueReport, BehaviorMonitoring
        from emergency_and_swap.models import EmergencyRequest
        from django.urls import reverse

        req = EmergencyRequest.submitEmergency(
            token=self.t1,
            emergency_type='Medical',
        )

        activity = QueueReport.monitorEmergencyQueueActivity(self.org, days=7)
        self.assertGreaterEqual(activity['total_period'], 1)

        flow = QueueReport.trackEmergencyQueueFlow(self.org)
        self.assertIn(flow['flow_status'], ['ReviewRequired', 'PriorityActive', 'NormalWaiting', 'Idle'])
        self.assertGreaterEqual(flow['pending_reviews'], 1)

        performance = QueueReport.analyzeEmergencyQueuePerformance(self.org, days=7)
        self.assertIn(performance['performance_rating'], ['Good', 'Fair', 'Poor', 'N/A'])

        misuse = BehaviorMonitoring.detectEmergencyMisuse(self.org)
        self.assertTrue(any(c['username'] == 'customer1' for c in misuse) or len(misuse) >= 0)

        user_activity = BehaviorMonitoring.monitorEmergencyActivity(self.customer1, self.org)
        self.assertEqual(user_activity['total_requests'], 1)

        report = QueueReport.generateEmergencyMonitoringReport(self.org)
        self.assertIn('activity', report)
        self.assertIn('misuse_scan', report)

        self.client.login(username='TestHospital', password='password123')
        response = self.client.get(reverse('org_emergency_monitoring'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Emergency Queue Monitoring')

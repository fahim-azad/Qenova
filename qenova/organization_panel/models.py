from django.db import models
from django.conf import settings
from queue_system.models import Token, Organization
import datetime

class OrganizationDashboard(models.Model):
    """
    App 4 Blueprint: OrganizationDashboard
    Aggregates and formats dashboard statistics and health status metrics for an organization.
    """
    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name='dashboard')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Dashboard for {self.organization.account.organization_name}"

    def monitorQueue(self):
        """
        Monitors active queue status and computes health levels:
        - Healthy: Waitlist is small (< 5 tokens) or average wait time is low.
        - Congested: Waitlist is moderate (5-15 tokens).
        - Critical: Waitlist is large (> 15 tokens).
        """
        import datetime
        today = datetime.date.today()
        waiting_tokens = Token.objects.filter(
            organization=self.organization,
            booking_date=today,
            status='Waiting'
        )
        waiting_count = waiting_tokens.count()
        serving_token = Token.objects.filter(
            organization=self.organization,
            booking_date=today,
            status='Serving'
        ).first()

        if waiting_count > 15:
            health_status = "Critical"
        elif waiting_count >= 5:
            health_status = "Congested"
        else:
            health_status = "Healthy"

        return {
            'waiting_count': waiting_count,
            'serving_token_number': serving_token.serial_number if serving_token else None,
            'serving_token_user': serving_token.user.username if serving_token else None,
            'health_status': health_status
        }

    def generateDashboardStats(self):
        """
        Generates comprehensive statistics and widget data:
        - Total distinct customer users who have booked at this organization.
        - Overall token states distribution (Waiting, Serving, Completed, Cancelled, Expired).
        - Peak booking dates.
        """
        all_tokens = Token.objects.filter(organization=self.organization)
        total_bookings = all_tokens.count()
        
        # Distinct users count
        total_users = all_tokens.values('user').distinct().count()

        # Distribution (matches Token.status choices)
        stats = {
            'Waiting': all_tokens.filter(status='Waiting').count(),
            'Serving': all_tokens.filter(status='Serving').count(),
            'Completed': all_tokens.filter(status='Completed').count(),
            'Skipped': all_tokens.filter(status='Skipped').count(),
        }

        return {
            'total_bookings': total_bookings,
            'total_users': total_users,
            'status_distribution': stats,
        }

    def callNextToken(self):
        """
        App 4 Blueprint: callNextToken
        Marks any currently 'Serving' token as 'Completed' and calls the next 'Waiting' token.
        """
        import datetime
        from queue_system.models import QueueTracker, EmailNotification
        today = datetime.date.today()
        tracker, _ = QueueTracker.objects.get_or_create(organization=self.organization)

        # Mark any currently 'Serving' token as 'Completed'
        current = Token.objects.filter(
            organization=self.organization, booking_date=today, status='Serving'
        ).first()
        if current:
            current.updateStatus('Completed')

        # Get the next waiting token in queue order
        next_token = Token.objects.filter(
            organization=self.organization, booking_date=today, status='Waiting'
        ).order_by('id').first()

        if next_token:
            next_token.updateStatus('Serving')
            tracker.current_token = next_token
            tracker.save()
            tracker.trackQueue()

            # Send queue update emails
            EmailNotification.sendQueueUpdate(next_token)

            # Send reminder to the next customer in line
            upcoming = Token.objects.filter(
                organization=self.organization,
                booking_date=today,
                status='Waiting'
            ).order_by('id').first()
            if upcoming:
                EmailNotification.sendReminder(upcoming)
            
            return next_token
        else:
            tracker.current_token = None
            tracker.save()
            return None

    def skipToken(self, token_id=None):
        """
        App 4 Blueprint: skipToken
        Skips the specified token (by token_id) or the currently serving token,
        marking it as 'Skipped' and moving the queue forward.
        """
        import datetime
        from queue_system.models import QueueTracker
        today = datetime.date.today()
        tracker, _ = QueueTracker.objects.get_or_create(organization=self.organization)

        token_to_skip = None
        if token_id:
            token_to_skip = Token.objects.filter(id=token_id, organization=self.organization).first()
        else:
            # Skip currently serving token
            token_to_skip = Token.objects.filter(
                organization=self.organization, booking_date=today, status='Serving'
            ).first()
            
            # If no serving token, skip the first waiting token
            if not token_to_skip:
                token_to_skip = Token.objects.filter(
                    organization=self.organization, booking_date=today, status='Waiting'
                ).order_by('id').first()

        if token_to_skip:
            token_to_skip.updateStatus('Skipped')
            if tracker.current_token == token_to_skip:
                tracker.current_token = None
                tracker.save()
            tracker.trackQueue()
            return True
        return False

    @classmethod
    def get_for_organization(cls, organization):
        dashboard, _ = cls.objects.get_or_create(organization=organization)
        return dashboard

    def getQueueSettings(self):
        """
        Returns current queue configuration and live capacity snapshot.
        """
        org = self.organization
        capacity = org.manageQueueCapacity()
        return {
            'organization_name': org.account.organization_name,
            'organization_type': org.type,
            'queue_status': org.queue_status,
            'token_limit': org.token_limit,
            'daily_booking_limit': org.token_limit,
            'queue_capacity': org.token_limit,
            'work_start': org.work_start,
            'work_end': org.work_end,
            'working_hours_display': org.getWorkingHoursDisplay(),
            'is_within_working_hours': org.isWithinWorkingHours(),
            'capacity': capacity,
        }

    def manageWorkingHours(self, work_start=None, work_end=None, clear=False):
        """
        App 4 Blueprint: manageWorkingHours
        Sets or clears structured working hours and syncs the profile text field.
        """
        org = self.organization
        if clear:
            org.work_start = None
            org.work_end = None
            org.account.working_hours = 'Open all day'
        else:
            if work_start and work_end and work_start >= work_end:
                return {'success': False, 'error': 'Opening time must be before closing time.'}
            org.work_start = work_start
            org.work_end = work_end
            org.account.working_hours = org.getWorkingHoursDisplay()
        org.save()
        org.account.save()
        self.save(update_fields=['updated_at'])
        return {
            'success': True,
            'working_hours_display': org.getWorkingHoursDisplay(),
        }

    def setTokenLimit(self, limit):
        """
        App 4 Blueprint: setTokenLimit
        Sets the maximum number of tokens issued per day.
        """
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return {'success': False, 'error': 'Token limit must be a valid number.'}
        if limit < 1 or limit > 1000:
            return {'success': False, 'error': 'Token limit must be between 1 and 1000.'}
        self.organization.setTokenLimit(limit)
        self.save(update_fields=['updated_at'])
        return {'success': True, 'token_limit': limit}

    def setQueueCapacity(self, capacity):
        """
        App 4 Blueprint: setQueueCapacity
        Sets maximum daily queue capacity (same as daily token limit).
        """
        result = self.setTokenLimit(capacity)
        if result.get('success'):
            result['queue_capacity'] = result['token_limit']
        return result

    def controlDailyBookingLimit(self, limit):
        """
        App 4 Blueprint: controlDailyBookingLimit
        Controls how many bookings are accepted per day.
        """
        result = self.setTokenLimit(limit)
        if result.get('success'):
            result['daily_booking_limit'] = result['token_limit']
        return result

    def configureQueueSettings(
        self,
        token_limit=None,
        work_start=None,
        work_end=None,
        queue_status=None,
        organization_type=None,
        clear_hours=False,
    ):
        """
        App 4 Blueprint: configureQueueSettings
        Updates one or more queue settings in a single operation.
        """
        org = self.organization
        changes = []
        errors = []

        if token_limit is not None:
            r = self.setTokenLimit(token_limit)
            if r.get('success'):
                changes.append(f"Daily limit set to {r['token_limit']}")
            else:
                errors.append(r.get('error', 'Invalid token limit'))

        if clear_hours:
            r = self.manageWorkingHours(clear=True)
            if r.get('success'):
                changes.append('Working hours cleared (open all day)')
        elif work_start is not None or work_end is not None:
            r = self.manageWorkingHours(work_start=work_start, work_end=work_end)
            if r.get('success'):
                changes.append(f"Hours: {r['working_hours_display']}")
            else:
                errors.append(r.get('error', 'Invalid working hours'))

        if queue_status is not None:
            if queue_status in ('Active', 'Paused', 'Closed'):
                org.updateQueueStatus(queue_status)
                changes.append(f"Queue status: {queue_status}")
            else:
                errors.append('Invalid queue status.')

        if organization_type is not None and organization_type.strip():
            org.type = organization_type.strip()[:100]
            org.save(update_fields=['type'])
            changes.append(f"Organization type: {org.type}")

        self.save(update_fields=['updated_at'])
        return {
            'success': len(errors) == 0,
            'changes': changes,
            'errors': errors,
            'settings': self.getQueueSettings(),
        }

class QueueReport(models.Model):
    """
    App 4 Blueprint: QueueReport
    Stores organization queue reports and runs analytics performance/efficiency algorithms.
    """
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='reports')
    report_date = models.DateField(default=datetime.date.today)
    stats = models.JSONField(null=True, blank=True)
    efficiency_rating = models.CharField(max_length=20, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Queue Report {self.id} for {self.organization.account.organization_name}"

    def analyzePerformance(self):
        """
        App 4 Blueprint: analyzePerformance
        Calculates queue stats, average service times, peak hours, and trends.
        """
        tokens = Token.objects.filter(organization=self.organization)
        total_tokens = tokens.count()

        completed_tokens = tokens.filter(status='Completed')
        completed_count = completed_tokens.count()

        # Calculate average service time in minutes
        total_service_time_seconds = 0
        valid_completed = 0
        for t in completed_tokens:
            if t.completed_at and t.served_at:
                diff = (t.completed_at - t.served_at).total_seconds()
                if diff > 0:
                    total_service_time_seconds += diff
                    valid_completed += 1

        avg_service_time_minutes = 0.0
        if valid_completed > 0:
            avg_service_time_minutes = round((total_service_time_seconds / valid_completed) / 60.0, 2)

        # Detect peak hours (using booking created_at hour)
        from django.db.models.functions import ExtractHour
        from django.db.models import Count
        
        hour_counts = tokens.filter(booking__isnull=False).annotate(
            hour=ExtractHour('booking__created_at')
        ).values('hour').annotate(count=Count('id')).order_by('-count')
        
        peak_hours = []
        for hc in hour_counts:
            if hc['hour'] is not None:
                peak_hours.append({
                    'hour': hc['hour'],
                    'count': hc['count']
                })

        # Generate queue trends (by date)
        date_counts = tokens.values('booking_date').annotate(count=Count('id')).order_by('booking_date')
        trends = []
        for dc in date_counts:
            trends.append({
                'date': dc['booking_date'].strftime('%Y-%m-%d'),
                'count': dc['count']
            })

        # Create queue heatmap data (breakdown of bookings by weekday and hour)
        from django.db.models.functions import ExtractWeekDay
        heatmap_query = tokens.filter(booking__isnull=False).annotate(
            hour=ExtractHour('booking__created_at'),
            weekday=ExtractWeekDay('booking__created_at')
        ).values('hour', 'weekday').annotate(count=Count('id'))

        heatmap_data = []
        for item in heatmap_query:
            if item['hour'] is not None and item['weekday'] is not None:
                heatmap_data.append({
                    'hour': item['hour'],
                    'weekday': item['weekday'],
                    'count': item['count']
                })

        # Performance Alert detection
        performance_alerts = []
        if avg_service_time_minutes > 20.0:
            performance_alerts.append("CRITICAL: Average service time is extremely high (over 20 minutes)!")
        elif avg_service_time_minutes > 10.0:
            performance_alerts.append("WARNING: Service speed is slower than optimal (10-20 minutes).")

        skipped_count = tokens.filter(status='Skipped').count()
        skipped_rate = (skipped_count / total_tokens * 100.0) if total_tokens > 0 else 0.0
        if skipped_rate > 30.0:
            performance_alerts.append(f"WARNING: High rate of skipped tokens ({round(skipped_rate, 1)}%) detected.")

        completion_rate = (completed_count / total_tokens * 100.0) if total_tokens > 0 else 0.0
        if total_tokens > 5 and completion_rate < 50.0:
            performance_alerts.append(f"WARNING: Low queue completion rate ({round(completion_rate, 1)}%).")

        if peak_hours and peak_hours[0]['count'] > 15:
            performance_alerts.append(f"NOTICE: High booking congestion during hour {peak_hours[0]['hour']}:00.")

        queue_speed_tokens_per_hour = round(60.0 / avg_service_time_minutes, 1) if avg_service_time_minutes > 0 else 0.0

        return {
            'total_tokens': total_tokens,
            'completed_count': completed_count,
            'avg_service_time_minutes': avg_service_time_minutes,
            'peak_hours': peak_hours,
            'trends': trends,
            'heatmap_data': heatmap_data,
            'performance_alerts': performance_alerts,
            'queue_speed_tokens_per_hour': queue_speed_tokens_per_hour
        }

    def monitorQueueEfficiency(self):
        """
        App 4 Blueprint: monitorQueueEfficiency
        Evaluates queue efficiency rating based on average service time and completed/skipped ratios.
        """
        perf = self.analyzePerformance()
        avg_time = perf['avg_service_time_minutes']
        total = perf['total_tokens']
        completed = perf['completed_count']

        if total == 0:
            return 'High'

        completion_rate = (completed / total) * 100.0

        if avg_time > 20.0 or completion_rate < 50.0:
            return 'Low'
        elif avg_time > 10.0 or completion_rate < 80.0:
            return 'Medium'
        else:
            return 'High'

    @classmethod
    def generateDailyReport(cls, organization, report_date=None):
        """
        App 4 Blueprint: generateDailyReport
        Generates a daily queue performance report for the specified organization and date,
        stores the statistics in JSON format, archives the report, and returns the report instance.
        """
        from django.db.models import Count
        from django.db.models.functions import ExtractHour
        
        if report_date is None:
            report_date = datetime.date.today()
        elif isinstance(report_date, str):
            report_date = datetime.datetime.strptime(report_date, '%Y-%m-%d').date()

        tokens = Token.objects.filter(organization=organization, booking_date=report_date)
        total_tokens = tokens.count()

        completed_tokens = tokens.filter(status='Completed')
        completed_count = completed_tokens.count()

        # Calculate average service time in minutes
        total_service_time_seconds = 0
        valid_completed = 0
        for t in completed_tokens:
            if t.completed_at and t.served_at:
                diff = (t.completed_at - t.served_at).total_seconds()
                if diff > 0:
                    total_service_time_seconds += diff
                    valid_completed += 1

        avg_service_time_minutes = 0.0
        if valid_completed > 0:
            avg_service_time_minutes = round((total_service_time_seconds / valid_completed) / 60.0, 2)

        # Skip count
        skipped_count = tokens.filter(status='Skipped').count()

        # Peak hour detection for that specific day
        hour_counts = tokens.filter(booking__isnull=False).annotate(
            hour=ExtractHour('booking__created_at')
        ).values('hour').annotate(count=Count('id')).order_by('-count')
        peak_hour = None
        if hour_counts.exists():
            peak_hour = hour_counts.first()['hour']

        stats = {
            'total_tokens': total_tokens,
            'completed_count': completed_count,
            'skipped_count': skipped_count,
            'avg_service_time_minutes': avg_service_time_minutes,
            'peak_hour': peak_hour
        }

        # Determine efficiency rating
        if total_tokens == 0:
            efficiency_rating = 'High'
        else:
            completion_rate = (completed_count / total_tokens) * 100.0
            if avg_service_time_minutes > 20.0 or completion_rate < 50.0:
                efficiency_rating = 'Low'
            elif avg_service_time_minutes > 10.0 or completion_rate < 80.0:
                efficiency_rating = 'Medium'
            else:
                efficiency_rating = 'High'

        # Get or create the report for this organization and date to store history
        report, created = cls.objects.get_or_create(
            organization=organization,
            report_date=report_date
        )
        report.stats = stats
        report.efficiency_rating = efficiency_rating
        report.save()

        return report

    @classmethod
    def analyzeQueueTraffic(cls, organization, days=7):
        """
        Analyzes booking traffic for an organization over the last N days.
        Returns daily volume and hourly breakdown for today.
        """
        from django.db.models import Count
        from django.db.models.functions import ExtractHour

        today = datetime.date.today()
        daily_traffic = []
        for i in range(days - 1, -1, -1):
            day = today - datetime.timedelta(days=i)
            day_tokens = Token.objects.filter(organization=organization, booking_date=day)
            daily_traffic.append({
                'date': day.strftime('%Y-%m-%d'),
                'total': day_tokens.count(),
                'waiting': day_tokens.filter(status='Waiting').count(),
                'completed': day_tokens.filter(status='Completed').count(),
                'skipped': day_tokens.filter(status='Skipped').count(),
            })

        today_tokens = Token.objects.filter(organization=organization, booking_date=today)
        hourly = (
            today_tokens.filter(booking__isnull=False)
            .annotate(hour=ExtractHour('booking__created_at'))
            .values('hour')
            .annotate(count=Count('id'))
            .order_by('hour')
        )
        hourly_breakdown = [
            {'hour': row['hour'], 'count': row['count']}
            for row in hourly if row['hour'] is not None
        ]

        peak_day = max(daily_traffic, key=lambda d: d['total']) if daily_traffic else None
        peak_hour_row = max(hourly_breakdown, key=lambda h: h['count']) if hourly_breakdown else None

        return {
            'days_analyzed': days,
            'daily_traffic': daily_traffic,
            'hourly_breakdown_today': hourly_breakdown,
            'peak_day': peak_day,
            'peak_hour_today': peak_hour_row,
            'total_bookings_period': sum(d['total'] for d in daily_traffic),
        }

    @classmethod
    def analyzeCompletionTrends(cls, organization, days=7):
        """
        Analyzes completion vs skip rates over the last N days.
        """
        today = datetime.date.today()
        trends = []
        for i in range(days - 1, -1, -1):
            day = today - datetime.timedelta(days=i)
            day_tokens = Token.objects.filter(organization=organization, booking_date=day)
            total = day_tokens.count()
            completed = day_tokens.filter(status='Completed').count()
            skipped = day_tokens.filter(status='Skipped').count()
            completion_rate = round((completed / total) * 100.0, 1) if total > 0 else 0.0
            skip_rate = round((skipped / total) * 100.0, 1) if total > 0 else 0.0
            trends.append({
                'date': day.strftime('%Y-%m-%d'),
                'total': total,
                'completed': completed,
                'skipped': skipped,
                'completion_rate': completion_rate,
                'skip_rate': skip_rate,
            })

        rates = [t['completion_rate'] for t in trends if t['total'] > 0]
        avg_completion = round(sum(rates) / len(rates), 1) if rates else 0.0

        direction = 'stable'
        if len(rates) >= 2:
            if rates[-1] > rates[0] + 5:
                direction = 'improving'
            elif rates[-1] < rates[0] - 5:
                direction = 'declining'

        return {
            'days_analyzed': days,
            'trends': trends,
            'average_completion_rate': avg_completion,
            'trend_direction': direction,
        }

    @classmethod
    def monitorQueueHealth(cls, organization):
        """
        Monitors live queue health using dashboard metrics and tracker flow state.
        """
        from queue_system.models import QueueTracker

        dashboard, _ = OrganizationDashboard.objects.get_or_create(organization=organization)
        monitor = dashboard.monitorQueue()
        tracker, _ = QueueTracker.objects.get_or_create(organization=organization)
        flow = tracker.monitorQueueFlow()

        alerts = []
        if monitor['health_status'] == 'Critical':
            alerts.append('Queue waitlist is critically long (>15 waiting).')
        elif monitor['health_status'] == 'Congested':
            alerts.append('Queue is congested (5–15 customers waiting).')

        if flow['health'] == 'Overloaded':
            alerts.append('Queue flow is overloaded relative to daily capacity.')
        if organization.queue_status != 'Active':
            alerts.append(f'Queue is currently {organization.queue_status}.')

        return {
            'health_status': monitor['health_status'],
            'flow_health': flow['health'],
            'waiting_count': monitor['waiting_count'],
            'serving_token': monitor['serving_token_number'],
            'serving_user': monitor['serving_token_user'],
            'queue_status': organization.queue_status,
            'flow_breakdown': {
                'waiting': flow['waiting'],
                'serving': flow['serving'],
                'completed': flow['completed'],
                'skipped': flow['skipped'],
            },
            'alerts': alerts,
        }

    @classmethod
    def generateQueueInsights(cls, organization, days=7):
        """
        Generates a consolidated insights package: health, traffic, completion trends,
        efficiency rating, and actionable recommendations.
        """
        cls.generateDailyReport(organization, datetime.date.today())
        report, _ = cls.objects.get_or_create(
            organization=organization,
            report_date=datetime.date.today(),
        )
        performance = report.analyzePerformance()
        efficiency = report.monitorQueueEfficiency()
        traffic = cls.analyzeQueueTraffic(organization, days=days)
        completion = cls.analyzeCompletionTrends(organization, days=days)
        health = cls.monitorQueueHealth(organization)

        recommendations = []
        if efficiency == 'Low':
            recommendations.append('Review staffing or reduce daily token limit to improve service speed.')
        if completion['trend_direction'] == 'declining':
            recommendations.append('Completion rate is declining — check for frequent no-shows or skips.')
        if traffic['peak_hour_today'] and traffic['peak_hour_today']['count'] >= 5:
            h = traffic['peak_hour_today']['hour']
            recommendations.append(f'Peak traffic today around {h}:00 — consider extra counter staff.')
        if health['alerts']:
            recommendations.append('Address queue health alerts shown above to reduce customer wait times.')
        if not recommendations:
            recommendations.append('Queue operations look healthy. Keep monitoring daily reports.')

        return {
            'efficiency_rating': efficiency,
            'performance': performance,
            'queue_health': health,
            'traffic': traffic,
            'completion_trends': completion,
            'recommendations': recommendations,
        }

    @classmethod
    def monitorEmergencyQueueActivity(cls, organization, days=7):
        """
        App 4 Blueprint: monitorEmergencyQueueActivity
        Tracks daily emergency request volume and status breakdown.
        """
        from emergency_and_swap.models import EmergencyRequest

        today = datetime.date.today()
        daily_activity = []
        for i in range(days - 1, -1, -1):
            day = today - datetime.timedelta(days=i)
            day_reqs = EmergencyRequest.objects.filter(
                token__organization=organization,
                created_at__date=day,
            )
            daily_activity.append({
                'date': day.strftime('%Y-%m-%d'),
                'total': day_reqs.count(),
                'pending': day_reqs.filter(status='Pending').count(),
                'approved': day_reqs.filter(status='Approved').count(),
                'rejected': day_reqs.filter(status='Rejected').count(),
            })

        today_reqs = EmergencyRequest.objects.filter(
            token__organization=organization,
            created_at__date=today,
        )
        all_reqs = EmergencyRequest.objects.filter(token__organization=organization)

        return {
            'days_analyzed': days,
            'daily_activity': daily_activity,
            'total_period': sum(d['total'] for d in daily_activity),
            'pending_today': today_reqs.filter(status='Pending').count(),
            'approved_today': today_reqs.filter(status='Approved').count(),
            'rejected_today': today_reqs.filter(status='Rejected').count(),
            'total_all_time': all_reqs.count(),
            'pending_all': all_reqs.filter(status='Pending').count(),
        }

    @classmethod
    def trackEmergencyQueueFlow(cls, organization):
        """
        App 4 Blueprint: trackEmergencyQueueFlow
        Monitors live emergency priority queue flow and pending reviews.
        """
        from emergency_and_swap.models import EmergencyRequest, PriorityQueue

        today = datetime.date.today()
        waiting_tokens = Token.objects.filter(
            organization=organization,
            booking_date=today,
            status='Waiting',
        ).count()

        priority_queue = PriorityQueue.objects.filter(
            token__organization=organization,
            token__booking_date=today,
            token__status='Waiting',
        ).select_related('token', 'token__user').order_by('insertion_position')

        pending_requests = EmergencyRequest.objects.filter(
            token__organization=organization,
            status='Pending',
        ).select_related('token', 'token__user').order_by('-created_at')

        if pending_requests.exists():
            flow_status = 'ReviewRequired'
        elif priority_queue.exists():
            flow_status = 'PriorityActive'
        elif waiting_tokens > 0:
            flow_status = 'NormalWaiting'
        else:
            flow_status = 'Idle'

        return {
            'flow_status': flow_status,
            'waiting_tokens': waiting_tokens,
            'priority_slots_active': priority_queue.count(),
            'pending_reviews': pending_requests.count(),
            'priority_queue': [
                {
                    'priority_serial': p.priority_serial,
                    'token_serial': p.token.serial_number,
                    'username': p.token.user.username,
                    'position': p.insertion_position,
                    'urgency': p.urgency_level,
                }
                for p in priority_queue
            ],
            'pending_requests': [
                {
                    'id': r.id,
                    'token_serial': r.token.serial_number,
                    'username': r.token.user.username,
                    'emergency_type': r.emergency_type,
                    'created_at': r.created_at.strftime('%Y-%m-%d %H:%M'),
                }
                for r in pending_requests[:20]
            ],
        }

    @classmethod
    def analyzeEmergencyQueuePerformance(cls, organization, days=7):
        """
        App 4 Blueprint: analyzeEmergencyQueuePerformance
        Evaluates emergency queue efficiency, approval rates, and risk alerts.
        """
        from emergency_and_swap.models import EmergencyAnalytics, EmergencyRequest

        activity = cls.monitorEmergencyQueueActivity(organization, days=days)
        flow = cls.trackEmergencyQueueFlow(organization)
        trends = EmergencyAnalytics.analyzeEmergencyTrends(organization)
        misuse_cases = BehaviorMonitoring.detectEmergencyMisuse(organization)

        total = trends['total_requests']
        approval_rate = trends['approval_rate']
        rejection_rate = trends['rejection_rate']

        if total == 0:
            performance_rating = 'N/A'
        elif approval_rate >= 70 and len(misuse_cases) == 0:
            performance_rating = 'Good'
        elif approval_rate >= 40 or rejection_rate < 60:
            performance_rating = 'Fair'
        else:
            performance_rating = 'Poor'

        alerts = []
        if misuse_cases:
            alerts.append(f'{len(misuse_cases)} suspected emergency misuse case(s) require review.')
        if activity['pending_all'] >= 3:
            alerts.append(f'{activity["pending_all"]} emergency request(s) still awaiting review.')
        if flow['flow_status'] == 'PriorityActive' and flow['waiting_tokens'] > 10:
            alerts.append('Priority queue is active while the main waitlist is congested.')
        if rejection_rate > 50 and total >= 3:
            alerts.append(f'High rejection rate ({rejection_rate:.1f}%) — review submission criteria.')

        from django.utils import timezone
        pending_over_24h = EmergencyRequest.objects.filter(
            token__organization=organization,
            status='Pending',
            created_at__lt=timezone.now() - datetime.timedelta(hours=24),
        ).count()
        if pending_over_24h:
            alerts.append(f'{pending_over_24h} request(s) pending for over 24 hours.')

        return {
            'days_analyzed': days,
            'performance_rating': performance_rating,
            'approval_rate': approval_rate,
            'rejection_rate': rejection_rate,
            'trends': trends,
            'activity': activity,
            'flow': flow,
            'misuse_case_count': len(misuse_cases),
            'alerts': alerts,
        }

    @classmethod
    def generateEmergencyMonitoringReport(cls, organization, days=7):
        """Consolidated emergency queue monitoring package for the organization panel."""
        return {
            'activity': cls.monitorEmergencyQueueActivity(organization, days=days),
            'flow': cls.trackEmergencyQueueFlow(organization),
            'performance': cls.analyzeEmergencyQueuePerformance(organization, days=days),
            'misuse_scan': BehaviorMonitoring.detectEmergencyMisuse(organization),
        }

    @classmethod
    def archiveOldReports(cls, before_date):
        """
        App 4 Blueprint: archiveOldReports
        Removes/archives reports older than the specified date.
        """
        old_reports = cls.objects.filter(report_date__lt=before_date)
        count = old_reports.count()
        old_reports.delete()
        return count

class BehaviorMonitoring(models.Model):
    """
    App 4 Blueprint: BehaviorMonitoring
    Tracks user behaviors, queue abuse, no-show occurrences, late arrivals, cancellations, and emergency misuse.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='behavior_records')
    no_shows = models.PositiveIntegerField(default=0)
    late_arrivals = models.PositiveIntegerField(default=0)
    cancellations = models.PositiveIntegerField(default=0)
    suspicious_activities = models.PositiveIntegerField(default=0)
    emergency_misuses = models.PositiveIntegerField(default=0)
    is_blacklisted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Behavior profile for {self.user.username}"

    @classmethod
    def recordCancellation(cls, user):
        record, _ = cls.objects.get_or_create(user=user)
        record.cancellations += 1
        record.save()
        return record

    @classmethod
    def recordEmergencyMisuse(cls, user):
        record, _ = cls.objects.get_or_create(user=user)
        record.emergency_misuses += 1
        record.save()
        return record

    @classmethod
    def recordSuspiciousActivity(cls, user):
        record, _ = cls.objects.get_or_create(user=user)
        record.suspicious_activities += 1
        record.save()
        return record

    def detectNoShow(self):
        """
        App 4 Blueprint: detectNoShow
        Increments no-show counter for the user.
        """
        self.no_shows += 1
        self.save()

    def trackLateArrival(self):
        """
        App 4 Blueprint: trackLateArrival
        Increments late arrival counter for the user.
        """
        self.late_arrivals += 1
        self.save()

    def monitorBehavior(self):
        """
        App 4 Blueprint: monitorBehavior
        Calculates user reliability score, detects abuse and returns reliability details.
        """
        score = 100
        score -= self.no_shows * 15
        score -= self.late_arrivals * 5
        score -= self.cancellations * 5
        score -= self.suspicious_activities * 25
        score -= self.emergency_misuses * 30
        
        reliability_score = max(0, score)
        
        flags = []
        if self.no_shows >= 3:
            flags.append("Repeated no-shows detected.")
        if self.emergency_misuses >= 1:
            flags.append("Emergency slot misuse detected.")
        if self.suspicious_activities >= 2:
            flags.append("Suspicious booking activity / slot hoarding detected.")
            
        queue_abuse_flag = reliability_score < 60 or len(flags) >= 2

        # Auto blacklist check
        if reliability_score < 40 or self.suspicious_activities >= 3 or self.no_shows >= 5:
            if not self.is_blacklisted:
                self.is_blacklisted = True
                self.save()
        
        return {
            'reliability_score': reliability_score,
            'is_flagged_for_abuse': queue_abuse_flag,
            'is_blacklisted': self.is_blacklisted,
            'flags': flags,
            'stats': {
                'no_shows': self.no_shows,
                'late_arrivals': self.late_arrivals,
                'cancellations': self.cancellations,
                'suspicious_activities': self.suspicious_activities,
                'emergency_misuses': self.emergency_misuses
            }
        }

    def generateBehaviorReport(self):
        """
        App 4 Blueprint: generateBehaviorReport
        Creates a behavior summary report for audit, detailing current status.
        """
        metrics = self.monitorBehavior()
        status = "Active"
        if self.is_blacklisted:
            status = "Blacklisted"
        elif metrics['is_flagged_for_abuse']:
            status = "Suspended/Warning"
            
        return {
            'username': self.user.username,
            'reliability_score': metrics['reliability_score'],
            'status': status,
            'is_blacklisted': self.is_blacklisted,
            'is_flagged_for_abuse': metrics['is_flagged_for_abuse'],
            'flags': metrics['flags'],
            'total_no_shows': self.no_shows,
            'total_cancellations': self.cancellations,
            'total_late_arrivals': self.late_arrivals,
            'total_emergency_misuses': self.emergency_misuses,
            'total_suspicious_activities': self.suspicious_activities,
        }

    @classmethod
    def monitorEmergencyActivity(cls, user, organization):
        """
        App 4 Blueprint: monitorEmergencyActivity
        Tracks a user's emergency request history at a specific organization.
        """
        from emergency_and_swap.models import EmergencyRequest

        reqs = EmergencyRequest.objects.filter(
            token__user=user,
            token__organization=organization,
        )
        total = reqs.count()
        approved = reqs.filter(status='Approved').count()
        rejected = reqs.filter(status='Rejected').count()
        pending = reqs.filter(status='Pending').count()

        return {
            'username': user.username,
            'total_requests': total,
            'approved': approved,
            'rejected': rejected,
            'pending': pending,
            'approval_rate': round((approved / total) * 100, 1) if total else 0.0,
            'rejection_rate': round((rejected / total) * 100, 1) if total else 0.0,
        }

    @classmethod
    def detectEmergencyMisuse(cls, organization, auto_record=False):
        """
        App 4 Blueprint: detectEmergencyMisuse
        Scans emergency requests at an organization for suspicious patterns.
        Uses EmergencyAnalytics.detectFakeEmergency and flags repeat rejections.
        """
        from emergency_and_swap.models import EmergencyRequest, EmergencyAnalytics
        from django.db.models import Count

        misuse_cases = []
        seen_users = set()

        requests = EmergencyRequest.objects.filter(
            token__organization=organization,
        ).select_related('token', 'token__user')

        for req in requests:
            check = EmergencyAnalytics.detectFakeEmergency(req)
            user = req.token.user
            profile, _ = cls.objects.get_or_create(user=user)

            reasons = list(check.get('reasons', []))
            if req.status == 'Rejected' and user.id not in seen_users:
                rejected_at_org = EmergencyRequest.objects.filter(
                    token__user=user,
                    token__organization=organization,
                    status='Rejected',
                ).count()
                if rejected_at_org >= 2:
                    reasons.append(f'User has {rejected_at_org} rejected emergencies at this organization.')

            if check['is_suspicious'] or len(reasons) > len(check.get('reasons', [])):
                activity = cls.monitorEmergencyActivity(user, organization)
                misuse_cases.append({
                    'request_id': req.id,
                    'user_id': user.id,
                    'username': user.username,
                    'token_serial': req.token.serial_number,
                    'emergency_type': req.emergency_type,
                    'status': req.status,
                    'reasons': reasons if reasons else check['reasons'],
                    'recent_week_count': check['recent_count'],
                    'emergency_misuses_recorded': profile.emergency_misuses,
                    'activity': activity,
                })
                seen_users.add(user.id)
                if auto_record:
                    cls.recordEmergencyMisuse(user)

        repeat_offenders = (
            EmergencyRequest.objects.filter(
                token__organization=organization,
                status='Rejected',
            )
            .values('token__user_id', 'token__user__username')
            .annotate(reject_count=Count('id'))
            .filter(reject_count__gte=3)
        )
        for row in repeat_offenders:
            uid = row['token__user_id']
            if uid not in seen_users:
                profile, _ = cls.objects.get_or_create(user_id=uid)
                misuse_cases.append({
                    'request_id': None,
                    'user_id': uid,
                    'username': row['token__user__username'],
                    'token_serial': '—',
                    'emergency_type': '—',
                    'status': 'Pattern',
                    'reasons': [f'{row["reject_count"]} rejected emergency requests (repeat offender).'],
                    'recent_week_count': row['reject_count'],
                    'emergency_misuses_recorded': profile.emergency_misuses,
                    'activity': cls.monitorEmergencyActivity(profile.user, organization),
                })

        return misuse_cases

    @classmethod
    def generateBehavioralReports(cls, organization):
        """
        Generates behavioral reports for every customer who has booked at this organization.
        Sorted by reliability score (lowest risk first for review priority).
        """
        user_ids = (
            Token.objects.filter(organization=organization)
            .values_list('user_id', flat=True)
            .distinct()
        )
        reports = []
        for user_id in user_ids:
            profile, _ = cls.objects.get_or_create(user_id=user_id)
            reports.append(profile.generateBehaviorReport())
        reports.sort(key=lambda r: r['reliability_score'])
        return reports


class OrganizationNotification(models.Model):
    """
    App 4 Blueprint: persisted in-app notification delivered to a user.
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='notifications_sent',
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='organization_notifications',
    )
    subject = models.CharField(max_length=255)
    message = models.TextField()
    notification_type = models.CharField(max_length=50, default='General', choices=[
        ('Announcement', 'Broadcast Announcement'),
        ('QueueAlert', 'Queue Alert'),
        ('General', 'General'),
    ])
    is_read = models.BooleanField(default=False)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-sent_at']

    def __str__(self):
        return f"[{self.notification_type}] {self.subject} → {self.recipient.username}"

    def mark_read(self):
        if not self.is_read:
            self.is_read = True
            self.save(update_fields=['is_read'])


class NotificationCenter(models.Model):
    """
    App 4 Blueprint: NotificationCenter
    Central hub for organization broadcast announcements, queue alerts, and dynamic user notifications.
    """
    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        related_name='notification_center',
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Notification Center — {self.organization.account.organization_name}"

    def _resolve_audience(self, audience='active_today'):
        """Returns a queryset of distinct customer users for the given audience key."""
        from django.contrib.auth import get_user_model
        User = get_user_model()
        today = datetime.date.today()
        base = Token.objects.filter(organization=self.organization)

        if audience == 'waiting_only':
            user_ids = base.filter(booking_date=today, status='Waiting').values_list('user_id', flat=True)
        elif audience == 'all_customers':
            user_ids = base.values_list('user_id', flat=True).distinct()
        else:
            # active_today: anyone with a Waiting or Serving token today
            user_ids = base.filter(
                booking_date=today,
                status__in=['Waiting', 'Serving'],
            ).values_list('user_id', flat=True)

        return User.objects.filter(id__in=user_ids, is_customer=True).distinct()

    def notifyUsers(self, users, subject, message, notification_type='General', send_email=True):
        """
        App 4 Blueprint: notifyUsers
        Delivers notifications to a list/queryset of users (in-app + optional email).
        Returns the list of OrganizationNotification records created.
        """
        from django.core.mail import send_mail
        from queue_system.models import EmailNotification

        org_name = self.organization.account.organization_name
        created = []
        email_type = 'Update' if notification_type == 'QueueAlert' else 'General'

        for user in users:
            full_message = (
                f"Hello {user.username},\n\n"
                f"{message}\n\n"
                f"— {org_name} via QeNova Notification Center"
            )
            note = OrganizationNotification.objects.create(
                organization=self.organization,
                recipient=user,
                subject=subject,
                message=full_message,
                notification_type=notification_type,
            )
            created.append(note)

            if send_email and user.email:
                send_mail(subject, full_message, None, [user.email], fail_silently=True)
                EmailNotification.objects.create(
                    user=user,
                    subject=subject,
                    message=full_message,
                    email_type=email_type,
                )

        self.save(update_fields=['updated_at'])
        return created

    def broadcastAnnouncement(self, subject, message, audience='active_today', send_email=True):
        """
        App 4 Blueprint: broadcastAnnouncement
        Sends an organization-wide announcement to the selected audience.
        """
        users = self._resolve_audience(audience)
        if not users.exists():
            return []

        announcement_subject = f"QeNova Announcement — {self.organization.account.organization_name}: {subject}"
        return self.notifyUsers(
            users,
            announcement_subject,
            message,
            notification_type='Announcement',
            send_email=send_email,
        )

    def sendQueueAlert(self, alert_type, custom_message=None, token=None, send_email=True):
        """
        App 4 Blueprint: sendQueueAlert
        Sends dynamic queue alerts based on alert_type:
        - status_change, queue_full, token_called, queue_reset, general
        """
        org_name = self.organization.account.organization_name
        today = datetime.date.today()

        if alert_type == 'status_change':
            status = self.organization.queue_status
            subject = f"QeNova Queue Alert — {org_name} queue is now {status}"
            message = custom_message or (
                f"The queue at {org_name} has been updated to: {status}. "
                f"Please check your token status before visiting."
            )
            users = self._resolve_audience('active_today')

        elif alert_type == 'queue_full':
            subject = f"QeNova Queue Alert — {org_name} is at capacity"
            message = custom_message or (
                f"The daily token limit at {org_name} has been reached. "
                f"No new bookings are available for today."
            )
            users = self._resolve_audience('all_customers')

        elif alert_type == 'token_called' and token:
            subject = f"QeNova Queue Alert — Token {token.serial_number} is being served"
            message = custom_message or (
                f"Token {token.serial_number} is now being served at {org_name}. "
                f"If this is your token, please proceed to the counter."
            )
            users = self._resolve_audience('waiting_only')

        elif alert_type == 'queue_reset':
            subject = f"QeNova Queue Alert — {org_name} queue has been reset"
            message = custom_message or (
                f"The queue at {org_name} was reset by staff. "
                f"Remaining waiting tokens for today may have been expired."
            )
            users = self._resolve_audience('active_today')

        else:
            subject = f"QeNova Queue Alert — {org_name}"
            message = custom_message or "There is an update regarding your queue. Please check your dashboard."
            users = self._resolve_audience('active_today')

        if not users.exists():
            return []

        return self.notifyUsers(
            users,
            subject,
            message,
            notification_type='QueueAlert',
            send_email=send_email,
        )

    @classmethod
    def get_or_create_for_organization(cls, organization):
        center, _ = cls.objects.get_or_create(organization=organization)
        return center

    @classmethod
    def get_inbox_for_user(cls, user, unread_only=False):
        """Returns notifications received by a customer."""
        qs = OrganizationNotification.objects.filter(recipient=user).select_related(
            'organization', 'organization__account',
        )
        if unread_only:
            qs = qs.filter(is_read=False)
        return qs

    @classmethod
    def unread_count_for_user(cls, user):
        return OrganizationNotification.objects.filter(recipient=user, is_read=False).count()

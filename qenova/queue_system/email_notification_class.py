

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

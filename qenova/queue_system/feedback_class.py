

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

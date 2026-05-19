from django import forms
from .models import QueueBooking
import datetime

class QueueBookingForm(forms.ModelForm):
    is_instant = forms.BooleanField(
        required=False, 
        initial=True, 
        label="Instant Booking (Join the queue right now)",
        help_text="Uncheck this if you want to book for a future date."
    )
    
    class Meta:
        model = QueueBooking
        fields = ['booking_date']
        widgets = {
            'booking_date': forms.DateInput(attrs={'type': 'date'})
        }

    def clean_booking_date(self):
        date = self.cleaned_data.get('booking_date')
        if date < datetime.date.today():
            raise forms.ValidationError("You cannot book a queue for a past date.")
        return date


class RescheduleBookingForm(forms.Form):
    new_date = forms.DateField(
        label="New Booking Date",
        widget=forms.DateInput(attrs={'type': 'date'})
    )

    def clean_new_date(self):
        date = self.cleaned_data.get('new_date')
        if date < datetime.date.today():
            raise forms.ValidationError("You cannot reschedule to a past date.")
        return date


class FeedbackForm(forms.ModelForm):
    class Meta:
        from .models import Feedback
        model = Feedback
        fields = ['rating', 'comment']
        widgets = {
            'rating': forms.Select(choices=[
                (1, '1 Star'),
                (2, '2 Stars'),
                (3, '3 Stars'),
                (4, '4 Stars'),
                (5, '5 Stars')
            ]),
            'comment': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Optional details about your queue experience...'})
        }


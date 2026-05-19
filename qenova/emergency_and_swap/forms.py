from django import forms
from .models import EmergencyRequest
import os

class EmergencyRequestForm(forms.ModelForm):
    class Meta:
        model = EmergencyRequest
        fields = ['emergency_type', 'document']
        widgets = {
            'emergency_type': forms.Select(choices=[
                ('Medical', 'Medical Emergency'),
                ('Urgent Business', 'Urgent Official/Business Duty'),
                ('Disability', 'Disability/Senior Citizen Assistance'),
                ('Other', 'Other Urgent Situation')
            ], attrs={'required': True}),
            'document': forms.FileInput(attrs={'accept': '.pdf,.jpg,.jpeg,.png'})
        }
        help_texts = {
            'document': 'Please upload supporting documents (PDF, JPG, PNG) to prove urgency.'
        }

    def clean_document(self):
        doc = self.cleaned_data.get('document')
        if doc:
            # 1. Validate File Extension
            ext = os.path.splitext(doc.name)[1].lower()
            valid_extensions = ['.pdf', '.jpg', '.jpeg', '.png']
            if ext not in valid_extensions:
                raise forms.ValidationError("Unsupported file format. Only PDF, JPG, JPEG, and PNG are allowed.")

            # 2. Validate File Size (Maximum 5 MB)
            max_size = 5 * 1024 * 1024  # 5MB
            if doc.size > max_size:
                raise forms.ValidationError("File size exceeds the 5MB limit. Please upload a smaller document.")
        return doc


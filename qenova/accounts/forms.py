from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from .models import User

class UserRegistrationForm(UserCreationForm):
    email = forms.EmailField(required=True)
    is_customer = forms.BooleanField(required=False, initial=True, widget=forms.HiddenInput())

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_customer = self.cleaned_data.get('is_customer', True)
        if commit:
            user.save()
        return user

class OrganizationRegistrationForm(UserCreationForm):
    email = forms.EmailField(required=True)
    organization_name = forms.CharField(max_length=255, required=True)
    logo = forms.ImageField(required=False)
    
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_organization = True
        user.is_customer = False
        user.is_active = False  # Require email verification
        if commit:
            user.save()
            from .models import OrganizationUser
            OrganizationUser.objects.create(
                user=user,
                organization_name=self.cleaned_data.get('organization_name'),
                logo=self.cleaned_data.get('logo')
            )
        return user

class UserProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'profile_picture')

class CustomAuthenticationForm(AuthenticationForm):
    remember_me = forms.BooleanField(required=False, initial=False, help_text="Keep me logged in")

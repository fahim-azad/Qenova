from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager

class User(AbstractUser):

    is_customer = models.BooleanField(default=False)
    is_organization = models.BooleanField(default=False)
    profile_picture = models.ImageField(upload_to='profiles/', blank=True, null=True)
    status = models.CharField(max_length=50, default='Active')

    def __str__(self):
        return self.username



class OrganizationUser(models.Model):

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='organization_profile')
    organization_name = models.CharField(max_length=255)
    logo = models.ImageField(upload_to='logos/', blank=True, null=True)
    working_hours = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return self.organization_name



class AdminManager(BaseUserManager):
    def get_queryset(self):
        return super().get_queryset().filter(is_superuser=True)

class Admin(User):

    objects = AdminManager()

    class Meta:
        proxy = True


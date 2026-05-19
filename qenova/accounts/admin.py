from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, OrganizationUser, Admin

class UserAdmin(BaseUserAdmin):
    # Add our custom fields to the admin form
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Custom Info', {'fields': ('is_customer', 'is_organization', 'profile_picture', 'status')}),
    )
    # Display these columns in the list view
    list_display = ('username', 'email', 'is_active', 'is_customer', 'is_organization', 'status', 'is_staff')
    # Add filters for easy sorting
    list_filter = ('is_active', 'is_customer', 'is_organization', 'status', 'is_staff')
    
    actions = ['approve_organizations']

    @admin.action(description='Approve selected organizations')
    def approve_organizations(self, request, queryset):
        # Only approve users that are organizations
        orgs = queryset.filter(is_organization=True)
        updated = orgs.update(is_active=True, status='Approved')
        self.message_user(request, f'Successfully approved {updated} organization(s).')

@admin.register(OrganizationUser)
class OrganizationUserAdmin(admin.ModelAdmin):
    list_display = ('user', 'organization_name', 'working_hours')
    search_fields = ('organization_name', 'user__username')
    
    actions = ['approve_organizations_profile']

    @admin.action(description='Approve selected organizations')
    def approve_organizations_profile(self, request, queryset):
        # We are selecting OrganizationUser models here, so we need to update the related User models
        user_ids = queryset.values_list('user_id', flat=True)
        updated = User.objects.filter(id__in=user_ids).update(is_active=True, status='Approved')
        self.message_user(request, f'Successfully approved {updated} organization(s).')

# Register the models
admin.site.register(User, UserAdmin)

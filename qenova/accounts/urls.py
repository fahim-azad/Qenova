from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('register/', views.register_view, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('profile/', views.profile_view, name='profile'),
    path('activate/<uidb64>/<token>/', views.activate_account_view, name='activate'),
    
    # Organization URLs
    path('org-register/', views.org_register_view, name='org_register'),
    path('org-login/', views.org_login_view, name='org_login'),
    path('org-dashboard/', views.org_dashboard_view, name='org_dashboard'),
    path('org-dashboard/update-status/', views.update_queue_status_view, name='update_queue_status'),
    path('org-dashboard/call-next/', views.call_next_token_view, name='call_next_token'),
    path('org-dashboard/reset-queue/', views.reset_queue_view, name='reset_queue'),
    path('org-dashboard/set-limit/', views.set_token_limit_view, name='set_token_limit'),
    path('org-dashboard/set-hours/', views.set_working_hours_view, name='set_working_hours'),
    path('org-dashboard/live-status/', views.org_live_status_api_view, name='org_live_status_api'),
    path('org-dashboard/skip-token/', views.skip_token_view, name='skip_token'),
    path('org-dashboard/skip-token/<int:token_id>/', views.skip_token_view, name='skip_token_by_id'),
    path('org-dashboard/reports/', views.org_reports_view, name='org_reports'),
    
    # Password Reset URLs
    path('password-reset/', 
         auth_views.PasswordResetView.as_view(template_name='accounts/password_reset.html'), 
         name='password_reset'),
    path('password-reset/done/', 
         auth_views.PasswordResetDoneView.as_view(template_name='accounts/password_reset_done.html'), 
         name='password_reset_done'),
    path('password-reset-confirm/<uidb64>/<token>/', 
         auth_views.PasswordResetConfirmView.as_view(template_name='accounts/password_reset_confirm.html'), 
         name='password_reset_confirm'),
    path('password-reset-complete/', 
         auth_views.PasswordResetCompleteView.as_view(template_name='accounts/password_reset_complete.html'), 
         name='password_reset_complete'),
         
    # Change Password URLs
    path('password-change/', 
         auth_views.PasswordChangeView.as_view(template_name='accounts/password_change.html'), 
         name='password_change'),
    path('password-change/done/', 
         auth_views.PasswordChangeDoneView.as_view(template_name='accounts/password_change_done.html'), 
         name='password_change_done'),
]

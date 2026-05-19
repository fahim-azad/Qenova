from django.urls import path
from . import views

urlpatterns = [
    # Emergency Requests
    path('emergency/submit/<int:token_id>/', views.submit_emergency_view, name='submit_emergency'),
    path('org/emergencies/', views.org_emergencies_view, name='org_emergencies'),
    path('emergency/approve/<int:request_id>/', views.approve_emergency_view, name='approve_emergency'),
    path('emergency/reject/<int:request_id>/', views.reject_emergency_view, name='reject_emergency'),

    # Swapping System
    path('swap/list/<int:token_id>/', views.swap_list_view, name='swap_list'),
    path('swap/request/<int:token_id>/<int:target_token_id>/', views.request_swap_view, name='request_swap'),
    path('swap/approve/<int:swap_id>/', views.approve_swap_view, name='approve_swap'),
    path('swap/reject/<int:swap_id>/', views.reject_swap_view, name='reject_swap'),

    # Priority Adjustment
    path('priority/adjust/<int:priority_id>/', views.adjust_priority_position_view, name='adjust_priority_position'),
]

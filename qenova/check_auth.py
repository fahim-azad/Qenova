import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'qenova.settings')
django.setup()

from django.contrib.auth import authenticate, get_user_model
User = get_user_model()

print("Checking 'fahim'...")
user = User.objects.filter(username='fahim').first()
if user:
    print(f"Exists: True")
    print(f"Is Active: {user.is_active}")
    u = authenticate(username='fahim', password='azad')
    print(f"Auth success with 'azad': {u is not None}")
else:
    print("User 'fahim' does not exist.")

from accounts.forms import CustomAuthenticationForm
from django.http import HttpRequest

print("\nTesting CustomAuthenticationForm with 'fahim' and 'azad':")
request = HttpRequest()
request.method = 'POST'
request.POST = {'username': 'fahim', 'password': 'azad'}
form = CustomAuthenticationForm(request, data=request.POST)
if form.is_valid():
    print("Form is valid!")
else:
    print(f"Form is invalid! Errors: {form.errors}")

print("\nTesting CustomAuthenticationForm with 'azad' (inactive):")
request.POST = {'username': 'azad', 'password': 'somepassword'}
form2 = CustomAuthenticationForm(request, data=request.POST)
if form2.is_valid():
    print("Form2 is valid!")
else:
    print(f"Form2 is invalid! Errors: {form2.errors}")


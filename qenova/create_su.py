import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'qenova.settings')
django.setup()

from django.contrib.auth import get_user_model
User = get_user_model()

if not User.objects.filter(username='fahim').exists():
    User.objects.create_superuser('fahim', 'fahim@example.com', 'azad')
    print("Superuser created successfully.")
else:
    print("Superuser already exists.")

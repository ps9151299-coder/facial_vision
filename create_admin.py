import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proctor_system.settings')
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()
if not User.objects.filter(username="admin").exists():
    User.objects.create_superuser("admin", "admin@exam-system.local", "adminpassword2026")
    print("Superuser 'admin' created with password 'adminpassword2026'")
else:
    print("Superuser 'admin' already exists.")

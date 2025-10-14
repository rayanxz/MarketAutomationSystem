from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from accounts.models import AccountProfile


class Command(BaseCommand):
    help = "Removes all users and account profiles so the system behaves like first run."

    def handle(self, *args, **options):
        User = get_user_model()
        AccountProfile.objects.all().delete()
        User.objects.all().delete()
        self.stdout.write(self.style.SUCCESS("All accounts removed. On next run, you'll be asked to create an Owner."))

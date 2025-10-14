from django.shortcuts import redirect
from accounts.utils import owner_exists  # <- correct import

# Allow-list: routes reachable before the owner exists
ALLOW = (
    "/setup/owner/",
    "/login/",
    "/logout/",
    "/admin/",      # admin and its static
    "/static/",     # static files
    "/favicon.ico",
)

class OwnerProvisioningMiddleware:
    """
    If no owner profile exists yet, redirect everything to /setup/owner/
    except for explicitly allowed paths.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not owner_exists():
            path = request.path
            if not any(path.startswith(p) for p in ALLOW):
                return redirect("owner_setup")  # by URL name
        return self.get_response(request)

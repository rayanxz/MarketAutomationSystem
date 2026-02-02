# app/core/middleware.py
import re
from urllib.parse import quote
from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse, NoReverseMatch


def _login_path():
    """
    Resolve LOGIN_URL to a path. Accepts a URL name ('login') or a path ('/login/').
    """
    val = getattr(settings, "LOGIN_URL", "/login/")
    if val.startswith("/"):
        return val
    try:
        return reverse(val)
    except NoReverseMatch:
        return "/login/"

class RequireLoginMiddleware:
    """
    Redirect any unauthenticated request to LOGIN_URL,
    except whitelisted paths.
    """
    def __init__(self, get_response):
        self.get_response = get_response
        self.exempt = [
            re.compile(r"^%s?$" % re.escape(_login_path())),
            re.compile(r"^/logout/?$"),
            re.compile(r"^/admin/login/?$"),
            re.compile(r"^/setup/owner/?$"),   # allow first-time owner setup page
            re.compile(r"^/static/"),
            re.compile(r"^/media/"),
        ]

    def __call__(self, request):
        if request.user.is_authenticated:
            return self.get_response(request)
        path = request.path_info or "/"
        for pat in self.exempt:
            if pat.match(path):
                return self.get_response(request)
        login_url = _login_path()
        return redirect(f"{login_url}?next={quote(path)}")

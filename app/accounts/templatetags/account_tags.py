
from __future__ import annotations

from django import template

from ..utils import profile_for

register = template.Library()


@register.simple_tag(takes_context=True)
def account_profile(context):
    request = context.get("request")
    user = getattr(request, "user", None)
    return profile_for(user)

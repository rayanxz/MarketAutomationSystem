# C:\MarketAutomationSystem\app\marketpos\views.py

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.shortcuts import render, redirect


class OwnerForm(forms.Form):
    username = forms.CharField(label="Username")
    password = forms.CharField(widget=forms.PasswordInput, label="Password")
    email = forms.EmailField(required=False, label="Email (optional)")


def owner_setup_view(request):
    """
    First-time owner provisioning. Redirects away if a superuser already exists.
    Renders templates/accounts/owner.html.مشروبات غازية
    """
    User = get_user_model()

    # If an owner already exists, don't allow this page
    if User.objects.filter(is_superuser=True).exists():
        return redirect("/")

    form = OwnerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        User.objects.create(
            username=form.cleaned_data["username"],
            password=make_password(form.cleaned_data["password"]),
            email=form.cleaned_data.get("email", ""),
            is_superuser=True,
            is_staff=True,
            is_active=True,
        )
        # After creating the owner, send to Django admin (you can change later)
        return redirect("/admin/")

    return render(request, "accounts/owner.html", {"form": form})

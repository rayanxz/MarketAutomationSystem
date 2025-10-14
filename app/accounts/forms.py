# accounts/forms.py
from __future__ import annotations

import re
from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password
from django.core.validators import RegexValidator
from django.contrib.auth.models import AbstractBaseUser

from .models import AccountProfile
from .utils import is_owner, owner_exists

User = get_user_model()

# =========================
# Validation policy
# =========================

# Username: English letters + spaces only, 3–12 chars
USERNAME_RE = r'^[A-Za-z ]{3,12}$'
username_validator = RegexValidator(
    regex=USERNAME_RE,
    message="اسم المستخدم يجب أن يحتوي على أحرف إنجليزية ومسافات فقط (من 3 إلى 12 حرفًا)."
)

# Password: any non-whitespace, length 6–20
PASSWORD_RE = r'^\S{6,20}$'
password_validator = RegexValidator(
    regex=PASSWORD_RE,
    message="كلمة المرور يجب أن تكون 6–20 حرفًا دون أي مسافات."
)

def normalize_username(value: str) -> str:
    value = (value or "").strip()
    value = re.sub(r'\s+', ' ', value)
    return value


# =========================
# Forms
# =========================

class LoginForm(AuthenticationForm):
    username = forms.CharField(label="اسم المستخدم", widget=forms.TextInput(attrs={"class": "input"}))
    password = forms.CharField(label="كلمة المرور", widget=forms.PasswordInput(attrs={"class": "input"}), strip=False)


class OwnerSetupForm(forms.Form):
    username = forms.CharField(
        label="اسم المستخدم",
        validators=[username_validator],
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    password1 = forms.CharField(
        label="كلمة المرور",
        validators=[password_validator],
        widget=forms.PasswordInput(attrs={"class": "input"}),
        strip=False,
    )
    password2 = forms.CharField(
        label="تأكيد كلمة المرور",
        validators=[password_validator],
        widget=forms.PasswordInput(attrs={"class": "input"}),
        strip=False,
    )

    def clean_username(self) -> str:
        username = normalize_username(self.cleaned_data.get("username", ""))
        username_validator(username)
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("اسم المستخدم مستخدم بالفعل.")
        return username

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")

        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("كلمتا المرور غير متطابقتين.")
        if p1:
            password_validator(p1)
            validate_password(p1)

        if owner_exists():
            raise forms.ValidationError("يوجد حساب مالك بالفعل.")

        return cleaned

    def save(self) -> AbstractBaseUser:
        username = self.cleaned_data["username"]
        password = self.cleaned_data["password1"]

        user = User.objects.create_user(username=username, password=password)
        user.is_staff = True
        user.is_active = True
        user.save()

        AccountProfile.objects.create(
            user=user,
            role=AccountProfile.Role.OWNER,
        )
        return user


class NewStaffAccountForm(forms.Form):
    """
    Owner UI: create MANAGER/CASHIER.
    Fields: owner_username/owner_password/new_username/new_password1/new_password2/role
    """
    # Owner re-auth
    owner_username = forms.CharField(label="اسم مالك النظام", widget=forms.TextInput(attrs={"class": "input"}))
    owner_password = forms.CharField(label="كلمة مرور المالك", widget=forms.PasswordInput(attrs={"class": "input"}), strip=False)

    # New account
    new_username = forms.CharField(
        label="اسم المستخدم الجديد",
        validators=[username_validator],
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    new_password1 = forms.CharField(
        label="كلمة المرور الجديدة",
        validators=[password_validator],
        widget=forms.PasswordInput(attrs={"class": "input"}), strip=False,
    )
    new_password2 = forms.CharField(
        label="تأكيد كلمة المرور",
        validators=[password_validator],
        widget=forms.PasswordInput(attrs={"class": "input"}), strip=False,
    )

    role = forms.ChoiceField(
        label="الدور",
        choices=[
            (AccountProfile.Role.MANAGER, "مدير"),
            (AccountProfile.Role.CASHIER, "كاشير"),
        ],
        widget=forms.Select(attrs={"class": "input"}),
    )

    def clean_new_username(self) -> str:
        username = normalize_username(self.cleaned_data.get("new_username", ""))
        username_validator(username)
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("اسم المستخدم موجود مسبقاً.")
        return username

    def clean(self):
        cleaned = super().clean()

        # new password checks
        p1 = cleaned.get("new_password1")
        p2 = cleaned.get("new_password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("كلمتا المرور غير متطابقتين.")
        if p1:
            password_validator(p1)
            validate_password(p1)

        # owner re-auth
        owner_username_raw = cleaned.get("owner_username") or ""
        owner_password = cleaned.get("owner_password")
        owner_username = normalize_username(owner_username_raw)
        if not owner_username or not owner_password:
            raise forms.ValidationError("يرجى إدخال بيانات اعتماد المالك.")

        owner_user = authenticate(username=owner_username, password=owner_password)
        if not owner_user or not is_owner(owner_user):
            raise forms.ValidationError("بيانات اعتماد المالك غير صحيحة.")

        cleaned["owner_username"] = owner_username
        return cleaned

    def save(self) -> AbstractBaseUser:
        username = self.cleaned_data["new_username"]
        password = self.cleaned_data["new_password1"]
        role = self.cleaned_data["role"]

        user = User.objects.create_user(username=username, password=password)
        # Staff flag for manager; cashier can be non-staff
        if role == AccountProfile.Role.MANAGER:
            user.is_staff = True
        user.is_active = True
        user.save()

        AccountProfile.objects.create(user=user, role=role)
        return user


class MainAccountUpdateForm(forms.Form):
    """Edit the OWNER (current) account — used on the dedicated owner edit page."""
    current_username = forms.CharField(label="اسم المستخدم الحالي", widget=forms.TextInput(attrs={"class": "input"}))
    current_password = forms.CharField(label="كلمة المرور الحالية", widget=forms.PasswordInput(attrs={"class": "input"}), strip=False)

    new_username = forms.CharField(
        label="اسم المستخدم الجديد",
        required=False,
        validators=[username_validator],
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    new_password1 = forms.CharField(
        label="كلمة المرور الجديدة",
        required=False,
        validators=[password_validator],
        widget=forms.PasswordInput(attrs={"class": "input"}), strip=False,
    )
    new_password2 = forms.CharField(
        label="تأكيد كلمة المرور الجديدة",
        required=False,
        validators=[password_validator],
        widget=forms.PasswordInput(attrs={"class": "input"}), strip=False,
    )

    def __init__(self, main_user: AbstractBaseUser, *args, **kwargs):
        self.main_user = main_user
        super().__init__(*args, **kwargs)
        self.fields["current_username"].initial = main_user.username

    def clean(self):
        cleaned = super().clean()
        current_username = (cleaned.get("current_username") or "").strip()
        current_password = cleaned.get("current_password")

        if current_username != self.main_user.username:
            raise forms.ValidationError("اسم المستخدم لا يطابق الحساب الحالي.")
        if not self.main_user.check_password(current_password):
            raise forms.ValidationError("كلمة المرور الحالية غير صحيحة.")

        # New username checks
        new_username = normalize_username(cleaned.get("new_username") or "")
        if new_username and new_username != self.main_user.username:
            username_validator(new_username)
            if User.objects.filter(username__iexact=new_username).exists():
                raise forms.ValidationError("اسم المستخدم الجديد مستخدم مسبقًا.")

        # New password checks
        p1 = cleaned.get("new_password1")
        p2 = cleaned.get("new_password2")
        if p1 or p2:
            if not p1 or not p2:
                raise forms.ValidationError("يرجى إدخال كلمة المرور الجديدة وتأكيدها.")
            if p1 != p2:
                raise forms.ValidationError("كلمتا المرور الجديدتان غير متطابقتين.")
            password_validator(p1)
            validate_password(p1, user=self.main_user)

        cleaned["new_username"] = new_username
        return cleaned

    def save(self) -> AbstractBaseUser:
        new_username = self.cleaned_data.get("new_username") or ""
        new_password = self.cleaned_data.get("new_password1")

        updated = False
        if new_username and new_username != self.main_user.username:
            self.main_user.username = new_username
            updated = True
        if new_password:
            self.main_user.set_password(new_password)
            updated = True

        if updated:
            self.main_user.save()
        return self.main_user


# =========================
# Manage staff: quick edit / delete (manager & cashier only)
# =========================

class StaffQuickEditForm(forms.Form):
    """Quick edit username and/or password for MANAGER or CASHIER (NOT owner)."""
    user_id = forms.IntegerField(widget=forms.HiddenInput)

    new_username = forms.CharField(
        label="اسم المستخدم الجديد",
        required=False,
        validators=[username_validator],
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    new_password1 = forms.CharField(
        label="كلمة المرور الجديدة",
        required=False,
        validators=[password_validator],
        widget=forms.PasswordInput(attrs={"class": "input"}), strip=False,
    )
    new_password2 = forms.CharField(
        label="تأكيد كلمة المرور",
        required=False,
        validators=[password_validator],
        widget=forms.PasswordInput(attrs={"class": "input"}), strip=False,
    )

    def clean(self):
        cleaned = super().clean()
        uid = cleaned.get("user_id")
        new_username = normalize_username(cleaned.get("new_username") or "")
        p1 = cleaned.get("new_password1")
        p2 = cleaned.get("new_password2")

        # Must change at least one thing
        if not new_username and not p1 and not p2:
            raise forms.ValidationError("قم بتعديل اسم المستخدم أو كلمة المرور أو كلاهما.")

        # Password checks (if provided)
        if p1 or p2:
            if not p1 or not p2:
                raise forms.ValidationError("يرجى إدخال كلمة المرور وتأكيدها.")
            if p1 != p2:
                raise forms.ValidationError("كلمتا المرور غير متطابقتين.")
            password_validator(p1)
            # validate against policies for this user
            target_user = User.objects.filter(id=uid).first()
            validate_password(p1, user=target_user)

        # Get target user
        try:
            user = User.objects.get(id=uid)
        except User.DoesNotExist:
            raise forms.ValidationError("المستخدم غير موجود.")

        # Ensure has profile and not owner
        try:
            profile = user.account_profile
        except AccountProfile.DoesNotExist:
            raise forms.ValidationError("لا يوجد ملف صلاحيات لهذا المستخدم.")

        if profile.role == AccountProfile.Role.OWNER:
            raise forms.ValidationError("لا يمكن تعديل حساب المالك من هنا.")

        # Username uniqueness
        if new_username and new_username.lower() != (user.username or "").lower():
            username_validator(new_username)
            if User.objects.filter(username__iexact=new_username).exclude(pk=user.pk).exists():
                raise forms.ValidationError("اسم المستخدم الجديد مستخدم مسبقًا.")

        cleaned["new_username"] = new_username
        cleaned["_target_user"] = user
        return cleaned

    def save(self) -> AbstractBaseUser:
        user = self.cleaned_data["_target_user"]
        new_username = self.cleaned_data.get("new_username") or ""
        new_password = self.cleaned_data.get("new_password1")

        if new_username:
            user.username = new_username
        if new_password:
            user.set_password(new_password)
        user.save()
        return user


class StaffDeleteForm(forms.Form):
    """Delete MANAGER/CASHIER (NOT owner)."""
    user_id = forms.IntegerField(widget=forms.HiddenInput)

    def clean(self):
        cleaned = super().clean()
        uid = cleaned.get("user_id")
        try:
            user = User.objects.get(id=uid)
        except User.DoesNotExist:
            raise forms.ValidationError("المستخدم غير موجود.")

        try:
            profile = user.account_profile
        except AccountProfile.DoesNotExist:
            raise forms.ValidationError("لا يوجد ملف صلاحيات لهذا المستخدم.")

        if profile.role == AccountProfile.Role.OWNER:
            raise forms.ValidationError("لا يمكن حذف حساب المالك.")

        cleaned["_target_user"] = user
        return cleaned

    def delete(self) -> None:
        user = self.cleaned_data["_target_user"]
        user.delete()

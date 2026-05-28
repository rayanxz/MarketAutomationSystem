from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from accounts.models import AccountProfile
from billing.models import Bill, BillItem, Provider, ProviderPhone
from billing.services_provider import add_provider_phone
from catalog.models import Product, ProductCollection, ProductSet, UnitType


class ProviderProfileDetailsPhase2Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username="provider_profile_details_mgr",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)

    def setUp(self):
        self.client.force_login(self.manager)
        self.provider = Provider.objects.create(
            name="مورد الاختبار",
            phone="0999000111",
            notes="ملاحظة مبدئية",
        )

    def _details_url(self, provider_ref: str) -> str:
        return reverse("billing_provider_details", kwargs={"provider_ref": provider_ref})

    def _create_product(self, *, idx: int, name: str) -> Product:
        collection = ProductCollection.objects.create(name=f"زمرة {idx}")
        product_set = ProductSet.objects.create(collection=collection, name=f"مجموعة {idx}")
        return Product.objects.create(
            name=name,
            set=product_set,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
        )

    def _create_bill_item(self, *, product: Product, qty: Decimal) -> None:
        bill = Bill.objects.create(
            provider=self.provider,
            total=Decimal("0.00"),
            total_syp=Decimal("0.00"),
            total_usd=Decimal("0.00"),
        )
        BillItem.objects.create(
            bill=bill,
            product=product,
            qty_primary=qty,
            cost=Decimal("1.00"),
            price=Decimal("1.00"),
            line_total=qty,
            currency="SYP",
        )

    def test_provider_details_page_loads_by_public_id(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ملف المورد")

    def test_general_info_section_shows_public_id_and_name(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="top-section-general-info"')
        self.assertContains(resp, 'id="provider-public-id"')
        self.assertContains(resp, self.provider.public_id)
        self.assertContains(resp, 'id="provider-name"')
        self.assertContains(resp, self.provider.name)
        self.assertContains(resp, 'id="provider-created-at"')
        self.assertContains(resp, "تاريخ إنشاء ملف المورد")
        self.assertNotContains(resp, "آخر رقم هاتف")
        self.assertNotContains(resp, "عدد أرقام الهاتف")

    def test_phone_count_text_not_displayed(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'id="provider-phone-count"')
        self.assertNotContains(resp, "عدد أرقام الهاتف")

    def test_top_section_has_three_boxes(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="top-section-grid"')
        self.assertContains(resp, 'id="top-section-general-info"')
        self.assertContains(resp, 'id="top-section-phone-numbers"')
        self.assertContains(resp, 'id="top-section-notes"')
        self.assertContains(resp, 'grid-template-areas:"general phones notes";')

        html = resp.content.decode("utf-8")
        self.assertLess(html.find('id="top-section-general-info"'), html.find('id="top-section-phone-numbers"'))
        self.assertLess(html.find('id="top-section-phone-numbers"'), html.find('id="top-section-notes"'))

    def test_multiple_phone_numbers_are_displayed(self):
        ProviderPhone.objects.create(provider=self.provider, phone_number="0933000001")
        ProviderPhone.objects.create(provider=self.provider, phone_number="0933000002")
        self.provider.phone = "0933000002"
        self.provider.save(update_fields=["phone"])

        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "0933000001")
        self.assertContains(resp, "0933000002")
        self.assertContains(resp, 'id="provider-phones-wrap"')
        self.assertContains(resp, 'class="phones-wrap"')
        self.assertContains(resp, "max-height:196px;")

    def test_phone_list_supports_compact_controls_markup(self):
        phone_row = ProviderPhone.objects.create(provider=self.provider, phone_number="0933111111")
        self.provider.phone = phone_row.phone_number
        self.provider.save(update_fields=["phone"])

        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-phone-row="existing"')
        self.assertContains(resp, "data-phone-input")
        self.assertContains(resp, "data-phone-default-btn")
        self.assertContains(resp, "data-phone-edit-icon")
        self.assertContains(resp, "data-phone-delete-icon")
        self.assertContains(resp, "data-phone-action-cancel-btn")
        self.assertContains(resp, "data-phone-edit-cancel-btn")
        self.assertContains(resp, 'id="phone-add-row"')
        self.assertContains(resp, 'id="phone-add-input"')

    def test_default_phone_row_shows_only_edit_button(self):
        ProviderPhone.objects.create(provider=self.provider, phone_number="0933555000")
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-phone-default-btn")
        self.assertContains(resp, ">تعديل</button>", html=False)
        self.assertContains(resp, "data-phone-action-group")
        self.assertContains(resp, "data-phone-edit-group")
        self.assertContains(resp, "data-phone-input")
        self.assertContains(resp, "readonly")

    def test_action_mode_controls_are_hidden_until_edit_button_is_pressed(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-phone-action-group")
        self.assertContains(resp, "data-phone-edit-group")
        self.assertContains(resp, 'setRowMode(row, "default");')
        self.assertContains(resp, 'setRowMode(row, "action");')
        self.assertContains(resp, "activeMode = \"action\";")

    def test_edit_icon_makes_same_field_editable_in_place(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "input: row.querySelector(\"[data-phone-input]\")")
        self.assertContains(resp, "nodes.input.readOnly = mode !== \"edit\";")
        self.assertContains(resp, "setRowMode(row, \"edit\");")
        self.assertNotContains(resp, "data-phone-edit-form")

    def test_delete_icon_opens_confirmation_modal(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="phone-delete-modal"')
        self.assertContains(resp, 'id="phone-delete-confirm-btn"')
        self.assertContains(resp, 'id="phone-delete-cancel-btn"')
        self.assertContains(resp, "لا يمكن التراجع عن حذف رقم الهاتف بعد المتابعة.")
        self.assertContains(resp, "openDeleteModal(nodes.deleteForm);")

    def test_action_mode_cancel_returns_row_to_default(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "data-phone-action-cancel-btn")
        self.assertContains(resp, 'setRowMode(row, "default");')
        self.assertContains(resp, "activeMode = \"idle\";")

    def test_add_phone_is_blocked_while_row_is_active(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "addPhoneBtn.disabled = isLocked;")
        self.assertContains(resp, 'if (activeMode !== "idle") return;')
        self.assertNotContains(resp, "أكمل الإجراء الحالي أولاً")

    def test_phone_success_message_has_auto_hide_behavior(self):
        resp = self.client.get(f"{self._details_url(self.provider.public_id)}?phone_saved=added")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="phone-success-message"')
        self.assertContains(resp, "window.setTimeout(() => {")
        self.assertContains(resp, "phoneSuccessMessage.remove();")
        self.assertContains(resp, "}, 2000);")

    def test_phone_warning_text_is_removed(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "أكمل الإجراء الحالي أولاً")

    def test_add_phone_button_text_is_expected(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="phone-box-header"')
        self.assertContains(resp, 'id="btn-show-add-phone"')
        self.assertContains(resp, "إضافة رقم")

        html = resp.content.decode("utf-8")
        header_pos = html.find('id="phone-box-header"')
        btn_pos = html.find('id="btn-show-add-phone"')
        list_pos = html.find('id="provider-phones-wrap"')
        self.assertNotEqual(header_pos, -1)
        self.assertNotEqual(btn_pos, -1)
        self.assertNotEqual(list_pos, -1)
        self.assertLess(header_pos, btn_pos)
        self.assertLess(btn_pos, list_pos)

    def test_add_phone_number_works(self):
        resp = self.client.post(
            self._details_url(self.provider.public_id),
            {"action": "add_phone", "phone_number": "0944000001"},
        )
        self.assertEqual(resp.status_code, 302)

        self.assertTrue(ProviderPhone.objects.filter(provider=self.provider, phone_number="0944000001").exists())
        self.provider.refresh_from_db()
        self.assertEqual(self.provider.phone, "0944000001")

    def test_edit_phone_number_works(self):
        phone_row = ProviderPhone.objects.create(provider=self.provider, phone_number="0955000001")
        self.provider.phone = "0955000001"
        self.provider.save(update_fields=["phone"])

        resp = self.client.post(
            self._details_url(self.provider.public_id),
            {"action": "edit_phone", "phone_id": str(phone_row.id), "phone_number": "0955000009"},
        )
        self.assertEqual(resp.status_code, 302)

        phone_row.refresh_from_db()
        self.assertEqual(phone_row.phone_number, "0955000009")
        self.provider.refresh_from_db()
        self.assertEqual(self.provider.phone, "0955000009")

    def test_delete_phone_number_works(self):
        older = ProviderPhone.objects.create(provider=self.provider, phone_number="0966000001")
        latest = ProviderPhone.objects.create(provider=self.provider, phone_number="0966000002")
        self.provider.phone = latest.phone_number
        self.provider.save(update_fields=["phone"])

        resp = self.client.post(
            self._details_url(self.provider.public_id),
            {"action": "delete_phone", "phone_id": str(latest.id)},
        )
        self.assertEqual(resp.status_code, 302)

        self.assertFalse(ProviderPhone.objects.filter(id=latest.id).exists())
        self.provider.refresh_from_db()
        self.assertEqual(self.provider.phone, older.phone_number)

    def test_providers_list_shows_latest_added_phone_only(self):
        self.provider.phone = ""
        self.provider.save(update_fields=["phone"])

        add_provider_phone(actor=self.manager, provider=self.provider, phone_number="0977000001")
        add_provider_phone(actor=self.manager, provider=self.provider, phone_number="0977000002")

        resp = self.client.get(
            reverse("billing_api_providers_list"),
            {"basic": "1", "include_all": "1", "page_size": "30"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("ok"), body)
        row = next(item for item in body["items"] if int(item["id"]) == self.provider.id)
        self.assertEqual(row["phone"], "0977000002")
        self.assertNotEqual(row["phone"], "0977000001")

    def test_latest_activity_component_exists_with_arabic_labels(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="latest-activity-component"')
        self.assertContains(resp, "سجل النشاط الأخير")
        self.assertContains(resp, "آخر فاتورة شراء")
        self.assertContains(resp, "آخر إرجاع")
        self.assertContains(resp, "آخر عملية دين")
        self.assertContains(resp, "آخر دفعة")
        self.assertContains(resp, "آخر تحصيل")

    def test_lower_section_renders_with_required_summary_parts(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="provider-lower-overview"')
        self.assertContains(resp, 'id="provider-activity-summary-strip"')
        self.assertContains(resp, "فواتير الشراء")
        self.assertContains(resp, "فواتير الإرجاع")
        self.assertContains(resp, 'id="stat-bills-count"')
        self.assertContains(resp, 'id="stat-returns-count"')
        self.assertContains(resp, 'id="summary-latest-activity"')
        self.assertContains(resp, 'id="summary-account-state"')
        self.assertContains(resp, "آخر تعامل")
        self.assertContains(resp, "حالة الحساب")

    def test_latest_activity_has_single_display_area(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        self.assertEqual(html.count('id="latest-activity-display"'), 1)
        self.assertEqual(html.count("data-latest-template="), 5)
        self.assertGreaterEqual(html.count("data-activity-trigger="), 5)

    def test_latest_activity_selector_is_client_side_without_reload(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'button.addEventListener("click", () => {')
        self.assertContains(resp, "latestActivityDisplay.appendChild(selectedTemplate.content.cloneNode(true));")
        self.assertContains(resp, 'button.classList.toggle("active", isActive);')
        self.assertNotContains(resp, "window.location")

    def test_top_items_default_3_max_10_and_desc_order(self):
        for idx in range(1, 13):
            product = self._create_product(idx=idx, name=f"صنف-{idx:02d}")
            self._create_bill_item(product=product, qty=Decimal(str(idx)))

        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, '<option value="3" selected>3</option>', html=False)
        self.assertContains(resp, '<option value="10">10</option>', html=False)
        self.assertContains(resp, "if (limit < 3) limit = 3;")
        self.assertContains(resp, "if (limit > 10) limit = 10;")

        rows_count = resp.content.decode("utf-8").count('data-top-item-row="1"')
        self.assertEqual(rows_count, 10)
        self.assertContains(resp, 'data-rank="4" hidden')
        self.assertContains(resp, 'data-rank="3"')

        html = resp.content.decode("utf-8")
        self.assertLess(html.find("صنف-12"), html.find("صنف-11"))
        self.assertLess(html.find("صنف-11"), html.find("صنف-10"))

    def test_most_purchased_products_section_exists(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="top-items-card"')
        self.assertContains(resp, "الأصناف الأكثر شراءً")

    def test_top_items_horizontal_bar_visualization_exists(self):
        product = self._create_product(idx=1, name="صنف-أ")
        self._create_bill_item(product=product, qty=Decimal("5"))

        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "item-bar-track")
        self.assertContains(resp, "item-bar-fill")
        self.assertContains(resp, "data-top-item-bar")

    def test_notes_update_still_works(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="provider-notes"')
        self.assertContains(resp, 'id="save-notes-btn"')

        payload = {"action": "update_notes", "notes": "ملاحظة جديدة للمورد"}
        resp = self.client.post(self._details_url(self.provider.public_id), payload)
        self.assertEqual(resp.status_code, 302)

        self.provider.refresh_from_db()
        self.assertEqual(self.provider.notes, payload["notes"])

    def test_net_balance_summary_uses_provider_projection(self):
        mocked_projection = {
            "provider_id": self.provider.id,
            "currencies": {
                "SYP": {
                    "receivable": Decimal("90.00"),
                    "payable": Decimal("40.00"),
                    "net": Decimal("50.00"),
                    "open_receivable_count": 2,
                    "open_payable_count": 1,
                },
                "USD": {
                    "receivable": Decimal("10.00"),
                    "payable": Decimal("2.00"),
                    "net": Decimal("8.00"),
                    "open_receivable_count": 1,
                    "open_payable_count": 0,
                },
            },
            "diagnostics": {
                "dedup_warnings": [],
                "unresolved_identities": [],
            },
        }

        with patch("billing.selectors.get_provider_net_position", return_value=mocked_projection) as projection_mock:
            resp = self.client.get(self._details_url(self.provider.public_id))

        self.assertEqual(resp.status_code, 200)
        projection_mock.assert_called_once_with(provider_id=self.provider.id)
        self.assertContains(resp, 'id="net-syp-payable"')
        self.assertContains(resp, 'id="net-syp-receivable"')
        self.assertContains(resp, 'id="net-usd-payable"')
        self.assertContains(resp, 'id="net-usd-receivable"')
        self.assertContains(resp, 'id="net-summary-card"')

    def test_balanced_net_with_open_obligations_warning_is_visible(self):
        mocked_projection = {
            "provider_id": self.provider.id,
            "currencies": {
                "SYP": {
                    "receivable": Decimal("100.00"),
                    "payable": Decimal("100.00"),
                    "net": Decimal("0.00"),
                    "open_receivable_count": 1,
                    "open_payable_count": 1,
                },
                "USD": {
                    "receivable": Decimal("0.00"),
                    "payable": Decimal("0.00"),
                    "net": Decimal("0.00"),
                    "open_receivable_count": 0,
                    "open_payable_count": 0,
                },
            },
            "diagnostics": {
                "dedup_warnings": [],
                "unresolved_identities": [],
            },
        }

        with patch("billing.selectors.get_provider_net_position", return_value=mocked_projection):
            resp = self.client.get(self._details_url(self.provider.public_id))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="net-open-warning"')
        self.assertContains(resp, "صافي الحساب متوازن لكن توجد ديون مفتوحة")

    def test_no_settlement_actions_and_disabled_net_details_button(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="btn-net-details" type="button" class="btn primary" disabled')
        self.assertNotContains(resp, "pay_provider")
        self.assertNotContains(resp, "receive_from_provider")
        self.assertNotContains(resp, "/account-settlement")

    def test_arabic_ui_text_only(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "المعلومات العامة")
        self.assertContains(resp, "أرقام الهاتف")
        self.assertContains(resp, "سجل النشاط الأخير")
        self.assertContains(resp, "ملخص صافي الحساب")
        self.assertContains(resp, "الأصناف الأكثر شراءً")
        self.assertContains(resp, "عرض تفاصيل صافي الحساب")
        self.assertNotContains(resp, "Provider Profile")
        self.assertNotContains(resp, "Show Net Balance")
        self.assertNotContains(resp, "Latest Activity")


class ProviderPhoneMigrationBackfillTests(TransactionTestCase):
    reset_sequences = True

    migrate_from = ("billing", "0026_provider_public_id_layer")
    migrate_to = ("billing", "0027_provider_phone_numbers")

    def setUp(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])
        self.old_apps = self.executor.loader.project_state([self.migrate_from]).apps

    def tearDown(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.executor.loader.graph.leaf_nodes())

    def test_existing_provider_phone_is_backfilled(self):
        OldProvider = self.old_apps.get_model("billing", "Provider")
        old_provider = OldProvider.objects.create(name="مزود قديم", phone="0988000001")

        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])
        new_apps = self.executor.loader.project_state([self.migrate_to]).apps
        NewProvider = new_apps.get_model("billing", "Provider")
        NewProviderPhone = new_apps.get_model("billing", "ProviderPhone")

        new_provider = NewProvider.objects.get(pk=old_provider.id)
        self.assertEqual(new_provider.phone, "0988000001")
        self.assertTrue(
            NewProviderPhone.objects.filter(provider_id=new_provider.id, phone_number="0988000001").exists()
        )

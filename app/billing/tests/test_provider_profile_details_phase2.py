from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AccountProfile
from billing.models import (
    Bill,
    BillItem,
    Provider,
    ProviderPhone,
    ProviderReturn,
    ProviderReturnItem,
)
from billing.services_provider import add_provider_phone
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from debts.models import (
    DebtCauseType,
    DebtDirection,
    DebtRecord,
    DebtSettlement,
    DebtStatus,
    OtherPartyType,
)
from financials.models import MoneyContainer


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
        self.other_provider = Provider.objects.create(
            name="مورد آخر",
            phone="0999777444",
            notes="",
        )

    def _details_url(self, provider_ref: str) -> str:
        return reverse("billing_provider_details", kwargs={"provider_ref": provider_ref})

    @staticmethod
    def _provider_details_css() -> str:
        return Path("app/static/billing/css/provider_details.css").read_text(encoding="utf-8")

    @staticmethod
    def _provider_details_js() -> str:
        return Path("app/static/billing/js/provider_details.js").read_text(encoding="utf-8")

    def _create_product(self, *, idx: int, name: str) -> Product:
        collection = ProductCollection.objects.create(name=f"زمرة {idx}")
        product_set = ProductSet.objects.create(collection=collection, name=f"مجموعة {idx}")
        return Product.objects.create(
            name=name,
            set=product_set,
            unit_primary=UnitType.PIECE,
            unit_secondary="",
        )

    def _create_bill_with_items(
        self,
        *,
        provider: Provider,
        items: list[tuple[Product, Decimal]],
        total_syp: Decimal = Decimal("0.00"),
        total_usd: Decimal = Decimal("0.00"),
    ) -> Bill:
        bill = Bill.objects.create(
            provider=provider,
            total=Decimal("0.00"),
            total_syp=total_syp,
            total_usd=total_usd,
        )
        for product, qty in items:
            BillItem.objects.create(
                bill=bill,
                product=product,
                qty_primary=qty,
                cost=Decimal("1.00"),
                price=Decimal("1.00"),
                line_total=qty,
                currency="SYP",
            )
        return bill

    def _create_return_with_items(
        self,
        *,
        provider: Provider,
        items: list[tuple[Product, Decimal]],
        total_syp: Decimal = Decimal("0.00"),
        total_usd: Decimal = Decimal("0.00"),
    ) -> ProviderReturn:
        ret = ProviderReturn.objects.create(
            provider=provider,
            total=Decimal("0.00"),
            total_syp=total_syp,
            total_usd=total_usd,
        )
        for product, qty in items:
            ProviderReturnItem.objects.create(
                ret=ret,
                product=product,
                qty_primary=qty,
                cost=Decimal("1.00"),
                line_total=qty,
                currency="SYP",
            )
        return ret

    def _create_debt(
        self,
        *,
        provider: Provider,
        direction: str,
        cause_type: str,
        cause_id: str,
        total_syp: Decimal = Decimal("0.00"),
        total_usd: Decimal = Decimal("0.00"),
        remaining_syp: Decimal = Decimal("0.00"),
        remaining_usd: Decimal = Decimal("0.00"),
        status: str = DebtStatus.OPEN,
    ) -> DebtRecord:
        return DebtRecord.objects.create(
            direction=direction,
            cause_type=cause_type,
            cause_id=cause_id,
            other_party_type=OtherPartyType.PROVIDER,
            provider=provider,
            total_syp=total_syp,
            total_usd=total_usd,
            remaining_syp=remaining_syp,
            remaining_usd=remaining_usd,
            status=status,
            note="سبب الاختبار",
        )

    def _create_container(self, *, name: str = "حاوية اختبار") -> MoneyContainer:
        return MoneyContainer.objects.create(
            name=name,
            container_type=MoneyContainer.ContainerType.DRAWER,
            created_by=self.manager,
        )

    def test_provider_details_page_loads_by_public_id(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ملف المورد")

    def test_top_section_phone_and_notes_behavior_still_works(self):
        add_phone_resp = self.client.post(
            self._details_url(self.provider.public_id),
            {"action": "add_phone", "phone_number": "0944000001"},
        )
        self.assertEqual(add_phone_resp.status_code, 302)
        self.assertTrue(ProviderPhone.objects.filter(provider=self.provider, phone_number="0944000001").exists())

        notes_payload = {"action": "update_notes", "notes": "ملاحظة جديدة للمورد"}
        notes_resp = self.client.post(self._details_url(self.provider.public_id), notes_payload)
        self.assertEqual(notes_resp.status_code, 302)
        self.provider.refresh_from_db()
        self.assertEqual(self.provider.notes, notes_payload["notes"])

    def test_lower_page_has_two_main_panels_in_order(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "تحليل تعاملات المورد")
        self.assertContains(resp, "ملخص صافي حساب المورد")
        self.assertContains(resp, 'id="provider-analysis-panel"')
        self.assertContains(resp, 'id="provider-net-summary-panel"')
        html = resp.content.decode("utf-8")
        self.assertLess(html.find("تحليل تعاملات المورد"), html.find("ملخص صافي حساب المورد"))

    def test_activity_chooser_groups_and_all_options_exist(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-analysis-group="totals"')
        self.assertContains(resp, 'data-analysis-group="latest"')
        self.assertContains(resp, 'data-analysis-group="extras"')
        self.assertContains(resp, 'data-analysis-group-toggle="totals"')
        self.assertContains(resp, 'data-analysis-group-toggle="latest"')
        self.assertContains(resp, 'data-analysis-group-toggle="extras"')
        self.assertContains(resp, 'data-analysis-option="total-purchase-bills"')
        self.assertContains(resp, 'data-analysis-option="total-provider-returns"')
        self.assertContains(resp, 'data-analysis-option="total-open-debts"')
        self.assertContains(resp, 'data-analysis-option="latest-purchase"')
        self.assertContains(resp, 'data-analysis-option="latest-return"')
        self.assertContains(resp, 'data-analysis-option="latest-debt"')
        self.assertContains(resp, 'data-analysis-option="latest-payment"')
        self.assertContains(resp, 'data-analysis-option="latest-collection"')
        self.assertContains(resp, 'data-analysis-option="top-products"')
        self.assertContains(resp, 'data-analysis-option="visit-frequency"')

    def test_activity_groups_collapsed_by_default_and_no_option_selected(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        self.assertGreaterEqual(html.count('data-expanded="false"'), 3)
        self.assertGreaterEqual(html.count('aria-expanded="false"'), 3)
        self.assertGreaterEqual(html.count("analysis-group is-collapsed"), 3)
        self.assertNotIn("analysis-group is-expanded", html)
        self.assertNotIn("analysis-option-btn active", html)
        self.assertNotContains(resp, 'aria-pressed="true"')
        self.assertContains(resp, 'id="analysis-group-options-totals" hidden')
        self.assertContains(resp, 'id="analysis-group-options-latest" hidden')
        self.assertContains(resp, 'id="analysis-group-options-extras" hidden')

    def test_details_and_visual_boxes_exist(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="analysis-details-box"')
        self.assertContains(resp, 'id="analysis-visual-box"')
        self.assertContains(resp, 'id="analysis-details-loading"')
        self.assertContains(resp, 'id="analysis-visual-loading"')
        self.assertContains(resp, "اختر بنداً من القائمة لعرض التفاصيل")
        self.assertContains(resp, "اختر بنداً من القائمة لعرض الرسم أو التحليل")

    def test_accordion_single_expand_behavior_is_wired_in_js(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "/static/billing/js/provider_details.js")
        js = self._provider_details_js()
        self.assertIn("function collapseAllGroups(expandKey = \"\")", js)
        self.assertIn("setGroupExpanded(groupNode, isExpanded);", js)
        self.assertIn("const alreadyExpanded = groupNode.getAttribute(\"data-expanded\") === \"true\";", js)
        self.assertIn("collapseAllGroups(groupKey);", js)
        self.assertIn("collapseAllGroups(\"\");", js)
        self.assertIn("analysisChooserList.addEventListener(\"click\", (event) => {", js)

    def test_accordion_header_layout_places_arrow_opposite_label(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "/static/billing/css/provider_details.css")
        self.assertContains(resp, 'class="analysis-group-chevron"')
        css = self._provider_details_css()
        self.assertIn("justify-content:space-between;", css)
        html = resp.content.decode("utf-8")
        self.assertRegex(
            html,
            re.compile(
                r'class="analysis-group-toggle"[\s\S]*?class="analysis-group-title"[\s\S]*?class="analysis-group-chevron"'
            ),
        )

    def test_expanded_group_header_highlight_and_option_active_style_are_separate(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        css = self._provider_details_css()
        self.assertIn(".analysis-group.is-expanded .analysis-group-toggle", css)
        self.assertIn(".analysis-group.is-expanded .analysis-group-title", css)
        self.assertIn(".analysis-option-btn.active", css)

    def test_totals_include_provider_vs_others_percentages(self):
        p1 = self._create_product(idx=1, name="منتج-1")
        p2 = self._create_product(idx=2, name="منتج-2")
        self._create_bill_with_items(provider=self.provider, items=[(p1, Decimal("3"))])
        self._create_bill_with_items(provider=self.other_provider, items=[(p2, Decimal("2"))])

        self._create_return_with_items(provider=self.provider, items=[(p1, Decimal("1"))])
        self._create_return_with_items(provider=self.other_provider, items=[(p2, Decimal("1"))])

        self._create_debt(
            provider=self.provider,
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="prov-open-1",
            total_syp=Decimal("50"),
            remaining_syp=Decimal("50"),
            status=DebtStatus.OPEN,
        )
        self._create_debt(
            provider=self.other_provider,
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.MANUAL,
            cause_id="prov-open-2",
            total_syp=Decimal("80"),
            remaining_syp=Decimal("80"),
            status=DebtStatus.OPEN,
        )

        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        details = resp.context["details"]
        totals = details["totals_share"]

        self.assertIn("provider_percent", totals["purchase_bills"])
        self.assertIn("others_percent", totals["purchase_bills"])
        self.assertIn("provider_percent", totals["provider_returns"])
        self.assertIn("others_percent", totals["provider_returns"])
        self.assertIn("provider_percent", totals["open_debts"])
        self.assertIn("others_percent", totals["open_debts"])
    def test_latest_activities_have_details_payload_and_no_chart_visual(self):
        product = self._create_product(idx=10, name="منتج-أ")
        bill = self._create_bill_with_items(
            provider=self.provider,
            items=[(product, Decimal("5"))],
            total_syp=Decimal("120"),
            total_usd=Decimal("0"),
        )
        ret = self._create_return_with_items(
            provider=self.provider,
            items=[(product, Decimal("2"))],
            total_syp=Decimal("30"),
            total_usd=Decimal("0"),
        )
        debt = self._create_debt(
            provider=self.provider,
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=bill.public_id,
            total_syp=Decimal("120"),
            remaining_syp=Decimal("70"),
            status=DebtStatus.OPEN,
        )
        container = self._create_container()
        DebtSettlement.objects.create(
            debt=debt,
            payment_syp=Decimal("20"),
            payment_usd=Decimal("0"),
            applied_syp=Decimal("20"),
            applied_usd=Decimal("0"),
            money_container=container,
        )
        receivable_debt = self._create_debt(
            provider=self.provider,
            direction=DebtDirection.RECEIVABLE,
            cause_type=DebtCauseType.PROVIDER_RETURN,
            cause_id=ret.public_id,
            total_syp=Decimal("30"),
            remaining_syp=Decimal("10"),
            status=DebtStatus.OPEN,
        )
        DebtSettlement.objects.create(
            debt=receivable_debt,
            payment_syp=Decimal("10"),
            payment_usd=Decimal("0"),
            applied_syp=Decimal("10"),
            applied_usd=Decimal("0"),
            money_container=container,
        )

        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        details = resp.context["details"]
        activities = details["analysis_panel"]["activities"]

        for key in ["latest-purchase", "latest-return", "latest-debt", "latest-payment", "latest-collection"]:
            self.assertEqual(activities[key]["visual_kind"], "none")
            self.assertIsNotNone(activities[key]["details"])

        self.assertNotIn("products_preview", activities["latest-purchase"]["details"])
        self.assertNotIn("products_preview", activities["latest-return"]["details"])
        js = self._provider_details_js()
        self.assertIn("هوية العملية", js)
        self.assertIn("الملخص", js)
        self.assertIn("الإجماليات", js)
        self.assertIn("رقم الفاتورة", js)
        self.assertIn("رقم الإرجاع", js)
        self.assertIn("التاريخ", js)
        self.assertIn("عدد المنتجات", js)
        self.assertIn("الحالة الحالية", js)
        self.assertIn("إجمالي SYP", js)
        self.assertIn("إجمالي USD", js)
        self.assertIn("عرض العملية", js)
        self.assertNotIn("الدفع عند الإنشاء", js)
        self.assertNotIn("حالة الدفع عند الإنشاء", js)
        self.assertNotIn("نوع الدفع", js)
        self.assertNotIn('analysis-detail-group-title">الدفع عند الإنشاء', js)
        self.assertIn("لا يوجد رسم بياني لهذا العنصر", js)

    def test_top_products_activity_returns_top_5_and_others_segment(self):
        products = []
        for idx in range(1, 7):
            products.append(self._create_product(idx=100 + idx, name=f"صنف-{idx:02d}"))

        bill = Bill.objects.create(provider=self.provider, total=Decimal("0"), total_syp=Decimal("0"), total_usd=Decimal("0"))
        quantities = [Decimal("12"), Decimal("11"), Decimal("10"), Decimal("9"), Decimal("8"), Decimal("7")]
        for product, qty in zip(products, quantities):
            BillItem.objects.create(
                bill=bill,
                product=product,
                qty_primary=qty,
                cost=Decimal("1.00"),
                price=Decimal("1.00"),
                line_total=qty,
                currency="SYP",
            )

        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        activities = resp.context["details"]["analysis_panel"]["activities"]
        top_products = activities["top-products"]["details"]["items"]
        self.assertEqual(len(top_products), 5)
        self.assertGreater(float(top_products[0]["total_qty"]), float(top_products[-1]["total_qty"]))

        segments = activities["top-products"]["visual"]["segments"]
        self.assertEqual(len(segments), 6)
        others_segment = segments[-1]
        self.assertEqual(others_segment["color"], "#9ca3af")

    def test_visit_frequency_contains_days_times_and_toggle_controls(self):
        product = self._create_product(idx=220, name="صنف-الزيارات")
        bill = self._create_bill_with_items(provider=self.provider, items=[(product, Decimal("1"))])
        Bill.objects.filter(id=bill.id).update(created_at=timezone.make_aware(datetime(2026, 5, 26, 9, 30)))

        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        details = resp.context["details"]
        visits = details["analysis_panel"]["activities"]["visit-frequency"]
        self.assertEqual(visits["visual_kind"], "bars-toggle")
        self.assertTrue(visits["visual"]["days"])
        self.assertTrue(visits["visual"]["times"])
        js = self._provider_details_js()
        self.assertIn("الأيام", js)
        self.assertIn("الأوقات", js)

    def test_net_balance_panel_is_below_analysis_and_button_disabled(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        self.assertLess(html.find('id="provider-analysis-panel"'), html.find('id="provider-net-summary-panel"'))
        self.assertContains(resp, 'id="btn-net-details" type="button" class="btn primary" disabled')

    def test_no_settlement_pay_receive_actions_exist(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "pay_provider")
        self.assertNotContains(resp, "receive_from_provider")
        self.assertNotContains(resp, "/account-settlement")

    def test_arabic_ui_text_only(self):
        resp = self.client.get(self._details_url(self.provider.public_id))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "المعلومات العامة")
        self.assertContains(resp, "أرقام الهاتف")
        self.assertContains(resp, "الملاحظات")
        self.assertContains(resp, "تحليل تعاملات المورد")
        self.assertContains(resp, "ملخص صافي حساب المورد")
        self.assertContains(resp, "عرض تفاصيل صافي الحساب")
        self.assertNotContains(resp, "Provider Profile")
        self.assertNotContains(resp, "Show Net Balance")
        self.assertNotContains(resp, "Latest Activity")

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


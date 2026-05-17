from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import AccountProfile
from billing import selectors as BillingSelectors
from billing import services as BillingSV
from billing.models import Bill, Provider
from billing.serializers import provider_row
from catalog.models import Product, ProductCollection, ProductSet, UnitType
from debts import services as DebtSV
from debts.models import (
    DebtCauseType,
    DebtDirection,
    DebtRecord,
    DebtStatus,
    DebtorDebt,
    OtherPartyType,
)
from debts.provider_account_allocator import simulate_provider_account_allocation
from debts.provider_account_settlement_execution import execute_provider_account_settlement
from debts.provider_position import collect_provider_open_obligations, get_provider_net_position
from financials import services as FinSV
from financials.models import ContainerFeature, Currency, MoneyContainer, MoneyContainerCurrency
from stock.models import ProductContainer


DEC0 = Decimal("0.00")


class ProviderNetBalanceLifecycleInvariantM3Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username="m3_lifecycle_mgr",
            password="pw12345",
        )
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)

        cls.container = MoneyContainer.objects.create(
            name="M3 Lifecycle Cash",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=cls.manager,
        )
        cls.container.allowed_users.add(cls.manager)

        for code in ("purchase_bills", "provider_returns", "provider_account_settlement"):
            feature, _ = ContainerFeature.objects.get_or_create(
                code=code,
                defaults={"name": code.replace("_", " ").title(), "is_active": True},
            )
            if not feature.is_active:
                feature.is_active = True
                feature.save(update_fields=["is_active"])
            cls.container.features.add(feature)

        cls.syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 2, "is_active": True},
        )
        cls.usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.container,
            currency=cls.syp,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=cls.container,
            currency=cls.usd,
            defaults={"is_enabled": True},
        )

        cls.store, _ = ProductContainer.objects.get_or_create(
            code="store",
            defaults={"name": "Store", "is_store": True, "is_active": True},
        )
        if not cls.store.is_active:
            cls.store.is_active = True
            cls.store.save(update_fields=["is_active"])

        FinSV.set_current_fx(actor=cls.manager, rate_syp_per_usd=Decimal("15000"))

    def setUp(self):
        self._serial_seq = 991000

    def _next_serial(self) -> int:
        self._serial_seq += 1
        return self._serial_seq

    def _create_product(self, *, name: str) -> Product:
        base = str(name or "M3 Product")[:40]
        col = ProductCollection.objects.create(name=f"{base} Col")
        pset = ProductSet.objects.create(collection=col, name=f"{base} Set")
        return Product.objects.create(
            name=base,
            set=pset,
            unit_primary=UnitType.PIECE,
            allow_syp_purchasing=True,
            allow_usd_purchasing=True,
            allow_syp_sales=True,
            allow_usd_sales=True,
            default_cost_syp=Decimal("1000"),
            default_cost_usd=Decimal("5"),
            default_price_syp=Decimal("1200"),
            default_price_usd=Decimal("6"),
        )

    def _create_bill(
        self,
        *,
        provider: Provider,
        product: Product,
        qty: str = "1",
        cost: str = "1000",
        currency: str = "SYP",
        status: str = "unpaid",
        paid_amount: Decimal = DEC0,
    ) -> Bill:
        return BillingSV.create_bill(
            actor=self.manager,
            provider_id=provider.id,
            status=status,
            paid_amount=paid_amount,
            items=[
                {
                    "product_id": product.id,
                    "unit_index": 1,
                    "qty_raw": qty,
                    "cost": cost,
                    "currency": currency,
                }
            ],
            container=self.store,
            money_container_id=self.container.id,
            settlement_currency=currency,
            fx_usd_syp=Decimal("15000"),
        )

    def _create_central_manual_debt(
        self,
        *,
        provider: Provider,
        direction: str,
        cause_id: str,
        remaining_syp: str = "0.00",
        remaining_usd: str = "0.00",
    ) -> DebtRecord:
        return DebtRecord.objects.create(
            direction=direction,
            cause_type=DebtCauseType.MANUAL,
            cause_id=cause_id,
            source_app="debts",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(provider.id),
            provider=provider,
            actor_username="m3",
            total_syp=Decimal(remaining_syp),
            total_usd=Decimal(remaining_usd),
            remaining_syp=Decimal(remaining_syp),
            remaining_usd=Decimal(remaining_usd),
            status=DebtStatus.OPEN,
        )

    def _collector_rollup(self, *, obligations: list[dict]) -> dict[str, dict[str, Decimal | int]]:
        out = {
            "SYP": {
                "receivable": DEC0,
                "payable": DEC0,
                "open_receivable_count": 0,
                "open_payable_count": 0,
            },
            "USD": {
                "receivable": DEC0,
                "payable": DEC0,
                "open_receivable_count": 0,
                "open_payable_count": 0,
            },
        }
        for row in obligations:
            direction = str(row.get("direction") or "")
            rem_syp = Decimal(row.get("remaining_syp") or DEC0)
            rem_usd = Decimal(row.get("remaining_usd") or DEC0)

            if rem_syp > DEC0:
                bucket = out["SYP"]
                if direction == DebtDirection.RECEIVABLE:
                    bucket["receivable"] = Decimal(bucket["receivable"]) + rem_syp
                    bucket["open_receivable_count"] = int(bucket["open_receivable_count"]) + 1
                elif direction == DebtDirection.PAYABLE:
                    bucket["payable"] = Decimal(bucket["payable"]) + rem_syp
                    bucket["open_payable_count"] = int(bucket["open_payable_count"]) + 1

            if rem_usd > DEC0:
                bucket = out["USD"]
                if direction == DebtDirection.RECEIVABLE:
                    bucket["receivable"] = Decimal(bucket["receivable"]) + rem_usd
                    bucket["open_receivable_count"] = int(bucket["open_receivable_count"]) + 1
                elif direction == DebtDirection.PAYABLE:
                    bucket["payable"] = Decimal(bucket["payable"]) + rem_usd
                    bucket["open_payable_count"] = int(bucket["open_payable_count"]) + 1
        return out

    def _collector_open_payable_rows_count(self, *, obligations: list[dict]) -> int:
        out = 0
        for row in obligations:
            if str(row.get("direction") or "") != DebtDirection.PAYABLE:
                continue
            rem_syp = Decimal(row.get("remaining_syp") or DEC0)
            rem_usd = Decimal(row.get("remaining_usd") or DEC0)
            if rem_syp <= DEC0 and rem_usd <= DEC0:
                continue
            out += 1
        return out

    def _provider_row(self, *, provider_id: int) -> dict:
        items = list(BillingSelectors.providers_with_stats("", True, None, 500))
        try:
            provider_obj = next(row for row in items if int(row.id) == int(provider_id))
        except StopIteration:
            self.fail(f"provider {provider_id} missing from providers_with_stats include_all=true")
        return provider_row(provider_obj)

    def _assert_projection_collector_provider_row_consistency(self, *, provider: Provider) -> dict:
        projection = get_provider_net_position(provider_id=provider.id)
        collected = collect_provider_open_obligations(provider_id=provider.id)
        obligations = list(collected.get("obligations") or [])
        rollup = self._collector_rollup(obligations=obligations)

        for code in ("SYP", "USD"):
            bucket = projection["currencies"][code]
            self.assertEqual(bucket["receivable"], Decimal(rollup[code]["receivable"]))
            self.assertEqual(bucket["payable"], Decimal(rollup[code]["payable"]))
            self.assertEqual(bucket["net"], bucket["receivable"] - bucket["payable"])
            self.assertEqual(bucket["open_receivable_count"], int(rollup[code]["open_receivable_count"]))
            self.assertEqual(bucket["open_payable_count"], int(rollup[code]["open_payable_count"]))

        row = self._provider_row(provider_id=provider.id)
        self.assertEqual(Decimal(row["debt_totals"]["SYP"]), projection["currencies"]["SYP"]["payable"])
        self.assertEqual(Decimal(row["debt_totals"]["USD"]), projection["currencies"]["USD"]["payable"])
        self.assertEqual(Decimal(row["total_debt"]), projection["currencies"]["SYP"]["payable"])
        self.assertEqual(
            int(row["unpaid_bills_count"]),
            self._collector_open_payable_rows_count(obligations=obligations),
        )
        return projection

    def test_purchase_bill_lifecycle_invariant(self):
        provider = Provider.objects.create(name=f"M3 Purchase Lifecycle {self._testMethodName}")
        product = self._create_product(name=f"M3 Purchase Product {self._testMethodName}")

        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["payable"], DEC0)

        bill = self._create_bill(provider=provider, product=product, qty="1", cost="1000", currency="SYP")
        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["payable"], Decimal("1000.00"))
        self.assertEqual(projection["currencies"]["SYP"]["net"], Decimal("-1000.00"))

        BillingSV.pay_partial(
            actor=self.manager,
            bill_id=bill.id,
            amount=Decimal("300"),
            money_container_id=self.container.id,
            currency_code="SYP",
        )
        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["payable"], Decimal("700.00"))
        self.assertEqual(projection["currencies"]["SYP"]["net"], Decimal("-700.00"))

        BillingSV.pay_full(
            actor=self.manager,
            bill_id=bill.id,
            money_container_id=self.container.id,
            currency_code="SYP",
        )
        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["payable"], DEC0)
        self.assertEqual(projection["currencies"]["SYP"]["open_payable_count"], 0)

        BillingSV.delete_bill(actor=self.manager, bill_id=bill.id)
        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["payable"], DEC0)
        self.assertFalse(
            DebtRecord.objects.filter(
                direction=DebtDirection.PAYABLE,
                cause_type=DebtCauseType.PURCHASE_BILL,
                cause_id=bill.public_id,
            ).exists()
        )

    def test_provider_return_lifecycle_invariant(self):
        provider = Provider.objects.create(name=f"M3 Return Lifecycle {self._testMethodName}")
        product = self._create_product(name=f"M3 Return Product {self._testMethodName}")
        bill = self._create_bill(provider=provider, product=product, qty="2", cost="1000", currency="SYP")
        item = bill.items.get()

        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["payable"], Decimal("2000.00"))

        ret = BillingSV.create_return(
            actor=self.manager,
            provider_id=provider.id,
            status="unpaid",
            paid_amount=Decimal("0"),
            items=[
                {
                    "bill_item_id": item.id,
                    "product_id": item.product_id,
                    "unit_index": 1,
                    "qty_primary": "1",
                    "container_splits": [{"code": "store", "qty_primary": "1"}],
                }
            ],
            container=None,
            source_bill_serial=bill.serial,
            money_container_id=None,
            currency_code="SYP",
            valuation_mode="HISTORICAL",
        )
        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["receivable"], Decimal("1000.00"))
        self.assertEqual(projection["currencies"]["SYP"]["net"], Decimal("-1000.00"))

        BillingSV.collect_partial(
            actor=self.manager,
            return_id=ret.id,
            amount=Decimal("250"),
            money_container_id=self.container.id,
            currency_code="SYP",
        )
        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["receivable"], Decimal("750.00"))

        BillingSV.collect_full(
            actor=self.manager,
            return_id=ret.id,
            money_container_id=self.container.id,
            currency_code="SYP",
        )
        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["receivable"], DEC0)

        BillingSV.delete_return(actor=self.manager, return_id=ret.id)
        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["receivable"], DEC0)

    def test_manual_debt_lifecycle_invariant_with_central_legacy_sync(self):
        provider = Provider.objects.create(name=f"M3 Manual Lifecycle {self._testMethodName}")

        debtor_entry = DebtSV.create_manual_debt(
            actor=self.manager,
            direction="debtor",
            party_type="provider",
            provider_id=provider.id,
            party_name=provider.name,
            amount=Decimal("1000"),
            currency_code="SYP",
        )
        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["payable"], Decimal("1000.00"))
        self.assertEqual(projection["currencies"]["SYP"]["open_payable_count"], 1)
        self.assertTrue(
            DebtRecord.objects.filter(
                direction=DebtDirection.PAYABLE,
                cause_type=DebtCauseType.MANUAL,
                cause_id=str(debtor_entry.id),
            ).exists()
        )

        DebtSV.pay_debt(
            actor=self.manager,
            entry_id=debtor_entry.id,
            amount=Decimal("250"),
            full=False,
            money_container_id=self.container.id,
            currency_code="SYP",
        )
        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["payable"], Decimal("750.00"))

        DebtSV.pay_debt(
            actor=self.manager,
            entry_id=debtor_entry.id,
            full=True,
            money_container_id=self.container.id,
            currency_code="SYP",
        )
        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["payable"], DEC0)

        creditor_entry = DebtSV.create_manual_debt(
            actor=self.manager,
            direction="creditor",
            party_type="provider",
            provider_id=provider.id,
            party_name=provider.name,
            amount=Decimal("600"),
            currency_code="SYP",
        )
        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["receivable"], Decimal("600.00"))
        self.assertEqual(projection["currencies"]["SYP"]["open_receivable_count"], 1)
        self.assertTrue(
            DebtRecord.objects.filter(
                direction=DebtDirection.RECEIVABLE,
                cause_type=DebtCauseType.MANUAL,
                cause_id=str(creditor_entry.id),
            ).exists()
        )

        DebtSV.collect_debt(
            actor=self.manager,
            entry_id=creditor_entry.id,
            amount=Decimal("100"),
            full=False,
            money_container_id=self.container.id,
            currency_code="SYP",
        )
        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["receivable"], Decimal("500.00"))

        DebtSV.collect_debt(
            actor=self.manager,
            entry_id=creditor_entry.id,
            full=True,
            money_container_id=self.container.id,
            currency_code="SYP",
        )
        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["receivable"], DEC0)

    def test_individual_central_debt_settlement_mutates_only_target_obligation(self):
        provider = Provider.objects.create(name=f"M3 Individual Settle {self._testMethodName}")
        d1 = self._create_central_manual_debt(
            provider=provider,
            direction=DebtDirection.PAYABLE,
            cause_id="m3-individual-1",
            remaining_syp="500.00",
        )
        d2 = self._create_central_manual_debt(
            provider=provider,
            direction=DebtDirection.PAYABLE,
            cause_id="m3-individual-2",
            remaining_syp="700.00",
        )
        before_d2_remaining = d2.remaining_syp

        DebtSV.settle_central_debt(
            actor=self.manager,
            debt_id=d1.id,
            amount=Decimal("200"),
            full=False,
            money_container_id=self.container.id,
            currency_code="SYP",
        )

        d1.refresh_from_db()
        d2.refresh_from_db()
        self.assertEqual(d1.remaining_syp, Decimal("300.00"))
        self.assertEqual(d2.remaining_syp, before_d2_remaining)

        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["payable"], Decimal("1000.00"))

    def test_account_settlement_lifecycle_preview_execute_replay_delta_invariant(self):
        provider = Provider.objects.create(name=f"M3 Account Settle {self._testMethodName}")
        self._create_central_manual_debt(
            provider=provider,
            direction=DebtDirection.PAYABLE,
            cause_id="m3-acc-1",
            remaining_syp="2000.00",
        )
        self._create_central_manual_debt(
            provider=provider,
            direction=DebtDirection.PAYABLE,
            cause_id="m3-acc-2",
            remaining_syp="3000.00",
        )

        before = self._assert_projection_collector_provider_row_consistency(provider=provider)
        preview = simulate_provider_account_allocation(
            provider_id=provider.id,
            action="pay_provider",
            currency="SYP",
            amount="2500.00",
        )
        self.assertEqual(preview["total_applied"], Decimal("2500.00"))

        idem = f"{self._testMethodName}:idem"
        result = execute_provider_account_settlement(
            provider_id=provider.id,
            action="pay_provider",
            currency="SYP",
            amount="2500.00",
            money_container_id=self.container.id,
            idempotency_key=idem,
            user=self.manager,
        )
        self.assertEqual(result["total_applied"], Decimal("2500.00"))
        after = self._assert_projection_collector_provider_row_consistency(provider=provider)

        self.assertEqual(
            before["currencies"]["SYP"]["payable"] - after["currencies"]["SYP"]["payable"],
            result["total_applied"],
        )
        self.assertEqual(
            before["currencies"]["SYP"]["receivable"],
            after["currencies"]["SYP"]["receivable"],
        )

        replay = execute_provider_account_settlement(
            provider_id=provider.id,
            action="pay_provider",
            currency="SYP",
            amount="2500.00",
            money_container_id=self.container.id,
            idempotency_key=idem,
            user=self.manager,
        )
        self.assertEqual(replay["action_id"], result["action_id"])
        self.assertEqual(replay["receipt_id"], result["receipt_id"])
        after_replay = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(after_replay["currencies"]["SYP"], after["currencies"]["SYP"])
        self.assertEqual(after_replay["currencies"]["USD"], after["currencies"]["USD"])

    def test_coexistence_duplicate_obligation_counted_once_invariant(self):
        provider = Provider.objects.create(name=f"M3 Coexistence {self._testMethodName}")
        bill = Bill.objects.create(serial=self._next_serial(), provider=provider)
        self._create_central_manual_debt(
            provider=provider,
            direction=DebtDirection.PAYABLE,
            cause_id="m3-unrelated-pay-usd",
            remaining_usd="2.00",
        )
        DebtRecord.objects.create(
            direction=DebtDirection.PAYABLE,
            cause_type=DebtCauseType.PURCHASE_BILL,
            cause_id=bill.public_id,
            source_app="billing",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(provider.id),
            provider=provider,
            actor_username="m3",
            total_syp=Decimal("400.00"),
            remaining_syp=Decimal("400.00"),
            total_usd=DEC0,
            remaining_usd=DEC0,
            status=DebtStatus.OPEN,
        )
        DebtorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="Bill",
            source_id=str(bill.id),
            total=Decimal("400.00"),
            paid_amount=DEC0,
            status=DebtorDebt.Status.OPEN,
            party_type="provider",
            party_name=provider.name,
            currency_code="SYP",
        )
        DebtorDebt.objects.create(
            provider=provider,
            source_app="billing",
            source_model="Bill",
            source_id="m3-legacy-only",
            total=Decimal("100.00"),
            paid_amount=DEC0,
            status=DebtorDebt.Status.OPEN,
            party_type="provider",
            party_name=provider.name,
            currency_code="SYP",
        )

        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["payable"], Decimal("500.00"))
        self.assertEqual(projection["currencies"]["USD"]["payable"], Decimal("2.00"))

    def test_net_zero_allows_open_opposite_direction_obligations_invariant(self):
        provider = Provider.objects.create(name=f"M3 Net Zero {self._testMethodName}")
        self._create_central_manual_debt(
            provider=provider,
            direction=DebtDirection.PAYABLE,
            cause_id="m3-net-zero-pay",
            remaining_syp="1000.00",
        )
        self._create_central_manual_debt(
            provider=provider,
            direction=DebtDirection.RECEIVABLE,
            cause_id="m3-net-zero-rec",
            remaining_syp="1000.00",
        )

        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["net"], DEC0)
        self.assertGreater(projection["currencies"]["SYP"]["open_payable_count"], 0)
        self.assertGreater(projection["currencies"]["SYP"]["open_receivable_count"], 0)

    def test_currency_isolation_invariant_for_account_settlement(self):
        provider = Provider.objects.create(name=f"M3 Currency Isolation {self._testMethodName}")
        debt = self._create_central_manual_debt(
            provider=provider,
            direction=DebtDirection.PAYABLE,
            cause_id="m3-cur-iso",
            remaining_syp="1000.00",
            remaining_usd="10.00",
        )
        before = self._assert_projection_collector_provider_row_consistency(provider=provider)

        execute_provider_account_settlement(
            provider_id=provider.id,
            action="pay_provider",
            currency="SYP",
            amount="400.00",
            money_container_id=self.container.id,
            idempotency_key=f"{self._testMethodName}:syp",
            user=self.manager,
        )
        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, Decimal("600.00"))
        self.assertEqual(debt.remaining_usd, Decimal("10.00"))

        after = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(
            before["currencies"]["USD"]["payable"],
            after["currencies"]["USD"]["payable"],
        )

    def test_no_auto_cancel_directional_action_leaves_opposite_side_untouched(self):
        provider = Provider.objects.create(name=f"M3 No Auto Cancel {self._testMethodName}")
        pay = self._create_central_manual_debt(
            provider=provider,
            direction=DebtDirection.PAYABLE,
            cause_id="m3-no-cancel-pay",
            remaining_syp="800.00",
        )
        rec = self._create_central_manual_debt(
            provider=provider,
            direction=DebtDirection.RECEIVABLE,
            cause_id="m3-no-cancel-rec",
            remaining_syp="500.00",
        )

        execute_provider_account_settlement(
            provider_id=provider.id,
            action="pay_provider",
            currency="SYP",
            amount="300.00",
            money_container_id=self.container.id,
            idempotency_key=f"{self._testMethodName}:pay",
            user=self.manager,
        )
        pay.refresh_from_db()
        rec.refresh_from_db()
        self.assertEqual(pay.remaining_syp, Decimal("500.00"))
        self.assertEqual(rec.remaining_syp, Decimal("500.00"))

        projection = self._assert_projection_collector_provider_row_consistency(provider=provider)
        self.assertEqual(projection["currencies"]["SYP"]["payable"], Decimal("500.00"))
        self.assertEqual(projection["currencies"]["SYP"]["receivable"], Decimal("500.00"))
        self.assertEqual(projection["currencies"]["SYP"]["net"], DEC0)

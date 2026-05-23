from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from accounts.models import AccountProfile
from billing.models import Provider
from debts.models import (
    DebtCauseType,
    DebtDirection,
    DebtRecord,
    DebtSettlement,
    DebtStatus,
    OtherPartyType,
    ProviderSettlementAction,
    ProviderSettlementAllocation,
)
from debts.provider_account_settlement_execution import execute_provider_account_settlement
from financials import services as FinSV
from financials.models import (
    ContainerFeature,
    Currency,
    MoneyContainer,
    MoneyContainerCurrency,
    Receipt,
)


class ProviderAccountSettlementExecuteApiPhase6ATests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.manager = user_model.objects.create_user(
            username="provider_settle_exec_api_mgr",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.manager, role=AccountProfile.Role.MANAGER)

        cls.cashier = user_model.objects.create_user(
            username="provider_settle_exec_api_cashier",
            password="123456",
        )
        AccountProfile.objects.create(user=cls.cashier, role=AccountProfile.Role.CASHIER)

    def setUp(self):
        self.client.force_login(self.manager)
        self.provider = Provider.objects.create(name=f"Phase6A Provider {self._testMethodName}")
        self.container = MoneyContainer.objects.create(
            name=f"Phase6A Drawer {self._testMethodName}",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=self.manager,
        )
        self.container.allowed_users.add(self.manager)
        feature, _ = ContainerFeature.objects.get_or_create(
            code="provider_account_settlement",
            defaults={"name": "Provider Account Settlement", "is_active": True},
        )
        self.container.features.add(feature)

        self.hidden_container = MoneyContainer.objects.create(
            name=f"Phase6A Hidden Drawer {self._testMethodName}",
            container_type=MoneyContainer.ContainerType.DRAWER,
            is_active=True,
            created_by=self.manager,
        )
        self.hidden_container.features.add(feature)

        syp, _ = Currency.objects.get_or_create(
            code="SYP",
            defaults={"name": "Syrian Pound", "decimals": 2, "is_active": True},
        )
        usd, _ = Currency.objects.get_or_create(
            code="USD",
            defaults={"name": "US Dollar", "decimals": 2, "is_active": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=self.container,
            currency=syp,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=self.container,
            currency=usd,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=self.hidden_container,
            currency=syp,
            defaults={"is_enabled": True},
        )
        MoneyContainerCurrency.objects.update_or_create(
            container=self.hidden_container,
            currency=usd,
            defaults={"is_enabled": True},
        )
        FinSV.set_current_fx(actor=self.manager, rate_syp_per_usd=Decimal("20000"))

    def _path(self, provider_ref: str) -> str:
        return f"/manager/debts/api/provider/{provider_ref}/account-settlement-execute/"

    def _post(self, provider_ref: str, payload: dict):
        return self.client.post(
            self._path(provider_ref),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def _serialize_decimals(self, value):
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, dict):
            return {k: self._serialize_decimals(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._serialize_decimals(v) for v in value]
        return value

    def _create_central_debt(
        self,
        *,
        direction: str,
        remaining_syp: str = "0.00",
        remaining_usd: str = "0.00",
    ) -> DebtRecord:
        return DebtRecord.objects.create(
            direction=direction,
            cause_type=DebtCauseType.MANUAL,
            cause_id=f"phase6a-{self._testMethodName}-{direction}",
            source_app="debts",
            other_party_type=OtherPartyType.PROVIDER,
            other_party_id=str(self.provider.id),
            provider=self.provider,
            actor_username="phase6a",
            total_syp=Decimal(remaining_syp),
            total_usd=Decimal(remaining_usd),
            remaining_syp=Decimal(remaining_syp),
            remaining_usd=Decimal(remaining_usd),
            status=DebtStatus.OPEN,
        )

    def _valid_payload(
        self,
        *,
        idempotency_key: str = "phase6a:k1",
        amount: str = "100.00",
        action: str = "pay_provider",
        currency: str = "SYP",
        preview_fingerprint: str | None = None,
        money_container_id: int | None = None,
    ) -> dict:
        payload = {
            "action": action,
            "currency": currency,
            "amount": amount,
            "money_container_id": money_container_id or self.container.id,
            "idempotency_key": idempotency_key,
        }
        if preview_fingerprint is not None:
            payload["preview_fingerprint"] = preview_fingerprint
        return payload

    def test_execution_api_is_disabled_by_default_without_override(self):
        response = self._post(str(self.provider.id), self._valid_payload())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            {"ok": False, "error": "provider account settlement execution is disabled"},
        )

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=False)
    def test_feature_flag_disabled_blocks_execution(self):
        debt = self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="500.00")
        before_counts = (
            ProviderSettlementAction.objects.count(),
            ProviderSettlementAllocation.objects.count(),
            Receipt.objects.count(),
            DebtSettlement.objects.count(),
        )

        resp = self._post(str(self.provider.id), self._valid_payload())
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            resp.json(),
            {"ok": False, "error": "provider account settlement execution is disabled"},
        )

        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, Decimal("500.00"))
        after_counts = (
            ProviderSettlementAction.objects.count(),
            ProviderSettlementAllocation.objects.count(),
            Receipt.objects.count(),
            DebtSettlement.objects.count(),
        )
        self.assertEqual(before_counts, after_counts)

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_enabled_flag_allows_execution(self):
        self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="500.00")
        resp = self._post(str(self.provider.id), self._valid_payload(amount="100.00"))
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("action", data)
        self.assertIn("allocations", data)
        self.assertIn("receipt", data)
        self.assertIn("diagnostics", data)
        self.assertEqual(data["action"]["currency"], "SYP")
        self.assertEqual(data["action"]["total_applied"], "100.00")

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_execution_api_accepts_provider_public_id_ref(self):
        self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="500.00")
        resp = self._post(self.provider.public_id, self._valid_payload(amount="100.00"))
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["action"]["provider_id"], self.provider.id)

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_manager_auth_required(self):
        self.client.logout()
        resp = self._post(str(self.provider.id), self._valid_payload())
        self.assertEqual(resp.status_code, 302)

        self.client.force_login(self.cashier)
        resp = self._post(str(self.provider.id), self._valid_payload())
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"ok": False, "error": "FORBIDDEN"})

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_successful_execution_response_shape(self):
        self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="1000.00")
        resp = self._post(str(self.provider.id), self._valid_payload(amount="300.00"))
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data["ok"])

        for key in ("action", "allocations", "receipt", "diagnostics"):
            self.assertIn(key, data)
        for key in (
            "id",
            "provider_id",
            "action",
            "currency",
            "requested_amount",
            "eligible_total_remaining",
            "total_applied",
            "allocation_count",
            "idempotency_key",
            "preview_fingerprint",
        ):
            self.assertIn(key, data["action"])
        for key in ("id", "serial", "status"):
            self.assertIn(key, data["receipt"])

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_idempotent_replay_through_api(self):
        debt = self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="500.00")
        payload = self._valid_payload(idempotency_key="phase6a:idem-replay", amount="200.00")
        first = self._post(str(self.provider.id), payload)
        second = self._post(str(self.provider.id), payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_data = first.json()
        second_data = second.json()
        self.assertEqual(first_data["action"]["id"], second_data["action"]["id"])
        self.assertEqual(first_data["receipt"]["id"], second_data["receipt"]["id"])
        self.assertEqual(ProviderSettlementAction.objects.count(), 1)
        self.assertEqual(ProviderSettlementAllocation.objects.count(), 1)
        self.assertEqual(Receipt.objects.count(), 1)
        debt.refresh_from_db()
        self.assertEqual(debt.remaining_syp, Decimal("300.00"))

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_idempotency_conflict_through_api(self):
        self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="500.00")
        key = "phase6a:idem-conflict"
        first = self._post(str(self.provider.id), self._valid_payload(idempotency_key=key, amount="100.00"))
        self.assertEqual(first.status_code, 200)
        second = self._post(str(self.provider.id), self._valid_payload(idempotency_key=key, amount="120.00"))
        self.assertEqual(second.status_code, 400)
        self.assertEqual(second.json(), {"ok": False, "error": "idempotency key conflict"})
        self.assertEqual(ProviderSettlementAction.objects.count(), 1)
        self.assertEqual(Receipt.objects.count(), 1)

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_stale_preview_conflict_through_api(self):
        self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="1000.00")
        resp = self._post(
            str(self.provider.id),
            self._valid_payload(
                idempotency_key="phase6a:stale",
                amount="100.00",
                preview_fingerprint="stale-preview-mismatch",
            ),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"ok": False, "error": "stale preview fingerprint"})

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_invalid_payload_errors(self):
        self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="500.00")

        bad_json = self.client.post(
            self._path(str(self.provider.id)),
            data="{not-json",
            content_type="application/json",
        )
        self.assertEqual(bad_json.status_code, 400)
        self.assertEqual(bad_json.json(), {"ok": False, "error": "bad json"})

        bad_provider = self._post("not-an-id", self._valid_payload())
        self.assertEqual(bad_provider.status_code, 400)
        self.assertEqual(bad_provider.json(), {"ok": False, "error": "invalid provider id"})

        missing_provider = self._post(str(self.provider.id + 999999), self._valid_payload())
        self.assertEqual(missing_provider.status_code, 404)
        self.assertEqual(missing_provider.json(), {"ok": False, "error": "provider not found"})

        bad_action = self._post(str(self.provider.id), self._valid_payload(action="bad"))
        self.assertEqual(bad_action.status_code, 400)
        self.assertEqual(bad_action.json(), {"ok": False, "error": "invalid action"})

        bad_currency = self._post(str(self.provider.id), self._valid_payload(currency="EUR"))
        self.assertEqual(bad_currency.status_code, 400)
        self.assertEqual(bad_currency.json(), {"ok": False, "error": "invalid currency"})

        bad_precision = self._post(str(self.provider.id), self._valid_payload(amount="1.001"))
        self.assertEqual(bad_precision.status_code, 400)
        self.assertEqual(
            bad_precision.json(),
            {"ok": False, "error": "amount supports at most 2 decimal digits"},
        )

        bad_container = self._post(
            str(self.provider.id),
            self._valid_payload(money_container_id=self.hidden_container.id),
        )
        self.assertEqual(bad_container.status_code, 400)
        self.assertEqual(
            bad_container.json(),
            {"ok": False, "error": "money container is not allowed for this operation"},
        )

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_no_cross_currency_behavior(self):
        debt = self._create_central_debt(
            direction=DebtDirection.PAYABLE,
            remaining_syp="0.00",
            remaining_usd="10.00",
        )
        resp = self._post(str(self.provider.id), self._valid_payload(currency="SYP", amount="1.00"))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            resp.json(),
            {"ok": False, "error": "action/currency has zero remaining balance"},
        )
        debt.refresh_from_db()
        self.assertEqual(debt.remaining_usd, Decimal("10.00"))
        self.assertEqual(debt.remaining_syp, Decimal("0.00"))

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_api_output_matches_service_result_shape(self):
        self._create_central_debt(direction=DebtDirection.PAYABLE, remaining_syp="800.00")
        payload = self._valid_payload(idempotency_key="phase6a:shape", amount="200.00")

        expected = execute_provider_account_settlement(
            provider_id=self.provider.id,
            action=payload["action"],
            currency=payload["currency"],
            amount=payload["amount"],
            money_container_id=payload["money_container_id"],
            idempotency_key=payload["idempotency_key"],
            preview_fingerprint=payload.get("preview_fingerprint"),
            user=self.manager,
        )
        resp = self._post(str(self.provider.id), payload)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["action"]["id"], expected["action_id"])
        self.assertEqual(data["action"]["provider_id"], expected["provider_id"])
        self.assertEqual(data["action"]["action"], expected["action"])
        self.assertEqual(data["action"]["currency"], expected["currency"])
        self.assertEqual(data["action"]["requested_amount"], "200.00")
        self.assertEqual(
            data["action"]["eligible_total_remaining"],
            self._serialize_decimals(expected["eligible_total_remaining"]),
        )
        self.assertEqual(
            data["action"]["total_applied"],
            self._serialize_decimals(expected["total_applied"]),
        )
        self.assertEqual(data["action"]["allocation_count"], expected["allocation_count"])
        self.assertEqual(data["action"]["idempotency_key"], expected["idempotency_key"])
        self.assertEqual(data["action"]["preview_fingerprint"], expected["preview_fingerprint"])
        self.assertEqual(data["allocations"], self._serialize_decimals(expected["allocations"]))
        self.assertEqual(data["receipt"]["id"], expected["receipt_id"])

    @override_settings(ENABLE_PROVIDER_ACCOUNT_SETTLEMENT_EXECUTION=True)
    def test_no_provider_list_execution_endpoint_is_exposed(self):
        resp = self.client.post(
            "/manager/debts/api/providers/account-settlement-execute/",
            data=json.dumps(self._valid_payload()),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)

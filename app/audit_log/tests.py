from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from audit_log import services as AuditSV


class AuditSafeLoggingTests(TestCase):
    def test_log_create_safe_is_non_blocking_and_observable(self):
        with patch("audit_log.services.AuditLog.objects.create", side_effect=RuntimeError("audit-down")):
            with self.assertLogs("audit_log.services", level="ERROR") as cm:
                out = AuditSV.log_create_safe(
                    title="safe create",
                    message="should not raise",
                    source="audit.tests",
                )
        self.assertIsNone(out)
        self.assertTrue(any("Audit write failed" in line for line in cm.output))

    def test_log_event_safe_keeps_business_flow_when_audit_fails(self):
        with patch("audit_log.services.AuditLog.objects.create", side_effect=RuntimeError("audit-down")):
            with self.assertLogs("audit_log.services", level="ERROR"):
                out = AuditSV.log_event_safe(
                    action="info",
                    title="safe event",
                    message="flow should continue",
                    source="audit.tests",
                )
        self.assertIsNone(out)

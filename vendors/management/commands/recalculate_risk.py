import logging
import uuid

from django.core.management.base import BaseCommand
from django.db import transaction

from ai_validation.models import VendorRiskProfile
from ai_validation.services.risk_calculator import RiskCalculator
from vendors.models import Vendor

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Recalculate risk level and score for every vendor"

    def add_arguments(self, parser):
        parser.add_argument("--org", type=uuid.UUID, help="Limit to one organization id")
        parser.add_argument("--dry-run", action="store_true", help="Report changes without saving")
        parser.add_argument("--batch-size", type=int, default=100)

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        batch_size = max(options["batch_size"], 1)

        vendors = Vendor.objects.select_related("industry").order_by("id")
        if options["org"] is not None:
            vendors = vendors.filter(organization_id=options["org"])

        calculator = RiskCalculator()
        processed = changed = failed = 0

        for vendor in vendors.iterator(chunk_size=batch_size):
            try:
                if self._recalculate(calculator, vendor, dry_run):
                    changed += 1
                processed += 1
            except Exception:
                failed += 1
                logger.exception("recalculate_risk: failed for vendor %s", vendor.id)

        label = "would change" if dry_run else "changed"
        self.stdout.write(f"processed={processed} {label}={changed} failed={failed}")

    def _recalculate(self, calculator, vendor, dry_run):
        before = self._snapshot(vendor)

        with transaction.atomic():
            profile = calculator.calculate(vendor)
            after = (vendor.risk_level, profile.risk_level, profile.risk_score)
            if dry_run:
                transaction.set_rollback(True)

        return before != after

    def _snapshot(self, vendor):
        profile = VendorRiskProfile.objects.filter(vendor=vendor).first()
        if profile is None:
            return (vendor.risk_level, None, None)
        return (vendor.risk_level, profile.risk_level, profile.risk_score)

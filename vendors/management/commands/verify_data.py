from django.core.management.base import BaseCommand
from vendors.models import Vendor, Document, Industry
from ai_validation.models import DocumentValidation, VendorRiskProfile, ManualReviewQueue


class Command(BaseCommand):
    help = "Verify database data and relationships"

    def handle(self, *args, **kwargs):
        self.stdout.write("=" * 80)
        self.stdout.write("DATABASE DATA VERIFICATION")
        self.stdout.write("=" * 80)

        # Industries
        industries = Industry.objects.all()
        self.stdout.write(f"\n✓ Industries: {industries.count()}")
        for ind in industries[:5]:
            self.stdout.write(f"  - {ind.name}")

        # Vendors
        vendors = Vendor.objects.select_related('industry').all()
        self.stdout.write(f"\n✓ Vendors: {vendors.count()}")
        for vendor in vendors[:5]:
            self.stdout.write(
                f"  - {vendor.name} ({vendor.industry.name}) - {vendor.compliance_status}"
            )

        # Documents
        documents = Document.objects.select_related('vendor', 'document_type').all()
        self.stdout.write(f"\n✓ Documents: {documents.count()}")
        status_breakdown = {}
        for doc in documents:
            status_breakdown[doc.status] = status_breakdown.get(doc.status, 0) + 1
        
        for status, count in status_breakdown.items():
            self.stdout.write(f"  - {status}: {count}")

        # Validations
        validations = DocumentValidation.objects.all()
        self.stdout.write(f"\n✓ Validations: {validations.count()}")
        
        # Risk Profiles
        risk_profiles = VendorRiskProfile.objects.select_related('vendor').all()
        self.stdout.write(f"\n✓ Risk Profiles: {risk_profiles.count()}")
        for profile in risk_profiles[:5]:
            self.stdout.write(
                f"  - {profile.vendor.name}: {profile.risk_level} (score: {profile.risk_score})"
            )

        # Review Queue
        reviews = ManualReviewQueue.objects.all()
        self.stdout.write(f"\n✓ Manual Reviews: {reviews.count()}")

        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("VERIFICATION COMPLETE")
        self.stdout.write("=" * 80)
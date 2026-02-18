from django.core.management.base import BaseCommand
from decimal import Decimal
from vendors.models import Industry, DocumentType, IndustryRequiredDocument
from ai_validation.models import IndustryEmissionThreshold


class Command(BaseCommand):
    help = "Seed base configuration data for CarbonSentry"

    def handle(self, *args, **kwargs):

        self.stdout.write("=" * 80)
        self.stdout.write("CARBONSENTRY - BASE DATA SETUP")
        self.stdout.write("=" * 80)

        # ============================================================
        # STEP 1: Create Industries
        # ============================================================
        self.stdout.write("\n[1/4] Creating Industries...")

        industries_data = [
            ("Manufacturing", "Manufacturing, production facilities, factories, and industrial operations"),
            ("Technology", "Technology companies, software development, data centers, IT services"),
            ("Logistics", "Transportation, shipping, warehousing, supply chain, freight"),
            ("Energy", "Power generation, utilities, oil & gas, renewable energy"),
            ("Healthcare", "Hospitals, clinics, pharmaceutical, medical services"),
            ("Retail", "Retail, e-commerce, consumer goods distribution"),  # added
        ]

        industries = {}

        for name, description in industries_data:
            industry, created = Industry.objects.get_or_create(
                name=name,
                defaults={"description": description}
            )
            industries[name] = industry

            status = "Created" if created else "Exists"
            self.stdout.write(f"  ✓ {status}: {name}")

        self.stdout.write(f"\n  Total Industries: {Industry.objects.count()}")

        # ============================================================
        # STEP 2: Create Document Types
        # ============================================================
        self.stdout.write("\n[2/4] Creating Document Types...")

        document_types_data = [
            ("Carbon Credit Certificate", "Official carbon credit certification for offset purchases"),
            ("Emission Report", "Annual greenhouse gas emissions inventory and reporting"),
            ("Carbon Offset Certificate", "Verification of carbon offset projects and credits"),
            ("GHG Inventory Report", "Detailed greenhouse gas inventory following GHG Protocol"),
            ("Sustainability Certificate", "General sustainability and environmental compliance certification"),
            ("ISO 14064 Certificate", "ISO 14064 greenhouse gas accounting and verification standard"),
        ]

        doc_types = {}

        for name, description in document_types_data:
            doc_type, created = DocumentType.objects.get_or_create(
                name=name,
                defaults={"description": description}
            )
            doc_types[name] = doc_type

            status = "Created" if created else "Exists"
            self.stdout.write(f"  ✓ {status}: {name}")

        self.stdout.write(f"\n  Total Document Types: {DocumentType.objects.count()}")

        # ============================================================
        # STEP 3: Industry-Document Mapping
        # ============================================================
        self.stdout.write("\n[3/4] Creating Industry-Document Mappings...")

        mapping_config = {
            "Manufacturing": {
                "mandatory": ["Emission Report", "GHG Inventory Report"],
                "optional": ["ISO 14064 Certificate"]
            },
            "Technology": {
                "mandatory": ["Emission Report", "ISO 14064 Certificate"],
                "optional": ["Sustainability Certificate"]
            },
            "Logistics": {
                "mandatory": ["Emission Report", "Carbon Offset Certificate"],
                "optional": []
            },
            "Energy": {
                "mandatory": ["Emission Report", "GHG Inventory Report"],
                "optional": ["Carbon Credit Certificate"]
            },
            "Healthcare": {
                "mandatory": ["Emission Report", "ISO 14064 Certificate"],
                "optional": []
            },
            "Retail": {  # added
                "mandatory": ["Emission Report", "Sustainability Certificate"],
                "optional": ["Carbon Offset Certificate"]
            },
        }

        mapping_count = 0

        for industry_name, config in mapping_config.items():
            industry = industries[industry_name]
            self.stdout.write(f"\n  Configuring: {industry_name}")

            for doc_name in config["mandatory"]:
                _, created = IndustryRequiredDocument.objects.get_or_create(
                    industry=industry,
                    document_type=doc_types[doc_name],
                    defaults={"mandatory": True}
                )
                if created:
                    mapping_count += 1

            for doc_name in config["optional"]:
                _, created = IndustryRequiredDocument.objects.get_or_create(
                    industry=industry,
                    document_type=doc_types[doc_name],
                    defaults={"mandatory": False}
                )
                if created:
                    mapping_count += 1

        self.stdout.write(f"\n  Total Mappings Created: {mapping_count}")

        # ============================================================
        # STEP 4: Emission Thresholds
        # values in sync with constants.py DEFAULT_THRESHOLDS (tonnes CO2e)
        # format: (low, medium, high, critical)
        # ============================================================
        self.stdout.write("\n[4/4] Creating Industry Emission Thresholds...")

        thresholds_config = {
            "Manufacturing": (1000,  5000,  15000,  50000),
            "Technology":    (300,   1500,  5000,   12000),
            "Logistics":     (2000,  10000, 30000,  100000),
            "Energy":        (5000,  20000, 80000,  250000),
            "Healthcare":    (400,   2000,  7000,   20000),
            "Retail":        (300,   1500,  3000,   8000),   # added
        }

        for industry_name, values in thresholds_config.items():
            industry = industries[industry_name]
            low, medium, high, critical = values

            IndustryEmissionThreshold.objects.update_or_create(
                industry=industry,
                defaults={
                    "low_threshold":      Decimal(str(low)),
                    "medium_threshold":   Decimal(str(medium)),
                    "high_threshold":     Decimal(str(high)),
                    "critical_threshold": Decimal(str(critical)),
                }
            )

            self.stdout.write(
                f"  ✓ Threshold Configured: {industry_name} "
                f"— low={low} / medium={medium} / high={high} / critical={critical}"
            )
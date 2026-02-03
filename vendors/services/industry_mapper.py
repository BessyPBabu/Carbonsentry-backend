import logging
from django.db import transaction
from vendors.models import Industry

logger = logging.getLogger("vendors.industry_mapper")


def get_or_create_industry(raw_name: str) -> Industry:
    name = (raw_name or "").strip()

    if not name:
        raise ValueError("Industry name is required")

    try:
        industry = Industry.objects.filter(name__iexact=name).first()
        if industry:
            return industry

        with transaction.atomic():
            industry = Industry.objects.create(name=name)

        logger.info(
            "Industry created",
            extra={"industry": industry.name},
        )
        return industry

    except Exception:
        logger.exception(
            "Failed to resolve industry",
            extra={"industry": name},
        )
        raise

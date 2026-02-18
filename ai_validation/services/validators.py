import json
import re
import logging
from datetime import datetime, date, timedelta

logger = logging.getLogger(__name__)


class ResponseParser:

    @staticmethod
    def parse_json(text):
        if not text:
            logger.warning("parse_json: received empty text")
            return False, None, "Empty response"

        # direct parse
        try:
            return True, json.loads(text.strip()), None
        except json.JSONDecodeError:
            pass

        # strip markdown fences
        try:
            cleaned = re.sub(r'```(?:json)?\s*', '', text)
            cleaned = re.sub(r'```', '', cleaned).strip()
            return True, json.loads(cleaned), None
        except json.JSONDecodeError:
            pass

        # find first {...} block
        try:
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                return True, json.loads(match.group()), None
        except json.JSONDecodeError:
            pass

        # slice between first { and last }
        try:
            start = text.find('{')
            end = text.rfind('}')
            if start != -1 and end > start:
                return True, json.loads(text[start:end + 1]), None
        except json.JSONDecodeError:
            pass

        logger.error("parse_json: no valid JSON found. preview: %s", text[:300])
        return False, None, f"No valid JSON found: {text[:200]}"


class DataValidator:

    @staticmethod
    def validate_date(date_str, is_expiry=False):
        if not date_str:
            return True, None

        date_str = str(date_str).strip()

        parsed = None
        for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%m/%d/%Y', '%d/%m/%Y', '%Y/%m/%d'):
            try:
                parsed = datetime.strptime(date_str, fmt).date()
                break
            except ValueError:
                continue

        if parsed is None:
            logger.warning("validate_date: cannot parse '%s'", date_str)
            return False, f"Cannot parse date: {date_str}"

        if parsed < date(2000, 1, 1):
            logger.warning("validate_date: '%s' is before year 2000", date_str)
            return False, f"Date {date_str} too far in the past"

        if parsed > date(2050, 12, 31):
            logger.warning("validate_date: '%s' is after 2050", date_str)
            return False, f"Date {date_str} unrealistically far in the future"

        # expiry dates are in the future — that is expected and valid
        if not is_expiry:
            if parsed > date.today() + timedelta(days=30):
                logger.warning("validate_date: issue date '%s' is in the future", date_str)
                return False, f"Issue date {date_str} is in the future"

        return True, parsed

    @staticmethod
    def validate_co2_value(value):
        if value is None:
            return True, None

        try:
            value_float = float(value)
        except (TypeError, ValueError) as e:
            logger.warning("validate_co2_value: cannot convert '%s' — %s", value, e)
            return False, f"Invalid CO2 value '{value}': {e}"

        if value_float < 0:
            logger.warning("validate_co2_value: negative value %s", value_float)
            return False, "CO2 value cannot be negative"

        if value_float > 10_000_000_000:
            logger.warning("validate_co2_value: unrealistically high %s", value_float)
            return False, "CO2 value unrealistically high"

        return True, value_float

    @staticmethod
    def normalize_unit(unit):
        if not unit:
            return 'tonnes'

        u = str(unit).lower().strip()

        if any(k in u for k in ('kg', 'kilogram')):
            return 'kg'
        if any(k in u for k in ('ton', 'tonne', 'metric')):
            return 'tonnes'

        logger.debug("normalize_unit: unrecognised '%s', defaulting to tonnes", unit)
        return 'tonnes'
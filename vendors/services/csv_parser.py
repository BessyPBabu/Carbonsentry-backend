import csv
import logging
from typing import Dict, Iterator, Tuple

logger = logging.getLogger("vendors.csv_parser")


REQUIRED_COLUMNS = {
    "name",
    "contact_email",
    "industry",
    "country",
}


class CsvParsingError(Exception):
    pass


def parse_csv(file) -> Iterator[Tuple[int, Dict[str, str]]]:

    logger.info("Starting CSV parsing")

    try:
        content = file.read().decode("utf-8").splitlines()
    except UnicodeDecodeError:
        logger.error("CSV decoding failed: not UTF-8 encoded")
        raise CsvParsingError("CSV file must be UTF-8 encoded")

    if not content:
        logger.error("CSV file is empty")
        raise CsvParsingError("CSV file is empty")

    reader = csv.DictReader(content)

    if not reader.fieldnames:
        logger.error("CSV has no headers")
        raise CsvParsingError("CSV file must contain headers")

    headers = {h.strip().lower() for h in reader.fieldnames}

    logger.debug(
        "CSV headers detected",
        extra={"headers": sorted(headers)},
    )

    missing_columns = REQUIRED_COLUMNS - headers
    if missing_columns:
        logger.error(
            "CSV missing required columns",
            extra={"missing_columns": sorted(missing_columns)},
        )
        raise CsvParsingError(
            f"Missing required columns: {', '.join(sorted(missing_columns))}"
        )

    logger.info("CSV header validation successful")

    for index, row in enumerate(reader, start=2):
        normalized_row = {}

        for key, value in row.items():
            if key is None:
                continue

            normalized_key = key.strip().lower()
            normalized_value = value.strip() if value else ""

            normalized_row[normalized_key] = normalized_value

        if not any(normalized_row.values()):
            logger.debug(
                "Skipping empty CSV row",
                extra={"row_number": index},
            )
            continue

        yield index, normalized_row

    logger.info("CSV parsing completed successfully")

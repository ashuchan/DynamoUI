"""AWS DynamoDB adapter (Phase 5).

Connection-test + scaffold paths only — query / mutation execution is
tracked as future work. The boto3 SDK is imported lazily so it isn't a
mandatory runtime dependency.
"""
from backend.adapters.dynamodb.adapter import (
    DynamoDBAdapter,
    DynamoDBConnectionTester,
    make_dynamodb_tester,
)

__all__ = ["DynamoDBAdapter", "DynamoDBConnectionTester", "make_dynamodb_tester"]

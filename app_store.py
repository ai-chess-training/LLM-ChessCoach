from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from token_utils import decode_hs256_jwt


class AppStoreVerificationError(ValueError):
    """Raised when App Store signed data cannot be verified."""


@dataclass
class VerifiedAppStoreTransaction:
    transaction_id: str
    original_transaction_id: Optional[str]
    product_id: str
    bundle_id: str
    environment: str
    signed_transaction_info: str
    revocation_date: Optional[Any] = None


@dataclass
class VerifiedAppStoreNotification:
    notification_type: str
    subtype: Optional[str]
    transaction: Optional[VerifiedAppStoreTransaction]


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "development") == "production"


def _expected_bundle_id() -> str:
    bundle_id = os.getenv("APPLE_BUNDLE_ID", "").strip()
    if bundle_id:
        return bundle_id
    if _is_production():
        raise AppStoreVerificationError("APPLE_BUNDLE_ID is not configured")
    return "com.llmchesscoach.dev"


def _expected_product_id() -> str:
    product_id = os.getenv("APPSTORE_PRODUCT_ID_30_GAMES", "").strip()
    if product_id:
        return product_id
    if _is_production():
        raise AppStoreVerificationError("APPSTORE_PRODUCT_ID_30_GAMES is not configured")
    return "com.llmchesscoach.games30"


def _get_attr(value: Any, *names: str) -> Any:
    current = value
    for name in names:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(name)
        else:
            current = getattr(current, name, None)
    return current


def _load_root_certificates() -> list[bytes]:
    paths = [entry.strip() for entry in os.getenv("APPSTORE_ROOT_CERT_PATHS", "").split(",") if entry.strip()]
    if not paths:
        raise AppStoreVerificationError("APPSTORE_ROOT_CERT_PATHS is not configured")
    certificates = []
    for entry in paths:
        path = Path(entry)
        if not path.exists():
            raise AppStoreVerificationError(f"Missing App Store root certificate: {entry}")
        certificates.append(path.read_bytes())
    return certificates


def _normalize_transaction(payload: Any, signed_transaction_info: str) -> VerifiedAppStoreTransaction:
    transaction_id = _get_attr(payload, "transactionId") or _get_attr(payload, "transaction_id")
    product_id = _get_attr(payload, "productId") or _get_attr(payload, "product_id")
    bundle_id = _get_attr(payload, "bundleId") or _get_attr(payload, "bundle_id")
    environment = _get_attr(payload, "environment")
    original_transaction_id = _get_attr(payload, "originalTransactionId") or _get_attr(payload, "original_transaction_id")
    revocation_date = _get_attr(payload, "revocationDate") or _get_attr(payload, "revocation_date")

    if not transaction_id:
        raise AppStoreVerificationError("App Store transaction is missing transactionId")
    if product_id != _expected_product_id():
        raise AppStoreVerificationError("Unexpected App Store productId")
    if bundle_id != _expected_bundle_id():
        raise AppStoreVerificationError("Unexpected App Store bundleId")

    return VerifiedAppStoreTransaction(
        transaction_id=str(transaction_id),
        original_transaction_id=str(original_transaction_id) if original_transaction_id else None,
        product_id=str(product_id),
        bundle_id=str(bundle_id),
        environment=str(environment or "UNKNOWN"),
        signed_transaction_info=signed_transaction_info,
        revocation_date=revocation_date,
    )


def verify_signed_transaction(signed_transaction_info: str) -> VerifiedAppStoreTransaction:
    if not signed_transaction_info:
        raise AppStoreVerificationError("Missing signed transaction info")

    test_secret = os.getenv("APPSTORE_TEST_SHARED_SECRET", "").strip()
    if test_secret and not _is_production():
        payload = decode_hs256_jwt(signed_transaction_info, test_secret)
        return _normalize_transaction(payload, signed_transaction_info)

    try:
        from appstoreserverlibrary.models.Environment import Environment
        from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier, VerificationException
    except ImportError as exc:  # pragma: no cover - exercised only when optional dependency is absent
        raise AppStoreVerificationError("app-store-server-library is required for production App Store verification") from exc

    root_certificates = _load_root_certificates()
    bundle_id = _expected_bundle_id()
    app_apple_id = os.getenv("APPLE_APPLE_ID", "").strip() or None
    environments = []
    if app_apple_id:
        environments.append((Environment.PRODUCTION, app_apple_id))
    environments.append((Environment.SANDBOX, None))

    last_error: Optional[Exception] = None
    for environment, current_app_apple_id in environments:
        try:
            verifier = SignedDataVerifier(
                root_certificates,
                True,
                environment,
                bundle_id,
                current_app_apple_id,
            )
            payload = verifier.verify_and_decode_signed_transaction(signed_transaction_info)
            return _normalize_transaction(payload, signed_transaction_info)
        except VerificationException as exc:
            last_error = exc
            continue

    raise AppStoreVerificationError("App Store transaction verification failed") from last_error


def verify_notification(signed_payload: str) -> VerifiedAppStoreNotification:
    if not signed_payload:
        raise AppStoreVerificationError("Missing signed notification payload")

    test_secret = os.getenv("APPSTORE_TEST_SHARED_SECRET", "").strip()
    if test_secret and not _is_production():
        payload = decode_hs256_jwt(signed_payload, test_secret)
        notification_type = _get_attr(payload, "notificationType") or "UNKNOWN"
        subtype = _get_attr(payload, "subtype")
        signed_transaction_info = _get_attr(payload, "data", "signedTransactionInfo") or _get_attr(payload, "signedTransactionInfo")
        transaction = verify_signed_transaction(signed_transaction_info) if signed_transaction_info else None
        return VerifiedAppStoreNotification(str(notification_type), str(subtype) if subtype else None, transaction)

    try:
        from appstoreserverlibrary.models.Environment import Environment
        from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier, VerificationException
    except ImportError as exc:  # pragma: no cover - exercised only when optional dependency is absent
        raise AppStoreVerificationError("app-store-server-library is required for production App Store verification") from exc

    root_certificates = _load_root_certificates()
    bundle_id = _expected_bundle_id()
    app_apple_id = os.getenv("APPLE_APPLE_ID", "").strip() or None
    environments = []
    if app_apple_id:
        environments.append((Environment.PRODUCTION, app_apple_id))
    environments.append((Environment.SANDBOX, None))

    last_error: Optional[Exception] = None
    for environment, current_app_apple_id in environments:
        try:
            verifier = SignedDataVerifier(
                root_certificates,
                True,
                environment,
                bundle_id,
                current_app_apple_id,
            )
            payload = verifier.verify_and_decode_notification(signed_payload)
            notification_type = _get_attr(payload, "notificationType") or "UNKNOWN"
            subtype = _get_attr(payload, "subtype")
            signed_transaction_info = _get_attr(payload, "data", "signedTransactionInfo")
            transaction = verify_signed_transaction(signed_transaction_info) if signed_transaction_info else None
            return VerifiedAppStoreNotification(str(notification_type), str(subtype) if subtype else None, transaction)
        except VerificationException as exc:
            last_error = exc
            continue

    raise AppStoreVerificationError("App Store notification verification failed") from last_error

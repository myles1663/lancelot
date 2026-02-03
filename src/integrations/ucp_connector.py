"""UCP (Universal Commerce Protocol) connector for agentic commerce operations.

Implements discovery, product search, and transaction handling per the UCP
specification (ucp.dev). All outbound URLs are validated through NetworkInterceptor
for SSRF protection.
"""
import json
import uuid
import time
import datetime
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError
from urllib.parse import urljoin

from security import NetworkInterceptor, AuditLogger


class UCPConnector:
    """Handles UCP discovery, product search, and transaction initiation."""

    DISCOVERY_ENDPOINT = "/.well-known/ucp.json"

    def __init__(self, audit_logger: Optional[AuditLogger] = None):
        self._net_interceptor = NetworkInterceptor()
        self._audit_logger = audit_logger or AuditLogger()
        self._registered_merchants = {}  # domain -> manifest
        self._pending_transactions = {}  # transaction_id -> transaction info

    def discover_merchant(self, merchant_url: str) -> dict:
        """Discovers UCP capabilities from a merchant's well-known endpoint.

        Args:
            merchant_url: Base URL of the merchant (e.g., 'https://shop.example.com')

        Returns:
            UCP manifest dict with merchant capabilities, endpoints, and supported actions.

        Raises:
            ValueError: If the URL is blocked by security policy.
            ConnectionError: If the merchant is unreachable.
        """
        # Security: validate URL against allowlist and SSRF protections
        if not self._net_interceptor.check_url(merchant_url):
            raise ValueError(f"URL blocked by security policy: {merchant_url}")

        discovery_url = urljoin(merchant_url.rstrip("/") + "/", ".well-known/ucp.json")

        self._audit_logger.log_event(
            "UCP_DISCOVERY",
            f"Discovering merchant: {merchant_url}"
        )

        try:
            req = Request(discovery_url, headers={
                "User-Agent": "Lancelot-UCP/1.0",
                "Accept": "application/json",
            })
            with urlopen(req, timeout=10) as response:
                manifest = json.loads(response.read().decode("utf-8"))

            # Cache the manifest
            self._registered_merchants[merchant_url] = manifest

            self._audit_logger.log_event(
                "UCP_DISCOVERY_SUCCESS",
                f"Discovered merchant: {manifest.get('name', 'Unknown')} at {merchant_url}"
            )

            return manifest

        except (URLError, json.JSONDecodeError, Exception) as e:
            self._audit_logger.log_event(
                "UCP_DISCOVERY_FAILED",
                f"Failed to discover {merchant_url}: {e}"
            )
            raise ConnectionError(f"Failed to discover UCP merchant: {e}")

    def search_products(self, merchant_url: str, query: str) -> list:
        """Searches products via a UCP-enabled merchant.

        Args:
            merchant_url: Base URL of the merchant.
            query: Search query string.

        Returns:
            List of product dicts with id, name, price, description, etc.
        """
        manifest = self._registered_merchants.get(merchant_url)
        if not manifest:
            manifest = self.discover_merchant(merchant_url)

        search_endpoint = manifest.get("endpoints", {}).get("search")
        if not search_endpoint:
            raise ValueError(f"Merchant {merchant_url} does not support product search")

        search_url = urljoin(merchant_url.rstrip("/") + "/", search_endpoint.lstrip("/"))

        # Security check on the resolved search URL
        if not self._net_interceptor.check_url(search_url):
            raise ValueError(f"Search URL blocked by security policy: {search_url}")

        self._audit_logger.log_event(
            "UCP_SEARCH",
            f"Searching '{query}' at {merchant_url}"
        )

        try:
            # Build search request with query parameter
            search_url_with_query = f"{search_url}?q={query}"
            req = Request(search_url_with_query, headers={
                "User-Agent": "Lancelot-UCP/1.0",
                "Accept": "application/json",
            })
            with urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))

            products = data.get("products", data.get("results", []))
            return products

        except (URLError, json.JSONDecodeError, Exception) as e:
            self._audit_logger.log_event(
                "UCP_SEARCH_FAILED",
                f"Search failed at {merchant_url}: {e}"
            )
            return []

    def initiate_transaction(self, merchant_url: str, product_id: str, params: dict) -> dict:
        """Initiates a commerce transaction.

        Creates a pending transaction that must be confirmed via confirm_transaction().
        The gateway layer should check MCP Sentry approval before calling this.

        Args:
            merchant_url: Base URL of the merchant.
            product_id: Product identifier.
            params: Transaction parameters (quantity, shipping address, etc.)

        Returns:
            Transaction info dict with transaction_id, status, and details.
        """
        manifest = self._registered_merchants.get(merchant_url)
        if not manifest:
            manifest = self.discover_merchant(merchant_url)

        transaction_id = str(uuid.uuid4())

        transaction = {
            "transaction_id": transaction_id,
            "merchant_url": merchant_url,
            "merchant_name": manifest.get("name", "Unknown"),
            "product_id": product_id,
            "params": params,
            "status": "pending_confirmation",
            "created_at": datetime.datetime.utcnow().isoformat(),
        }

        self._pending_transactions[transaction_id] = transaction

        self._audit_logger.log_event(
            "UCP_TRANSACTION_INITIATED",
            f"Transaction {transaction_id} for product {product_id} at {merchant_url}"
        )

        return transaction

    def confirm_transaction(self, transaction_id: str) -> dict:
        """Confirms and executes a previously initiated transaction.

        Args:
            transaction_id: The transaction ID from initiate_transaction().

        Returns:
            Updated transaction info with execution result.
        """
        transaction = self._pending_transactions.get(transaction_id)
        if not transaction:
            raise ValueError(f"Transaction {transaction_id} not found")

        if transaction["status"] != "pending_confirmation":
            raise ValueError(f"Transaction {transaction_id} is not pending (status: {transaction['status']})")

        merchant_url = transaction["merchant_url"]
        manifest = self._registered_merchants.get(merchant_url)

        if not manifest:
            raise ValueError(f"Merchant manifest not found for {merchant_url}")

        transact_endpoint = manifest.get("endpoints", {}).get("transact")
        if not transact_endpoint:
            raise ValueError(f"Merchant {merchant_url} does not support transactions")

        transact_url = urljoin(merchant_url.rstrip("/") + "/", transact_endpoint.lstrip("/"))

        # Security check
        if not self._net_interceptor.check_url(transact_url):
            raise ValueError(f"Transaction URL blocked by security policy: {transact_url}")

        self._audit_logger.log_event(
            "UCP_TRANSACTION_CONFIRMING",
            f"Confirming transaction {transaction_id}"
        )

        try:
            payload = json.dumps({
                "product_id": transaction["product_id"],
                "params": transaction["params"],
                "transaction_id": transaction_id,
            }).encode("utf-8")

            req = Request(transact_url, data=payload, headers={
                "User-Agent": "Lancelot-UCP/1.0",
                "Content-Type": "application/json",
                "Accept": "application/json",
            })
            with urlopen(req, timeout=15) as response:
                result = json.loads(response.read().decode("utf-8"))

            transaction["status"] = "completed"
            transaction["result"] = result
            transaction["completed_at"] = datetime.datetime.utcnow().isoformat()

            self._audit_logger.log_event(
                "UCP_TRANSACTION_COMPLETED",
                f"Transaction {transaction_id} completed successfully"
            )

            return transaction

        except (URLError, Exception) as e:
            transaction["status"] = "failed"
            transaction["error"] = str(e)

            self._audit_logger.log_event(
                "UCP_TRANSACTION_FAILED",
                f"Transaction {transaction_id} failed: {e}"
            )

            return transaction

    def get_transaction(self, transaction_id: str) -> Optional[dict]:
        """Retrieves transaction info by ID."""
        return self._pending_transactions.get(transaction_id)

    def list_merchants(self) -> list:
        """Returns a list of discovered merchant URLs and names."""
        return [
            {"url": url, "name": manifest.get("name", "Unknown")}
            for url, manifest in self._registered_merchants.items()
        ]

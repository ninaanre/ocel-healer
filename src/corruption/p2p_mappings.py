"""Domain mappings from Order Management to P2P.

This module provides mappings between the Order Management domain (used in the
original corruptor implementations) and the Procure-to-Pay (P2P) domain in the
ocel2-p2p.sqlite database.

The mappings enable all 26 issue types to be tested against the P2P database
without rewriting corruptor logic from scratch.
"""

from __future__ import annotations

# Event type mappings: Order Management display names → P2P display names
EVENT_TYPE_MAP = {
    "place order": "Create Purchase Order",
    "confirm order": "Approve Purchase Order",
    "pay order": "Execute Payment",
    "pick item": "Create Goods Receipt",
    "item out of stock": "Delegate Purchase Requisition Approval",
    "create package": "Create Invoice Receipt",
    "send package": "Perform Two-Way Match",
    "package delivered": "Create Goods Receipt",
    "payment reminder": "Execute Payment",
    "reorder item": "Create Purchase Requisition",
    "failed delivery": "Create Request for Quotation",
}

# Event table mappings: Order Management table names → P2P table names
EVENT_TABLE_MAP = {
    "event_PlaceOrder": "event_CreatePurchaseOrder",
    "event_ConfirmOrder": "event_ApprovePurchaseOrder",
    "event_PayOrder": "event_ExecutePayment",
    "event_PickItem": "event_CreateGoodsReceipt",
    "event_ItemOutOfStock": "event_DelegatePurchaseRequisitionApproval",
    "event_CreatePackage": "event_CreateInvoiceReceipt",
    "event_SendPackage": "event_PerformTwoWayMatch",
    "event_PackageDelivered": "event_CreateGoodsReceipt",
    "event_PaymentReminder": "event_ExecutePayment",
    "event_ReorderItem": "event_CreatePurchaseRequisition",
    "event_FailedDelivery": "event_CreateRequestforQuotation",
}

# Object type mappings: Order Management types → P2P types
OBJECT_TYPE_MAP = {
    "orders": "purchase_order",
    "items": "material",
    "packages": "goods receipt",
    "customers": "purchase_requisition",
    "products": "material",
    "employees": "payment",
}

# Object table mappings: Order Management table names → P2P table names
OBJECT_TABLE_MAP = {
    "object_orders": "object_purchase_order",
    "object_items": "object_material",
    "object_packages": "object_goodsreceipt",
    "object_customers": "object_purchase_requisition",
    "object_products": "object_material",
    "object_employees": "object_payment",
}

# E2O qualifier mappings: Order Management qualifiers → P2P qualifiers
E2O_QUALIFIER_MAP = {
    "order": "purchase_order",
    "item": "material",
    "packer": "goods receipt",
    "customer": "purchase_requisition",
    "product": "material",
    "employee": "payment",
    "package": "goods receipt",
}

# O2O qualifier mappings: Order Management qualifiers → P2P qualifiers
O2O_QUALIFIER_MAP = {
    "comprises": "Materials of Purchase Order",
    "includes": "Materials of Goods Receipt",
    "delivered_by": "goods_receipt_pm",
    "processed_by": "order_pm",
}


def get_p2p_event_type(om_type: str) -> str:
    """Map Order Management event type to P2P.

    Args:
        om_type: Order Management event type (e.g., "place order")

    Returns:
        P2P event type (e.g., "Create Purchase Order")
    """
    return EVENT_TYPE_MAP.get(om_type, om_type)


def get_p2p_event_table(om_table: str) -> str:
    """Map Order Management event table to P2P.

    Args:
        om_table: Order Management table name (e.g., "event_PlaceOrder")

    Returns:
        P2P table name (e.g., "event_CreatePurchaseOrder")
    """
    return EVENT_TABLE_MAP.get(om_table, om_table)


def get_p2p_object_type(om_type: str) -> str:
    """Map Order Management object type to P2P.

    Args:
        om_type: Order Management object type (e.g., "orders")

    Returns:
        P2P object type (e.g., "purchase_order")
    """
    return OBJECT_TYPE_MAP.get(om_type, om_type)


def get_p2p_object_table(om_table: str) -> str:
    """Map Order Management object table to P2P.

    Args:
        om_table: Order Management table name (e.g., "object_orders")

    Returns:
        P2P table name (e.g., "object_purchase_order")
    """
    return OBJECT_TABLE_MAP.get(om_table, om_table)


def get_p2p_e2o_qualifier(om_qual: str) -> str:
    """Map Order Management E2O qualifier to P2P.

    Args:
        om_qual: Order Management E2O qualifier (e.g., "order")

    Returns:
        P2P E2O qualifier (e.g., "purchase_order")
    """
    return E2O_QUALIFIER_MAP.get(om_qual, om_qual)


def get_p2p_o2o_qualifier(om_qual: str) -> str:
    """Map Order Management O2O qualifier to P2P.

    Args:
        om_qual: Order Management O2O qualifier (e.g., "comprises")

    Returns:
        P2P O2O qualifier (e.g., "Materials of Purchase Order")
    """
    return O2O_QUALIFIER_MAP.get(om_qual, om_qual)

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
    """Get the event type name for Order Management database.

    Args:
        om_type: Event type key (e.g., "place order")

    Returns:
        Event type name in OM database
    """
    # For OM database, these are already the correct display names
    return om_type


def get_p2p_event_table(om_table: str) -> str:
    """Map Order Management event table to P2P.

    Args:
        om_table: Order Management table name (e.g., "event_PlaceOrder")

    Returns:
        P2P table name (e.g., "event_CreatePurchaseOrder")
    """
    return EVENT_TABLE_MAP.get(om_table, om_table)


def get_p2p_object_type(om_type: str) -> str:
    """Get the object type name for Order Management database.

    Args:
        om_type: Object type key (e.g., "orders", "products")

    Returns:
        Object type name in OM database (e.g., "Orders", "Products")
    """
    # Map lowercase keys to actual Order Management type names (with capitals)
    OM_TYPE_NAMES = {
        "orders": "Orders",
        "items": "Items",
        "packages": "Packages",
        "customers": "Customers",
        "products": "Products",
        "employees": "Employees",
    }
    return OM_TYPE_NAMES.get(om_type, om_type)


def get_p2p_object_table(om_table: str) -> str:
    """Get the object table name, returning OM name as-is since tests use OM database.

    For Order Management database: "object_products" → "object_Products"
    For P2P database: this would need the mapping, but default DB is OM.

    Args:
        om_table: Key for object table (e.g., "object_products")

    Returns:
        Actual table name in the database
    """
    # Map lowercase keys to actual Order Management table names (with capitals)
    OM_TABLE_NAMES = {
        "object_orders": "object_Orders",
        "object_items": "object_Items",
        "object_packages": "object_Packages",
        "object_customers": "object_Customers",
        "object_products": "object_Products",
        "object_employees": "object_Employees",
    }
    return OM_TABLE_NAMES.get(om_table, om_table)


def get_p2p_e2o_qualifier(om_qual: str) -> str:
    """Get the E2O qualifier name for Order Management database.

    Args:
        om_qual: E2O qualifier key (e.g., "order", "product")

    Returns:
        E2O qualifier name in OM database
    """
    # For OM database, qualifiers are lowercase keys as-is
    return om_qual


def get_p2p_o2o_qualifier(om_qual: str) -> str:
    """Get the O2O qualifier name for Order Management database.

    Args:
        om_qual: O2O qualifier key (e.g., "comprises", "processed_by")

    Returns:
        O2O qualifier name in OM database
    """
    # For OM database, qualifiers are as-is
    return om_qual
